"""Opt-in, bounded arXiv abstract screening of proposed Future Work directions.

This is not a novelty search or proof that a research problem has been solved.
Only a small keyword query goes to arXiv. The configured model sees the proposed
directions and returned public abstracts; no local PDF is uploaded by this module.
The caller persists the returned audit record and owns job/cancellation handling.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import inspect
from pathlib import Path
import re
from typing import Literal

from pydantic import Field

from .discovery import DiscoveryService, arxiv_identifier
from .intelligence import StrictModel


PROMPT_VERSION = "future-verification-v1"
MAX_QUERIES = 3
RESULTS_PER_QUERY = 5
MAX_ABSTRACT_CHARS = 12000
_STOP_WORDS = set("""
a an the of in on for to and or with by from as at is are this that using via
based research study paper future work proposed proposal direction directions
explore exploring investigate investigating improve improving new novel further
can could may will would should we our you your all need needs approach approaches
method methods framework system performance more better also such these those
into under over its their be been being it not do does how what which than then
evaluate evaluation extend extension develop development test testing towards
limitations limitation experiments experimental application applications
""".split())


class AbstractCitation(StrictModel):
    source_id: str = Field(min_length=1, max_length=100)
    quote: str = Field(min_length=12, max_length=6000)


class DirectionAssessment(StrictModel):
    direction_id: str = Field(pattern=r"^direction-\d+$")
    status: Literal["potentially_addressed", "not_found_in_search", "evidence_insufficient"]
    coverage: Literal["explicit_direction_coverage", "related_only", "unclear"]
    citations: list[AbstractCitation] = Field(default_factory=list, max_length=8)


class VerificationPayload(StrictModel):
    directions: list[DirectionAssessment] = Field(min_length=1, max_length=12)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _normalized(text: str) -> str:
    return " ".join(text.split())


def _keywords(text: str) -> list[str]:
    result = []
    for word in re.findall(r"[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*", text):
        word = word.casefold()
        if 2 <= len(word) <= 40 and word not in _STOP_WORDS and word not in result:
            result.append(word)
    return result


def _candidates(synthesis: dict) -> list[dict]:
    result = []
    for index, section in enumerate(synthesis.get("sections", [])):
        if not isinstance(section, dict):
            continue
        proposed = section.get("proposed_work", "")
        if not isinstance(proposed, str) or not proposed.strip():
            continue
        result.append({"direction_id": f"direction-{index + 1}", "section_index": index,
                       "heading": str(section.get("heading", "")), "proposed_work": proposed,
                       "query_ids": []})
    return result


def _query_plan(candidates: list[dict], synthesis: dict) -> list[dict]:
    # Local document titles may be filenames, so discard obvious file extensions.
    topic_words = _keywords(" ".join(str(item.get("title", ""))
                                    for item in synthesis.get("references", [])
                                    if isinstance(item, dict)))
    topic_words = [word for word in topic_words if word not in {"pdf", "docx", "txt"}]
    plans = []
    for candidate in candidates:
        heading = _keywords(candidate["heading"])
        proposal = _keywords(candidate["proposed_work"])
        own_words = list(dict.fromkeys(heading[:2] + proposal[:3]))
        words = own_words[:3] + [word for word in topic_words if word not in own_words][:1]
        if not own_words:
            words = topic_words[:3]
        if not words:
            candidate["query_limitation"] = "no_usable_english_keywords"
            continue
        candidate["query_strategy"] = "direction_and_topic_keywords" if own_words else "topic_only"
        query = " ".join(words[:4])
        existing = next((plan for plan in plans if plan["query"] == query), None)
        if existing:
            existing["direction_ids"].append(candidate["direction_id"])
            candidate["query_ids"].append(existing["query_id"])
            continue
        if len(plans) == MAX_QUERIES:
            candidate["query_limitation"] = "query_budget_exhausted"
            continue
        plan = {"query_id": f"query-{len(plans) + 1}", "query": query,
                "direction_ids": [candidate["direction_id"]], "field": "all",
                "limit": RESULTS_PER_QUERY, "sort_by": "submittedDate",
                "strategy": candidate["query_strategy"],
                "status": "pending", "source_ids": []}
        plans.append(plan)
        candidate["query_ids"].append(plan["query_id"])
    return plans


def _source(record: dict, retrieved_at: str) -> dict | None:
    if not isinstance(record, dict) or record.get("source") != "arxiv":
        return None
    source_id = record.get("id")
    url = record.get("source_url")
    abstract = record.get("summary")
    if not all(isinstance(value, str) and value for value in (source_id, url, abstract)):
        return None
    identifier = arxiv_identifier(source_id)
    if not identifier or source_id != f"arxiv:{identifier}" or arxiv_identifier(url) != identifier:
        return None
    # Identity and the URL must both come from the same returned arXiv record.
    # Never accept a model-generated URL or synthesize a missing publication date.
    if not url.startswith(("https://arxiv.org/abs/", "http://arxiv.org/abs/")):
        return None
    return {"source_id": source_id, "arxiv_id": identifier,
            "title": str(record.get("title", "")), "url": url, "source": "arxiv",
            "published": record.get("published") or None,
            "updated": record.get("updated") or None,
            "retrieved_at": record.get("retrieved_at") or retrieved_at,
            "abstract": abstract[:MAX_ABSTRACT_CHARS],
            "abstract_truncated": len(abstract) > MAX_ABSTRACT_CHARS,
            "full_text_reviewed": False}


def _valid_quote(quote: str, abstract: str) -> bool:
    """Check an exact, whitespace-normalized complete sentence/excerpt boundary.

    This is a membership check, not semantic entailment verification. Keeping
    case and punctuation prevents the model from silently paraphrasing a quote.
    """
    quote, abstract = _normalized(quote), _normalized(abstract)
    if len(quote) < 12:
        return False
    offset = 0
    while (index := abstract.find(quote, offset)) != -1:
        before, after = abstract[:index].rstrip(), abstract[index + len(quote):]
        start_ok = not before or before[-1] in ".!?。！？"
        end_ok = not after or (quote[-1] in ".!?。！？" and after[0].isspace())
        if start_ok and end_ok:
            return True
        offset = index + 1
    return False


def _insufficient(candidate: dict, reason: str) -> dict:
    return {**candidate, "status": "evidence_insufficient", "coverage": "unclear",
            "citations": [], "reason": reason, "requires_full_text_confirmation": True,
            "excluded": False}


def _checked(candidate: dict, assessment: DirectionAssessment, sources: dict[str, dict],
             allowed_ids: set[str]) -> dict:
    citations = []
    for citation in assessment.citations:
        if (citation.source_id not in allowed_ids or citation.source_id not in sources
                or not _valid_quote(citation.quote, sources[citation.source_id]["abstract"])):
            return _insufficient(candidate, "模型引文未通过真实来源及摘要原句校验；保留该方向，需人工核验。")
        source = sources[citation.source_id]
        citations.append({"source_id": citation.source_id, "quote": citation.quote,
                          "url": source["url"], "published": source["published"]})
    if assessment.status != "evidence_insufficient" and not citations:
        return _insufficient(candidate, "模型判定缺少有效的公开摘要原句；不能据此排除或确认该方向。")
    if assessment.status == "potentially_addressed":
        if assessment.coverage != "explicit_direction_coverage":
            return _insufficient(candidate, "摘要仅显示相关性或覆盖关系不明确，不能据此排除该方向。")
        reason = "摘要显示该方向可能已有研究明确覆盖，暂从重点候选中移出；是否真正解决仍须阅读全文确认。"
        excluded = True
    elif assessment.status == "not_found_in_search":
        if assessment.coverage != "related_only":
            return _insufficient(candidate, "模型判定与覆盖关系不一致；未作出研究空白或已解决结论。")
        reason = "在本次有限检索的摘要中未找到明确覆盖该方向的证据；这不能证明研究空白或新颖性。"
        excluded = False
    else:
        reason = "现有摘要不足以判断该方向是否已被覆盖；保留该方向，需扩展检索并阅读全文。"
        excluded = False
    return {**candidate, "status": assessment.status, "coverage": assessment.coverage,
            "citations": citations, "reason": reason, "excluded": excluded,
            "requires_full_text_confirmation": True}


async def _check_cancel(callback) -> None:
    if callback is not None:
        result = callback()
        if inspect.isawaitable(result):
            await result


async def verify_future_work(library, provider, synthesis: dict, *, discovery=None,
                             check_cancel=None) -> dict:
    """Return a copy of a Future Work synthesis with auditable abstract screening.

    At most three distinct arXiv queries run, five results each, newest submission
    first. Existing DiscoveryService caching/throttling applies; cached timestamps
    are preserved. A successful run makes one bounded provider.generate call.
    All retrieval/model failures retain the original directions with an explicit
    insufficient-evidence status. check_cancel may be sync or async; it is called
    outside failure-degradation handlers before each search and model call, so
    task cancellation exceptions propagate without starting the next operation.
    """
    result = deepcopy(synthesis)
    if result.get("kind") != "future_work":
        raise ValueError("公开研究核验仅适用于Future Work结果。")
    candidates = _candidates(result)
    plans = _query_plan(candidates, result)
    sources: dict[str, dict] = {}
    errors: list[dict] = []
    directions: dict[str, dict] = {}
    started_at = _now()
    model_called = False
    reason = "没有可核验的研究设想；未发起检索或模型调用。"
    if candidates and not getattr(provider, "configured", False):
        reason = "尚未配置大模型服务；未发起外部检索或核验调用。"
        errors.append({"code": "model_not_configured", "message": reason})
    elif candidates and plans:
        discovery = discovery if discovery is not None else DiscoveryService(library)
        for plan in plans:
            await _check_cancel(check_cancel)
            plan["attempted_at"] = _now()
            try:
                response = await asyncio.to_thread(discovery.search, plan["query"], field="all",
                                                   limit=RESULTS_PER_QUERY, sort_by="submittedDate")
                if inspect.isawaitable(response):
                    response = await response
                if not isinstance(response, dict) or not isinstance(response.get("items"), list):
                    raise ValueError("Invalid discovery response")
                retrieved_at = response.get("retrieved_at")
                if not isinstance(retrieved_at, str) or not retrieved_at:
                    raise ValueError("Missing retrieval timestamp")
                if response.get("query") != plan["query"]:
                    raise ValueError("Discovery query mismatch")
                plan.update(status="completed", retrieved_at=retrieved_at,
                            cached=bool(response.get("cached", False)),
                            total_results=response.get("total_results"),
                            next_network_refresh_at=response.get("next_network_refresh_at"))
                for record in response["items"][:RESULTS_PER_QUERY]:
                    source = _source(record, retrieved_at)
                    if source is None:
                        plan["invalid_source_count"] = plan.get("invalid_source_count", 0) + 1
                        continue
                    source_id = source["source_id"]
                    sources.setdefault(source_id, source)
                    if source_id not in plan["source_ids"]:
                        plan["source_ids"].append(source_id)
            except Exception:
                # Transport errors can contain URLs, credentials, or response
                # bodies. Persist only this fixed error code and safe message.
                plan.update(status="failed", error="search_failed")
                errors.append({"code": "search_failed", "query_id": plan["query_id"],
                               "message": "公开论文检索失败或返回记录无效；该查询未形成可靠证据。"})
        if any(plan["status"] == "failed" for plan in plans):
            reason = "本次公开检索未完整成功；未据此排除研究方向，也不能判断研究空白。"
        else:
            eligible = []
            for candidate in candidates:
                associated = [plan for plan in plans if plan["query_id"] in candidate["query_ids"]]
                allowed = set(source_id for plan in associated for source_id in plan["source_ids"])
                if not associated:
                    directions[candidate["direction_id"]] = _insufficient(
                        candidate, "无可用英文检索关键词或达到本次三条查询上限；该方向尚未核验。")
                elif not allowed:
                    directions[candidate["direction_id"]] = _insufficient(
                        candidate, "检索未返回可引用的公开摘要；无结果不能证明研究空白。")
                else:
                    eligible.append({**candidate, "allowed_source_ids": sorted(allowed)})
            if eligible:
                await _check_cancel(check_cancel)
                try:
                    prompt = Path(__file__).with_name("prompts").joinpath("future_verification_v1.txt").read_text(encoding="utf-8")
                    model_called = True
                    generated = await provider.generate(prompt, {
                        "task": "screen_future_work_against_public_abstracts",
                        "directions": eligible, "sources": list(sources.values()),
                        "queries": plans, "scope": "bounded_arxiv_abstracts_only",
                        "full_text_reviewed": False,
                    }, VerificationPayload)
                    payload = VerificationPayload.model_validate(
                        generated.model_dump() if isinstance(generated, VerificationPayload) else generated)
                    expected_ids = {item["direction_id"] for item in eligible}
                    actual_ids = [item.direction_id for item in payload.directions]
                    if len(actual_ids) != len(set(actual_ids)) or set(actual_ids) != expected_ids:
                        raise ValueError("Direction IDs mismatch")
                    assessments = {item.direction_id: item for item in payload.directions}
                    for candidate in eligible:
                        clean_candidate = {key: value for key, value in candidate.items() if key != "allowed_source_ids"}
                        directions[candidate["direction_id"]] = _checked(
                            clean_candidate, assessments[candidate["direction_id"]], sources,
                            set(candidate["allowed_source_ids"]))
                except Exception:
                    reason = "模型核验失败或输出结构无效；保留候选方向，未认定其已被解决。"
                    errors.append({"code": "verification_failed", "message": reason})
            else:
                reason = "没有可供模型核验的公开摘要证据；未确认任何研究空白。"
    elif candidates:
        reason = "未提取到可用英文检索关键词；未发起外部检索，需补充明确术语。"
    for plan in plans:
        if plan["status"] == "pending":
            plan.update(status="not_attempted", reason=reason)
    ordered = [directions.get(candidate["direction_id"], _insufficient(candidate, reason)) for candidate in candidates]
    reviewed = sum(item["status"] != "evidence_insufficient" for item in ordered)
    status = "completed" if ordered and reviewed == len(ordered) else "partial" if reviewed else "evidence_insufficient"
    audit = {"status": status, "prompt_version": PROMPT_VERSION, "started_at": started_at,
             "completed_at": _now(), "scope": "bounded_arxiv_abstracts_only",
             "query_limit": MAX_QUERIES, "results_per_query": RESULTS_PER_QUERY,
             "query_generation": "deterministic_english_keywords_from_headings_proposals_and_titles",
             "sort_by": "submittedDate", "date_filter": None,
             "queries": plans, "sources": list(sources.values()), "directions": ordered,
             "errors": errors, "model_called": model_called,
             "validation": "source_membership_and_exact_abstract_quote; classification_is_model_judgment",
             "full_text_reviewed": False, "novelty_proven": False,
             "notice": "只核验有限arXiv返回摘要；按提交时间排序不代表覆盖全部最新研究，排除项仍需全文确认。"}
    result["future_verification"] = audit
    result["future_work_verification"] = "bounded_abstract_review" if reviewed else "evidence_insufficient"
    result["retained_directions"] = [item for item in ordered if not item["excluded"]]
    result["excluded_directions"] = [item for item in ordered if item["excluded"]]
    result["search_time_range"] = {"from": None, "to": None, "date_filter": None,
                                   "checked_at": audit["completed_at"],
                                   "note": "未设置发表日期区间；真实检索/缓存时间见每条query.retrieved_at。"}
    # Preserve the initial synthesis trace and sections, while retiring the old
    # blanket claim that no external search has happened after an actual attempt.
    limitations = list(result.get("limitations", []))
    if any(plan["status"] in {"completed", "failed"} for plan in plans):
        old = "未进行外部最新文献检索；是否已解决及新颖性均为证据不足，需后续研究核验。"
        limitations = [item for item in limitations if item != old]
    limitations += [audit["notice"], "关键词采用规则提取，不保证覆盖同义表达；topic_only查询仅来自文献标题，关键词不足的方向未核验。",
                    "摘要引文校验仅证明来源归属与文字匹配，不自动证明模型判定正确，也不构成研究空白证明。"]
    result["limitations"] = list(dict.fromkeys(limitations))
    result["trace"] = list(result.get("trace", [])) + [
        {"step": "public_research_search", "detail": f"计划{len(plans)}条查询，实际成功{sum(p['status'] == 'completed' for p in plans)}条；返回{len(sources)}条可引用arXiv摘要。"},
        {"step": "future_work_screening", "detail": f"{len(ordered)}个方向中{reviewed}个形成有限摘要判定；其余证据不足。未作出已解决或新颖性证明。"},
    ]
    return result
