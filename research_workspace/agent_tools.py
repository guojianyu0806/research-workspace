"""Bounded tool adapters for an authenticated, explicitly scoped agent task.

The runner persists its call journal BEFORE execute and state AFTER execute.
This module never chooses an account, accepts paths, or retries unknown writes.
Paper/page text and tool results are data, not instructions for the executor.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import inspect
import json
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .discovery import DiscoveryError, DiscoveryService, arxiv_identifier
from .graph import GraphService
from .intelligence import Evidence
from .library import LibraryError

MAX_EVIDENCE = 48
MAX_EVIDENCE_CHARS = 96000
Identifier = Annotated[str, Field(min_length=1, max_length=150)]
DocumentIds = Annotated[list[Identifier], Field(min_length=1, max_length=20)]


class ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SearchEvidence(ToolInput):
    query: str = Field(min_length=1, max_length=4000)
    document_ids: DocumentIds
    limit: int = Field(default=8, ge=1, le=12)


class BuildGraph(ToolInput):
    document_ids: DocumentIds


class SearchArxiv(ToolInput):
    query: str = Field(min_length=1, max_length=300)
    limit: int = Field(default=5, ge=1, le=10)


class EmptyInput(ToolInput):
    pass


class ImportPaper(ToolInput):
    paper_id: Identifier


class PrepareParse(ToolInput):
    document_id: Identifier


def _error(code, *, retryable=False):
    return {"status": "error", "code": code, "retryable": retryable}


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError("non-finite JSON constant")


def decode_arguments(arguments):
    return json.loads(arguments, object_pairs_hook=_pairs, parse_constant=_reject_constant)


class AgentTools:
    """Service is already bound to the authenticated Library; scope is trusted.

    Additional persisted state: parse_runs, public_papers, external_attempts,
    external_results, import_attempts. Successful network results are reused.
    Unknown outcomes require runner reconciliation, never automatic replay.
    """

    _models = {
        "search_evidence": SearchEvidence, "build_citation_graph": BuildGraph,
        "search_arxiv": SearchArxiv, "check_arxiv_updates": EmptyInput,
        "import_paper": ImportPaper, "prepare_parse": PrepareParse,
    }
    _descriptions = {
        "search_evidence": "检索本任务授权文献的固定解析版本，返回可定位的原文证据，不调用查询扩展模型。",
        "build_citation_graph": "从授权文献固定版本的参考书目构建局域引用图谱。PageRank 阻尼为0.85，非学界总体影响力。",
        "search_arxiv": "仅用用户授权的原始公开检索词查询 arXiv。不得改写成私有论文片段；返回元数据不等于全文证据。",
        "check_arxiv_updates": "检查用户明确选中的订阅；不能选择、创建或修改其他订阅。",
        "import_paper": "导入本任务已发现的公开论文，受本任务导入配额限制；不接受任意URL。",
        "prepare_parse": "查询本任务已导入PDF的既有解析任务；未完成时返回waiting，不重复导入或创建新解析版本。",
    }

    def __init__(self, service, *, scope: dict, state: dict, check_cancel):
        self.service, self.library = service, service.library
        self.scope, self.state, self.check_cancel = scope, state, check_cancel
        self._lock = asyncio.Lock()
        for key, default in {"run_ids": {}, "discovered_ids": [], "imported": {}, "evidence": [],
                             "graphs": [], "parse_runs": {}, "public_papers": [],
                             "external_attempts": {}, "external_results": {}, "import_attempts": {}}.items():
            state.setdefault(key, default)

    def _allowed_names(self):
        names = {"search_evidence", "build_citation_graph"}
        if self.scope.get("allow_arxiv") is True:
            query = self.scope.get("public_query")
            if isinstance(query, str) and 1 <= len(query.strip()) <= 300:
                names.add("search_arxiv")
            if isinstance(self.scope.get("subscription_id"), str) and self.scope["subscription_id"]:
                names.add("check_arxiv_updates")
            if self.scope.get("import_limit") == 1 and not isinstance(self.scope.get("import_limit"), bool):
                names.update({"import_paper", "prepare_parse"})
        return names

    def schemas(self) -> list[dict]:
        return [{"type": "function", "function": {"name": name, "description": self._descriptions[name],
                 "parameters": model.model_json_schema()}}
                for name, model in self._models.items() if name in self._allowed_names()]

    async def _cancel(self):
        if self.check_cancel is not None:
            result = self.check_cancel()
            if inspect.isawaitable(result):
                await result

    async def execute(self, name: str, arguments: str, *, recover_import: bool = False) -> dict:
        if not isinstance(name, str) or name not in self._allowed_names():
            return _error("tool_not_allowed")
        try:
            if not isinstance(arguments, str) or len(arguments) > 16000:
                return _error("invalid_arguments")
            values = decode_arguments(arguments)
            args = self._models[name].model_validate(values)
        except (ValidationError, ValueError, TypeError, RecursionError):
            return _error("invalid_arguments")
        async with self._lock:
            await self._cancel()
            try:
                if (recover_import and name == "import_paper"
                        and args.paper_id in self.state["discovered_ids"]
                        and args.paper_id not in self.state["imported"]):
                    self.state["import_attempts"][args.paper_id] = "unknown"
                return await getattr(self, "_" + name)(args)
            except KeyError:
                return _error("resource_unavailable")
            except (LibraryError, ValidationError):
                return _error("source_validation_failed")

    def _documents(self, identifiers, *, require_run=True):
        selected = list(dict.fromkeys(identifiers))
        allowed = set(self.scope.get("document_ids", [])) | {
            value["document_id"] for value in self.state["imported"].values()}
        if not set(selected) <= allowed:
            return None, _error("document_out_of_scope")
        result = []
        for identifier in selected:
            doc = self.library.get_document(identifier)
            if doc.get("archived_at"):
                return None, _error("document_unavailable")
            if identifier not in self.state["run_ids"]:
                return None, _error("snapshot_missing")
            run_id = self.state["run_ids"][identifier]
            if require_run and not run_id:
                return None, _error("parse_not_ready")
            if run_id:
                run = self.library.get_run(run_id)
                if run["document_id"] != identifier or run["status"] not in {"completed", "partial"}:
                    return None, _error("snapshot_invalid")
            result.append(doc)
        return result, None

    def _retain_evidence(self, values, *, graph=False):
        retained = list(self.state["evidence"])
        identities = {item["evidence_id"] for item in retained}
        size = sum(len(item["text"]) for item in retained)
        # A large reference graph should leave room for later full-text retrieval.
        limit = MAX_EVIDENCE // 4 if graph else MAX_EVIDENCE
        char_limit = MAX_EVIDENCE_CHARS // 4 if graph else MAX_EVIDENCE_CHARS
        returned, omitted = [], 0
        for value in values:
            raw = value.model_dump() if isinstance(value, Evidence) else value
            identifier = raw.get("document_id")
            if self.state["run_ids"].get(identifier) != raw.get("run_id"):
                continue
            _, problem = self._documents([identifier])
            if problem:
                continue
            canonical = self.service._canonical_block(raw)
            item = canonical.model_dump()
            if item["evidence_id"] not in identities:
                if len(retained) >= limit or size + len(item["text"]) > char_limit:
                    omitted += 1
                    continue
                retained.append(item); identities.add(item["evidence_id"]); size += len(item["text"])
            returned.append(item)
        self.state["evidence"] = retained
        return returned, omitted

    async def _search_evidence(self, args):
        documents, problem = self._documents(args.document_ids)
        if problem:
            return problem
        ids = [doc["id"] for doc in documents]
        found = await self.service.retrieve_for_question(args.query, ids, limit=args.limit, expansion="off",
                    run_ids={identifier:self.state["run_ids"][identifier] for identifier in ids},
                    check_cancel=self.check_cancel)
        evidence, omitted = self._retain_evidence(found["evidence"][:args.limit])
        if omitted and not evidence:
            return {**_error("evidence_budget_reached"), "evidence": [], "omitted_due_to_budget": omitted}
        return {"status": "ok", "code": "evidence_found" if evidence else "no_evidence", "evidence": evidence,
                "selected_runs": {identifier:self.state["run_ids"][identifier] for identifier in ids},
                "evidence_limit": MAX_EVIDENCE, "omitted_due_to_budget": omitted,
                "source_rule": "canonical_original_blocks",
                "retrieval": {"mode":found.get("mode", "unknown"),
                    "semantic":{key:found.get("semantic", {}).get(key) for key in ("status", "fallback", "error")
                                if key in found.get("semantic", {})},
                    "expansion": {key:found.get("expansion", {}).get(key) for key in ("policy", "status", "calls")
                                  if key in found.get("expansion", {})}}}

    async def _build_citation_graph(self, args):
        documents, problem = self._documents(args.document_ids)
        if problem:
            return problem
        ids = [doc["id"] for doc in documents]
        result = await asyncio.to_thread(GraphService(self.library).build, ids,
                         run_ids={identifier:self.state["run_ids"][identifier] for identifier in ids})
        edges, omitted = [], 0
        for edge in result["edges"][:100]:
            retained, skipped = self._retain_evidence(edge["evidence"], graph=True)
            omitted += skipped
            edges.append({key:edge[key] for key in ("source", "target", "match_method")} |
                         {"evidence_ids": [item["evidence_id"] for item in retained]})
        graph = {"nodes": [{key:node[key] for key in ("id", "document_id", "run_id", "title", "kind", "pagerank", "rank")}
                           for node in result["nodes"][:100]], "edges": edges,
                 "method": result["method"], "unresolved_count": result["unresolved_count"], "network_used": False,
                 "truncated": len(result["nodes"]) > 100 or len(result["edges"]) > 100,
                 "evidence_omitted_due_to_budget": omitted}
        self.state["graphs"] = (self.state["graphs"] + [graph])[-4:]
        return {"status": "ok", "code": "graph_built", "graph": graph}

    def _discovery(self):
        return DiscoveryService(self.library)

    def _public_papers(self, papers):
        output = []
        for paper in papers[:30]:
            identifier = arxiv_identifier(paper.get("arxiv_id", ""))
            if not identifier or paper.get("id") != "arxiv:" + identifier:
                continue
            item = {key:paper.get(key) for key in ("id", "arxiv_id", "published", "updated", "retrieved_at")}
            item.update(title=str(paper.get("title", ""))[:500], summary=str(paper.get("summary", ""))[:2000],
                        source_url="https://arxiv.org/abs/" + identifier,
                        pdf_url="https://arxiv.org/pdf/" + identifier if paper.get("pdf_url") else None,
                        evidence_scope="public_metadata_only")
            output.append(item)
        prior = {item["id"]:item for item in self.state["public_papers"]}
        for item in output:
            if item["id"] in prior or len(prior) < 50:
                prior[item["id"]] = item
        self.state["public_papers"] = list(prior.values())
        self.state["discovered_ids"] = list(prior)
        return [item for item in output if item["id"] in prior]

    async def _external(self, name, arguments, work, finish, *, safe_retry=False):
        key = hashlib.sha256(json.dumps([name, arguments], sort_keys=True).encode()).hexdigest()
        attempt = self.state["external_attempts"].get(key)
        if attempt:
            if attempt["status"] == "completed":
                return self.state["external_results"][key]
            if attempt["status"] in {"started", "unknown"}:
                return _error("operation_outcome_unknown")
            if not safe_retry or attempt["attempts"] >= 2:
                return _error("external_retry_exhausted")
        if len(self.state["external_attempts"]) >= 16 and attempt is None:
            return _error("external_call_limit")
        self.state["external_attempts"][key] = {"status":"started", "attempts":(attempt or {}).get("attempts",0)+1}
        try:
            value = await asyncio.to_thread(work)
        except DiscoveryError:
            self.state["external_attempts"][key]["status"] = "failed" if safe_retry else "unknown"
            return _error("public_search_failed" if safe_retry else "operation_outcome_unknown",
                          retryable=safe_retry and self.state["external_attempts"][key]["attempts"] < 2)
        except asyncio.CancelledError:
            self.state["external_attempts"][key]["status"] = "unknown"
            raise
        except Exception:
            self.state["external_attempts"][key]["status"] = "unknown"
            return _error("operation_outcome_unknown")
        result = finish(value)
        self.state["external_attempts"][key]["status"] = "completed"
        self.state["external_results"][key] = result
        return result

    async def _search_arxiv(self, args):
        public_query = self.scope["public_query"].strip()
        if " ".join(args.query.split()).casefold() != " ".join(public_query.split()).casefold():
            return _error("public_query_not_authorized")
        kwargs = {"limit": args.limit, "force_refresh": self.scope.get("refresh_search") is True,
                  "sort_by": "submittedDate"}
        # Freeze the requested date window once so resume/retry cannot shift it.
        days = self.scope.get("research_days")
        if isinstance(days, int) and not isinstance(days, bool) and 1 <= days <= 3650 and not arxiv_identifier(public_query):
            if "public_search_window" not in self.state:
                end = datetime.now(timezone.utc)
                self.state["public_search_window"] = {"published_from":(end-timedelta(days=days)).isoformat(),
                                                      "published_to":end.isoformat()}
            kwargs.update(self.state["public_search_window"])
        discovery = self._discovery()
        return await self._external("search_arxiv", {"query":public_query, **kwargs},
            lambda: discovery.search(public_query, **kwargs),
            lambda result:{"status":"ok", "code":"public_papers_found", "papers":self._public_papers(result["items"]),
                           "cached":bool(result.get("cached")), "retrieved_at":result.get("retrieved_at"),
                           "metadata_only":True}, safe_retry=True)

    async def _check_arxiv_updates(self, args):
        discovery = self._discovery()
        identifier = self.scope["subscription_id"]
        if not any(item["id"] == identifier for item in discovery.list_subscriptions()):
            return _error("subscription_unavailable")
        def finish(result):
            if result.get("error"):
                return _error("subscription_check_incomplete")
            return {"status":"ok", "code":"subscription_checked", "papers":self._public_papers(result["items"]),
                    "new_paper_ids":[item["id"] for item in result.get("new_items",[]) if item["id"] in self.state["discovered_ids"]],
                    "metadata_only":True, "cached":bool(result.get("cached")), "retrieved_at":result.get("retrieved_at")}
        return await self._external("check_arxiv_updates", {"subscription_id":identifier},
                                    lambda: discovery.check_subscription(identifier), finish)

    def _record_import(self, paper_id, document):
        if document.get("archived_at"):
            return _error("document_unavailable")
        identifier = document["id"]
        pinned = self.state["run_ids"].get(identifier)
        run = self.library.get_run(pinned) if pinned else document.get("available_run") or document.get("current_run")
        if pinned and (run['document_id'] != identifier or run['status'] not in {'completed', 'partial'}):
            return _error("snapshot_invalid")
        self.state["imported"][paper_id] = {"document_id":identifier,"sha256":document["sha256"]}
        self.state["parse_runs"][identifier] = run["id"] if run else None
        self.state["run_ids"][identifier] = run["id"] if pinned or document.get("available_run") else None
        self.state["import_attempts"][paper_id] = "completed"
        return {"status":"ok", "code":"paper_imported", "document_id":identifier,"sha256":document["sha256"],
                "parse_status":run["status"] if run else "missing", "run_id":run["id"] if run else None}

    async def _import_paper(self, args):
        paper_id = args.paper_id
        if paper_id not in self.state["discovered_ids"] or not arxiv_identifier(paper_id):
            return _error("paper_not_discovered_in_task")
        existing = self.state["imported"].get(paper_id)
        if existing:
            doc = self.library.get_document(existing["document_id"])
            if doc.get("archived_at"):
                return _error("document_unavailable")
            if doc["sha256"] != existing["sha256"]:
                return _error("source_validation_failed")
            return {"status":"ok", "code":"paper_imported", **existing, "duplicate":True,
                    "run_id":self.state["parse_runs"].get(doc["id"])}
        attempts = self.state["import_attempts"]
        if attempts.get(paper_id) in {"started", "unknown"}:
            # A completed discovery mapping can reconcile a lost return without another download.
            paper = self._discovery().get_paper(paper_id)
            if paper.get("document_id"):
                return self._record_import(paper_id, self.library.get_document(paper["document_id"]))
            return _error("operation_outcome_unknown")
        if len(attempts) >= self.scope["import_limit"] or len(self.state["imported"]) >= self.scope["import_limit"]:
            return _error("import_quota_exhausted")
        attempts[paper_id] = "started"
        discovery = self._discovery()
        try:
            result = await asyncio.to_thread(discovery.import_paper, paper_id)
        except asyncio.CancelledError:
            attempts[paper_id] = "unknown"
            raise
        except Exception:
            attempts[paper_id] = "unknown"
            return _error("operation_outcome_unknown")
        return self._record_import(paper_id, result["document"])

    async def _prepare_parse(self, args):
        imported = next((value for value in self.state["imported"].values() if value["document_id"] == args.document_id), None)
        if not imported:
            return _error("document_not_imported_in_task")
        doc = self.library.get_document(args.document_id)
        if doc["sha256"] != imported["sha256"] or doc.get("archived_at"):
            return _error("document_unavailable")
        run_id = self.state["parse_runs"].get(args.document_id)
        if not run_id:
            return _error("parse_run_missing")
        run = self.library.get_run(run_id)
        if run["document_id"] != args.document_id:
            return _error("snapshot_invalid")
        identity = {"document_id":args.document_id, "run_id":run_id}
        if run["status"] in {"queued", "running"}:
            return {"status":"waiting", "code":"parse_pending", **identity}
        if run["status"] in {"completed", "partial"} and run.get("text_pages", 0) > 0:
            self.state["run_ids"][args.document_id] = run_id
            return {"status":"ok", "code":"parse_ready", **identity, "parse_status":run["status"]}
        return {**_error("parse_unavailable"), **identity, "parse_status":run["status"]}
