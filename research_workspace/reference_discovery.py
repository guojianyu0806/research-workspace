"""Local, reversible reference ranking and explicitly selected public lookup.

Ranking never uses the network or edits a reference. Lookup sends only identifiers
or a heuristic title from selected numbered bibliography entries, never citation
contexts or the parent paper. DiscoveryService owns its shared throttle/cache.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import re
import unicodedata

from .discovery import ARXIV_API, DiscoveryError, DiscoveryService, arxiv_identifier, tokens
from .library import LibraryError

CONTEXT_RADIUS = 240
MAX_SELECTED = 100
RESULTS_PER_REFERENCE = 3
METHOD_WORDS = re.compile(r"\b(?:methods?|methodology|experiments?|experimental|evaluation|ablation|baselines?|benchmarks?)\b|方法|实验|评估|消融|基线", re.I)
DOI = re.compile(r"10\.\d{4,9}/[^\s<>\"]+", re.I)
ARXIV_IN_TEXT = re.compile(r"(?:arxiv\s*:\s*|(?:https?://)?(?:export\.)?arxiv\.org/(?:abs|pdf)/)((?:\d{4}\.\d{4,5}|[a-z][a-z.-]*/\d{7})(?:v[1-9]\d*)?)", re.I)
LIMITATIONS = [
    "仅使用本次解析版本识别到的数字型书目与引用，可能漏检；作者年份型引用尚未可靠覆盖。",
    "排序表达当前查询和本篇正文中的使用线索，不评价论文的客观学术价值。",
    "筛选只影响结果视图，不删除原书目；被筛选条目仍可恢复查看和显式选择检索。",
]


class ReferenceDiscoveryError(DiscoveryError):
    """Recoverable input or public reference lookup error."""


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _doi(value):
    if not isinstance(value, str):
        return None
    value = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi\s*:\s*)", "", value.strip(), flags=re.I)
    value = value.rstrip(".,;")
    while value.endswith(")") and value.count(")") > value.count("("):
        value = value[:-1]
    return value.casefold() if DOI.fullmatch(value) else None


def _canonical_arxiv(value):
    identifier = arxiv_identifier(value) if isinstance(value, str) else None
    return re.sub(r"v\d+$", "", identifier).casefold() if identifier else None


def _title_key(value):
    return " ".join(re.findall(r"\w+", unicodedata.normalize("NFKC", value).casefold()))


def _title_candidate(text):
    """Keep an explicitly quoted title or a plausible sentence, never a whole entry."""
    text = re.sub(r"^\s*(?:\[\d+\]|\d+\.)\s*", "", text)
    candidates = [(match[1], "quoted_title_heuristic") for match in re.finditer(r'["“]([^"”]{12,300})["”]', text)]
    candidates += [(part, "sentence_title_heuristic") for part in re.split(r"(?<=[.!?])\s+", text)]
    for candidate, method in candidates:
        candidate = candidate.strip(" .;,:\"“”")
        if (not 12 <= len(candidate) <= 300 or len(tokens(candidate)) < 3
                or re.search(r"https?://|\b(?:arxiv|doi)\s*:|10\.\d{4,9}/", candidate, re.I)
                or re.match(r"(?:in\b|proceedings\b|journal\b|vol\b|pp\b|pages\b|\d{4}\b)", candidate, re.I)):
            continue
        if method != "quoted_title_heuristic":
            words = re.findall(r"[A-Za-z]+", candidate)
            author_list = " and " in candidate and all(word == "and" or word[0].isupper() for word in words)
            if candidate.count(",") > 1 or re.search(r"\b[A-Z]\.", candidate) or author_list:
                continue
        return candidate, method
    return None, None


def _parse_reference(reference):
    text = reference["text"]
    match = ARXIV_IN_TEXT.search(text)
    arxiv = arxiv_identifier(match[1]) if match else arxiv_identifier(reference.get("arxiv_id") or "")
    doi_match = DOI.search(text)
    doi = _doi(reference.get("doi")) or (_doi(doi_match[0]) if doi_match else None)
    title, title_method = _title_candidate(text)
    query, kind = (arxiv, "arxiv_id") if arxiv else (doi, "doi") if doi else (title, "title_candidate")
    if query and kind == "title_candidate":
        query = " ".join(query.split()[:20])[:240].strip()
    return {"arxiv_id": arxiv, "doi": doi, "title_candidate": title, "title_method": title_method,
            "query": query, "query_type": kind if query else None,
            "query_origin": "selected_numbered_bibliography_only"}


def _candidate(paper, parsed):
    """Identifier agreement is distinct from title resemblance and exact version."""
    raw_identifier, raw_url = paper.get("arxiv_id"), paper.get("source_url")
    if not isinstance(raw_identifier, str) or not isinstance(raw_url, str):
        return None
    identifier = arxiv_identifier(raw_identifier)
    if (paper.get("source") != "arxiv" or not identifier or paper.get("id") != f"arxiv:{identifier}"
            or arxiv_identifier(raw_url) != identifier):
        return None
    methods, conflicts, reasons = [], [], []
    local_arxiv, found_arxiv = _canonical_arxiv(parsed["arxiv_id"]), _canonical_arxiv(identifier)
    found_doi = _doi(paper.get("doi"))
    if local_arxiv:
        (methods if local_arxiv == found_arxiv else conflicts).append("arxiv_id")
    if parsed["doi"] and found_doi:
        (methods if parsed["doi"] == found_doi else conflicts).append("doi")
    title = parsed["title_candidate"] or ""
    local_terms, found_terms = tokens(title), tokens(str(paper.get("title", "")))
    union = local_terms | found_terms
    overlap = local_terms & found_terms
    similarity = len(overlap) / len(union) if union else 0.0
    exact_title = bool(title and _title_key(title) == _title_key(str(paper.get("title", ""))))
    if conflicts:
        status = "identifier_conflict"
        reasons.append({"code": "identifier_conflict", "fields": conflicts, "detail": "书目与返回元数据中的标识存在冲突，不能按同一论文处理。"})
    elif methods:
        status = "identifier_match"
        reasons.append({"code": "identifier_agreement", "fields": methods, "detail": "本地书目与官方返回元数据的标识一致；仍需确认目标及论文版本。"})
    elif exact_title or (similarity >= 0.6 and len(overlap) >= 3):
        status = "title_candidate"
        reasons.append({"code": "title_similarity_only", "detail": "标题文字相似，仅列为待确认候选，不认定为同一论文。"})
    else:
        status = "unconfirmed"
        reasons.append({"code": "search_return_only", "detail": "公开检索返回了此记录，但现有标识与标题不足以确认身份。"})
    version_match = None
    if local_arxiv and local_arxiv == found_arxiv:
        local_version = re.search(r"v(\d+)$", parsed["arxiv_id"])
        found_version = re.search(r"v(\d+)$", identifier)
        version_match = "unspecified" if not local_version or not found_version else "same" if local_version[1] == found_version[1] else "different"
        if version_match == "different":
            reasons.append({"code": "different_arxiv_version", "detail": "arXiv论文编号相同，但版本号不同，不可替代原版本证据。"})
    return {"paper": deepcopy(paper), "match_status": status, "matched_identifiers": methods,
            "conflicting_identifiers": conflicts, "title_similarity": round(similarity, 6),
            "matched_title_terms": sorted(overlap), "arxiv_version_match": version_match,
            "requires_user_confirmation": True, "reasons": reasons}


class ReferenceDiscovery:
    def __init__(self, library, discovery=None):
        self.library = library
        self.discovery = discovery  # Lazy: even constructing a ranker creates no discovery DB.

    def _structure(self, document_id, run_id):
        structure = self.library.get_structure(document_id, run_id=run_id)
        # Structures originate from Library's numbered-reference extractor.
        references = [deepcopy(reference) for reference in structure.get("references", [])
                      if re.fullmatch(r"\d{1,4}", str(reference.get("label", ""))) and reference.get("id")
                      and reference.get("text") and reference.get("evidence")]
        return structure, references

    def rank_references(self, document_id, query, run_id=None, include_filtered=False):
        if not isinstance(query, str) or len(query) > 500:
            raise ReferenceDiscoveryError("筛选查询须为不超过500个字符的文本。")
        if not isinstance(include_filtered, bool):
            raise ReferenceDiscoveryError("include_filtered须为布尔值。")
        query = " ".join(query.split())
        terms = tokens(query)
        if query and not terms:
            raise ReferenceDiscoveryError("筛选查询未包含可用关键词；留空可查看全部书目。")
        structure, references = self._structure(document_id, run_id)
        pinned_run = structure["run_id"]
        citations = {citation["id"]: citation for citation in structure.get("citations", [])}
        pages = {}

        def context(citation):
            anchor = deepcopy(citation["evidence"])
            page = anchor["page"]
            if page not in pages:
                try:
                    pages[page] = self.library.get_page(document_id, page, run_id=pinned_run)
                except (KeyError, LibraryError):
                    pages[page] = None
            record = pages[page]
            block = next((item for item in record.get("blocks", []) if item["id"] == anchor["block_id"]), None) if record else None
            result = {"citation_id": citation["id"], "marker": citation.get("marker"), "citation_evidence": anchor,
                      "status": "unavailable", "context_evidence": None, "matched_terms": [], "method_cues": []}
            if block:
                start, end = anchor.get("char_start"), anchor.get("char_end")
                if (isinstance(start, int) and isinstance(end, int) and 0 <= start < end <= len(block["text"])
                        and block["text"][start:end] == anchor.get("text")):
                    left, right = max(0, start - CONTEXT_RADIUS), min(len(block["text"]), end + CONTEXT_RADIUS)
                    text = block["text"][left:right]
                    result.update(status="available", context_evidence={**anchor, "text": text, "char_start": left, "char_end": right},
                                  matched_terms=sorted(terms & tokens(text)), method_cues=sorted({match[0].casefold() for match in METHOD_WORDS.finditer(text)}))
            if result["status"] == "unavailable":
                result["reason"] = "未能核对引用所在原文块或字符位置；保留引用锚点，但不计上下文信号。"
            return result

        ranked = []
        for ordinal, reference in enumerate(references):
            linked = [citation for citation in citations.values() if reference["id"] in citation.get("reference_ids", [])]
            contexts = [context(citation) for citation in linked]
            bibliographic_terms = terms & tokens(reference["text"])
            context_terms = set().union(*(set(item["matched_terms"]) for item in contexts)) if contexts else set()
            method_count = sum(bool(item["method_cues"]) for item in contexts)
            signals = {"bibliography_keywords": {"points": 3 * len(bibliographic_terms), "matched_terms": sorted(bibliographic_terms)},
                       "context_keywords": {"points": 2 * len(context_terms), "matched_terms": sorted(context_terms)},
                       "citation_occurrences": {"points": min(len(linked), 5), "count": len(linked), "cap": 5},
                       "method_experiment_context": {"points": 2 * min(method_count, 3), "count": method_count, "cap": 3}}
            included = not terms or bool(bibliographic_terms | context_terms)
            reasons = [
                {"code": "bibliography_keywords", "detail": "书目文字每命中一个不同查询词计3分；作者和出版项也可能贡献命中。", **signals["bibliography_keywords"]},
                {"code": "context_keywords", "detail": "全部引用邻近上下文每命中一个不同查询词计2分，重复词不累加。", **signals["context_keywords"]},
                {"code": "citation_occurrences", "detail": "本篇正文每处数字引用计1分，最多5分；次数不代表学术价值。", **signals["citation_occurrences"]},
                {"code": "method_experiment_context", "detail": "引用前后各240字符内出现方法、实验等词的每处上下文计2分，最多6分；仅为词语线索。", **signals["method_experiment_context"]},
                {"code": "query_match" if included else "no_query_match", "detail": "查询为空，保留全部书目。" if not terms else "书目或引用上下文命中查询词，保留展示。" if included else "书目及已核对上下文均未命中查询词，暂在本查询视图中筛出；可恢复，不表示论文无价值。"},
            ]
            ranked.append({"reference_id": reference["id"], "document_id": document_id, "run_id": pinned_run,
                           "reference": reference, "label": reference["label"], "text": reference["text"],
                           "included": included, "score": sum(item["points"] for item in signals.values()),
                           "signals": signals, "contexts": contexts, "reasons": reasons, "original_order": ordinal})
        ranked.sort(key=lambda item: (-item["score"], item["original_order"]))
        included = [item for item in ranked if item["included"]]
        filtered = [item for item in ranked if not item["included"]]
        return {"document_id": document_id, "run_id": pinned_run, "query": query, "query_terms": sorted(terms),
                "included": included, "filtered": filtered, "items": ranked if include_filtered else included,
                "include_filtered": include_filtered, "total_references": len(references), "scoring_version": "reference-ranking-v1",
                "processing_status": structure.get("processing_status"),
                "filtering": {"persisted": False, "reversible": True, "academic_value_assessed": False},
                "limitations": list(LIMITATIONS)}

    def search_selected(self, document_id, reference_ids, max_searches=5, run_id=None):
        if (not isinstance(reference_ids, list) or not 1 <= len(reference_ids) <= MAX_SELECTED
                or any(not isinstance(identifier, str) or not identifier for identifier in reference_ids)):
            raise ReferenceDiscoveryError(f"请选择1至{MAX_SELECTED}个原参考书目ID。")
        if isinstance(max_searches, bool) or not isinstance(max_searches, int) or not 1 <= max_searches <= 10:
            raise ReferenceDiscoveryError("检索预算须为1至10次，默认5次。")
        structure, references = self._structure(document_id, run_id)
        by_id = {reference["id"]: reference for reference in references}
        selected = list(dict.fromkeys(reference_ids))
        if any(identifier not in by_id for identifier in selected):
            raise ReferenceDiscoveryError("所选书目ID不属于该文档的指定解析版本。请刷新后重新选择。")
        results, cache, requests_used = [], {}, 0
        for identifier in selected:
            reference = by_id[identifier]
            parsed = _parse_reference(reference)
            row = {"reference_id": identifier, "reference": reference, "document_id": document_id, "run_id": structure["run_id"],
                   "parsed": parsed, "query": parsed["query"], "status": "skipped_no_query", "attempted_at": None,
                   "retrieved_at": None, "cached": None, "reused_query": False, "source": None, "source_url": None,
                   "candidates": [], "error": None, "requires_user_confirmation": True}
            results.append(row)
            if not parsed["query"]:
                row["reason"] = "未从所选书目可靠提取标识或标题片段，未发送整条正文；请人工核对书目。"
                continue
            key = parsed["query"].casefold()
            if key not in cache and requests_used >= max_searches:
                row.update(status="skipped_budget", reason="本次明确检索预算已用完；该书目未发起外部请求。")
                continue
            if key in cache:
                found = cache[key]
                row["reused_query"] = True
            else:
                requests_used += 1
                attempted = _now()
                try:
                    if self.discovery is None:
                        self.discovery = DiscoveryService(self.library)
                    response = self.discovery.search_references([parsed["query"]], limit_per_reference=RESULTS_PER_REFERENCE)
                    found = response["results"][0]
                    if found.get("error"):
                        raise ReferenceDiscoveryError("检索来源返回失败。")
                    if (found.get("source") != "arxiv" or found.get("source_url") != ARXIV_API
                            or not isinstance(found.get("items"), list) or not isinstance(found.get("retrieved_at"), str)):
                        raise ReferenceDiscoveryError("检索返回缺少来源或时间字段。")
                    datetime.fromisoformat(found["retrieved_at"].replace("Z", "+00:00"))
                    found = {**deepcopy(found), "attempted_at": attempted}
                except Exception:
                    # Do not expose raw transport/provider responses, local paths, or credentials.
                    found = {"error": {"code": "search_failed", "message": "公开书目检索未完成或返回记录不完整，请稍后重试。"}, "attempted_at": attempted}
                cache[key] = found
            row["attempted_at"] = found["attempted_at"]
            if found.get("error"):
                row.update(status="failed", error=found["error"])
                continue
            candidates = []
            ignored = 0
            for paper in found["items"][:RESULTS_PER_REFERENCE]:
                candidate = _candidate(paper, parsed) if isinstance(paper, dict) else None
                if candidate is None:
                    ignored += 1
                else:
                    candidates.append(candidate)
            row.update(status="completed" if candidates else "not_found", candidates=candidates,
                       source=found["source"], source_url=found["source_url"], retrieved_at=found["retrieved_at"],
                       cached=found.get("cached") if isinstance(found.get("cached"), bool) else None,
                       next_network_refresh_at=found.get("next_network_refresh_at"), total_results=found.get("total_results"),
                       ignored_invalid_metadata=ignored)
            if not candidates:
                row["reason"] = "本次返回中未取得可核对的官方元数据；不表示该论文不存在。"
        completed = sum(row["status"] in {"completed", "not_found"} for row in results)
        status = "completed" if completed == len(results) else "partial" if completed else "failed" if any(row["status"] == "failed" for row in results) else "skipped"
        return {"document_id": document_id, "run_id": structure["run_id"], "status": status, "results": results,
                "max_searches": max_searches, "requests_used": requests_used, "selected_count": len(selected),
                "request_budget_unit": "unique_lookup_calls_including_cache_or_failure",
                "duplicate_selections_ignored": len(reference_ids) - len(selected), "metadata_only": True,
                "automatic_download": False, "requires_user_confirmation": True,
                "limitations": ["仅查询选定书目中的标识或标题片段，不发送正文引用上下文。",
                                "沿用DiscoveryService的共享3秒网络间隔和24小时缓存；cached及retrieved_at保留来源原值。",
                                "标识相符与标题候选分开标注；全部结果仍需确认，不自动下载或替换本地论文。"]}
