"""Conservative local citation graphs with immutable page/block evidence.

Edges mean A's reference list identifies B. Topic overlap never creates an edge.
Ambiguous identities and missing reference evidence remain unresolved. No network.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import json
import re
import sqlite3
import unicodedata

from .discovery import ARXIV_ID
from .library import Library, LibraryError

DOI = re.compile(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.I)
ARXIV_REFERENCE = re.compile(r"(?:arxiv\s*:\s*|arxiv\.org/(?:abs|pdf)/)((?:\d{4}\.\d{4,5}|[a-z][a-z.-]*/\d{7})(?:v[1-9]\d*)?)", re.I)
REFERENCE_HEADING = re.compile(r"^\s*(?:\d+[.\s]+)?(?:references|bibliography|参考文献)\s*:?\s*$", re.I)
END_REFERENCES = re.compile(r"^\s*(?:appendix(?:\s+[A-Z0-9])?|appendices|supplementary\s+material|附录)\b", re.I)


def normalized_title(text: str) -> str:
    return " ".join(re.findall(r"[\w]+", unicodedata.normalize("NFKC", text).casefold(), re.UNICODE))


def _doi(text: str) -> set[str]:
    return {match.group().rstrip(".,;").casefold() for match in DOI.finditer(text)}


def _specific_title(title: str) -> bool:
    return len(title) >= 16 and (len(title.split()) >= 3 or len(re.findall(r"[\u3400-\u9fff]", title)) >= 8)


def _contains_title(text: str, title: str) -> bool:
    return f" {title} " in f" {normalized_title(text)} "


def _title_proof(blocks: list[dict], title: str) -> list[dict]:
    """A PDF title may span adjacent blocks; metadata alone is never proof."""
    if not _specific_title(title):
        return []
    for start in range(len(blocks)):
        group=[]
        for block in blocks[start:start+4]:
            box=json.loads(block['bbox']) if isinstance(block['bbox'],str) else block['bbox']
            if group:
                previous=group[-1]
                prior=json.loads(previous['bbox']) if isinstance(previous['bbox'],str) else previous['bbox']
                if block['page']!=previous['page'] or box[1]-prior[3]>.06:
                    break
            group.append(block)
            combined=' '.join(item['text'] for item in group)
            if len(combined)>1600:
                break
            if _contains_title(combined,title):
                return group
    return []


def _evidence(block: dict, document_id: str, run_id: str) -> dict:
    bbox = block["bbox"]
    return {"document_id": document_id, "run_id": run_id, "page": block["page"],
            "block_id": block["id"], "bbox": json.loads(bbox) if isinstance(bbox, str) else bbox,
            "text": block["text"]}


def pagerank(node_ids: list[str], edges: list[dict], damping: float = .85) -> dict[str, float]:
    """Unweighted, directed PageRank; dangling mass is redistributed uniformly."""
    if not node_ids:
        return {}
    outgoing = {identifier: set() for identifier in node_ids}
    for edge in edges:
        outgoing[edge["source"]].add(edge["target"])
    count = len(node_ids)
    scores = {identifier: 1 / count for identifier in node_ids}
    for _ in range(100):
        dangling = sum(scores[identifier] for identifier, targets in outgoing.items() if not targets)
        updated = {identifier: (1 - damping) / count + damping * dangling / count for identifier in node_ids}
        for source, targets in outgoing.items():
            for target in targets:
                updated[target] += damping * scores[source] / len(targets)
        if sum(abs(updated[key] - scores[key]) for key in scores) < 1e-10:
            scores = updated
            break
        scores = updated
    return scores


class GraphService:
    def __init__(self, library: Library):
        self.library = library

    def _catalog(self) -> tuple[list[dict], dict[str, str]]:
        path = self.library.data_dir / "discovery.sqlite3"
        if not path.exists():
            return [], {}
        # Discovery owns this schema. This read-only connection never creates it.
        with sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=30) as db:
            records = [json.loads(row[0]) for row in db.execute("SELECT record FROM papers")]
            imported = dict(db.execute("SELECT paper_id,document_id FROM imports"))
        return records, imported

    def _blocks(self, document: dict, run_id: str) -> list[dict]:
        with self.library._connection() as db:
            return [dict(row) for row in db.execute(
                "SELECT id,page,ordinal,text,bbox FROM blocks WHERE run_id=? ORDER BY page,ordinal", (run_id,))]

    def _references(self, document: dict, run_id: str, blocks: list[dict]) -> tuple[list[dict], str]:
        getter = getattr(self.library, "get_structure", None)
        if callable(getter):
            structure = getter(document["id"], run_id=run_id)
            return list(structure.get("references", [])), structure.get("method", "structure")
        # Incremental fallback for the original parser. Require an explicit
        # References heading and retain each source block's page and identity.
        references = []
        inside = False
        for block in blocks:
            selected = []
            for line in block["text"].splitlines():
                if REFERENCE_HEADING.match(line):
                    inside = True
                    continue
                if inside and END_REFERENCES.match(line):
                    inside = False
                elif inside:
                    selected.append(line)
            text = "\n".join(selected).strip()
            if text:
                references.append({"id": f"ref:{block['id']}", "text": text,
                                   "evidence": [_evidence(block, document["id"], run_id)], "citation_ids": []})
        return references, "explicit_reference_heading_blocks"

    @staticmethod
    def _validated_reference(reference: dict, document: dict, run_id: str,
                             blocks_by_id: dict[str, dict]) -> tuple[str, list[dict]] | None:
        raw = reference.get("evidence", [])
        if isinstance(raw, dict):
            raw = [raw]
        raw = [*raw, *reference.get('continuation_evidence', [])]
        evidence = []
        retained_blocks = set()
        for item in raw:
            if item.get("document_id", document["id"]) != document["id"] or item.get("run_id", run_id) != run_id:
                return None
            block = blocks_by_id.get(item.get("block_id"))
            if not block or item.get("page", block["page"]) != block["page"]:
                return None
            if block['id'] not in retained_blocks:
                evidence.append(_evidence(block, document["id"], run_id))
                retained_blocks.add(block['id'])
        text = str(reference.get("text", ""))
        if not text or not evidence:
            return None
        # Do not trust a generated reference title unless it is actually present
        # in the retained parser evidence. Normalize whitespace/punctuation only.
        if normalized_title(text) not in normalized_title(" ".join(item["text"] for item in evidence)):
            return None
        return text, evidence

    def build(self, document_ids: list[str] | None = None, *,
              run_ids: dict[str, str | None] | None = None) -> dict:
        if document_ids is not None and (not isinstance(document_ids, list) or not all(isinstance(item, str) for item in document_ids)):
            raise LibraryError("图谱文献列表格式不正确。")
        documents = ([doc for doc in self.library.list_documents() if doc.get("is_current_version", True) and not doc.get("archived_at")]
                     if document_ids is None else [self.library.get_document(identifier) for identifier in dict.fromkeys(document_ids)])
        if len(documents) > 100:
            raise LibraryError("一次最多构建100篇本地文献的局域引用图谱。")
        if run_ids is not None:
            if (document_ids is None or not isinstance(run_ids, dict)
                    or set(run_ids) != {doc['id'] for doc in documents}
                    or any(value is not None and not isinstance(value, str) for value in run_ids.values())):
                raise LibraryError("固定图谱解析版本必须与明确选择的文献一一对应。")
            frozen = []
            for doc in documents:
                run = self.library.get_run(run_ids[doc['id']]) if run_ids[doc['id']] else None
                if run and (run['document_id'] != doc['id'] or run['status'] not in {'completed', 'partial'}):
                    raise LibraryError("图谱解析版本不属于选定文献或尚未完成。")
                frozen.append({**doc, 'available_run': run})
            documents = frozen
        catalog, imported = self._catalog()
        metadata_by_document = defaultdict(list)
        for record in catalog:
            if record["id"] in imported:
                metadata_by_document[imported[record["id"]]].append(record)
        nodes = {}
        identities = {}
        document_blocks = {}
        reference_sets = {}
        unresolved = []
        extraction_methods = set()
        for doc in documents:
            run = doc.get("available_run")
            blocks = self._blocks(doc, run["id"]) if run else []
            document_blocks[doc["id"]] = blocks
            first_page = [block for block in blocks if block["page"] == 1]
            title = normalized_title(doc["title"])
            title_proof = _title_proof(first_page,title)
            node_evidence = [_evidence(block, doc["id"], run["id"]) for block in title_proof]
            identity = {"titles": {title} if title_proof else set(), "arxiv_ids": set(), "dois": set()}
            for block in first_page:
                content = block["text"].strip()
                own_arxiv = re.match(r"^arxiv\s*:\s*(" + ARXIV_ID.pattern + r")", content, re.I)
                own_doi = _doi(content) if len(content.split()) <= 3 and re.match(r"^(?:doi\s*:|https?://(?:dx\.)?doi\.org/|10\.)", content, re.I) else set()
                if own_arxiv:
                    identity["arxiv_ids"].add(own_arxiv[1].casefold())
                if own_doi:
                    identity["dois"].update(own_doi)
                if own_arxiv or own_doi:
                    node_evidence.append(_evidence(block, doc["id"], run["id"]))
            metadata_sources = []
            for record in metadata_by_document[doc["id"]]:
                if not record.get("metadata_verified"):
                    continue
                identity["arxiv_ids"].add(record["arxiv_id"].casefold())
                if record.get("doi"):
                    identity["dois"].update(_doi(record["doi"]))
                identity["titles"].add(normalized_title(record["title"]))
                metadata_sources.append({"source_url": record["source_url"], "retrieved_at": record["retrieved_at"], "paper_id": record["id"]})
            nodes[doc["id"]] = {"id": doc["id"], "document_id": doc["id"], "run_id": run["id"] if run else None,
                                "title": doc["title"], "filename": doc["filename"], "kind": "local_document",
                                "fulltext_available": bool(run), "evidence": node_evidence, "metadata_sources": metadata_sources}
            identities[doc["id"]] = identity
            if run:
                references, method = self._references(doc, run["id"], blocks)
                reference_sets[doc["id"]] = references
                extraction_methods.add(str(method))
                if not references:
                    unresolved.append({"document_id": doc["id"], "reason": "未识别到可核验的参考文献区，未根据主题相似度建立引用边。", "evidence": []})
            else:
                unresolved.append({"document_id": doc["id"], "reason": "暂无可用文字解析结果。", "evidence": []})

        local_arxiv_ids = set().union(*(item["arxiv_ids"] for item in identities.values())) if identities else set()
        external = {}
        for paper in catalog:
            if not paper.get("metadata_verified") or paper["arxiv_id"].casefold() in local_arxiv_ids:
                continue
            external[paper["id"]] = {"id": paper["id"], "document_id": None, "run_id": None, "title": paper["title"],
                                     "kind": "external_metadata", "fulltext_available": False, "evidence": [],
                                     "source_url": paper["source_url"], "retrieved_at": paper["retrieved_at"],
                                     "metadata_sources": [{"paper_id": paper["id"], "source_url": paper["source_url"], "retrieved_at": paper["retrieved_at"]}]}
            identities[paper["id"]] = {"arxiv_ids": {paper["arxiv_id"].casefold()}, "dois": _doi(paper.get("doi") or ""),
                                       "titles": {normalized_title(paper["title"])}}

        edge_map = {}
        matched_reference_count = 0
        for doc in documents:
            source = doc["id"]
            run = doc.get("available_run")
            if not run:
                continue
            by_id = {block["id"]: block for block in document_blocks[source]}
            for reference in reference_sets.get(source, []):
                validated = self._validated_reference(reference, doc, run["id"], by_id)
                if not validated:
                    unresolved.append({"document_id": source, "reference_id": reference.get("id"), "reason": "参考书目无法与保留的原文块一致核对，未连边。", "evidence": []})
                    continue
                text, evidence = validated
                cited_arxiv = {match[1].casefold() for match in ARXIV_REFERENCE.finditer(text)}
                cited_doi = _doi(text)
                matches = {}
                for target, identity in identities.items():
                    if target == source:
                        continue
                    arxiv_match = any(cited == own or (not re.search(r"v\d+$", cited) and cited == re.sub(r"v\d+$", "", own)) for cited in cited_arxiv for own in identity["arxiv_ids"])
                    doi_match = bool(cited_doi & identity["dois"])
                    title_match = any(_specific_title(title) and _contains_title(text, title) for title in identity["titles"])
                    # A title must not override a contradictory known identifier.
                    if (cited_arxiv and identity["arxiv_ids"] and not arxiv_match) or (cited_doi and identity["dois"] and not doi_match):
                        title_match = False
                    if arxiv_match or doi_match or title_match:
                        matches[target] = "arxiv_identifier" if arxiv_match else ("doi" if doi_match else "exact_title_in_reference")
                # A parsed block can hold multiple distinct references. Multiple
                # targets are acceptable only if they identify different works;
                # a shared identifier/title across versions remains ambiguous.
                accepted = {}
                for target, method in matches.items():
                    identity = identities[target]
                    collisions = []
                    for other in matches:
                        if other == target:
                            continue
                        theirs = identities[other]
                        if method == "arxiv_identifier":
                            same = any(own == another or (re.sub(r"v\d+$", "", own) in cited_arxiv and re.sub(r"v\d+$", "", own) == re.sub(r"v\d+$", "", another)) for own in identity["arxiv_ids"] for another in theirs["arxiv_ids"])
                        elif method == "doi":
                            same = bool(identity["dois"] & theirs["dois"])
                        else:
                            same = bool(identity["titles"] & theirs["titles"])
                        if same:
                            collisions.append(other)
                    if not collisions:
                        accepted[target] = method
                    else:
                        unresolved.append({"document_id": source, "reference_id": reference.get("id"), "reason": "引用目标存在多个同名或版本候选，无法唯一确认。", "candidate_ids": [target, *collisions], "evidence": evidence})
                if not matches:
                    unresolved.append({"document_id": source, "reference_id": reference.get("id"), "reference_text": text,
                                       "reason": "尚未在本地文献及已核验公开元数据中找到明确引用目标。", "evidence": evidence})
                for target, method in accepted.items():
                    if target in external:
                        nodes[target] = external[target]
                    key = (source, target)
                    if key not in edge_map:
                        edge_map[key] = {"id": f"{source}->{target}", "source": source, "target": target, "type": "citation",
                                         "match_method": method, "evidence": [], "reference_ids": [], "citation_ids": []}
                    edge = edge_map[key]
                    retained = {(item["run_id"], item["block_id"]) for item in edge["evidence"]}
                    edge["evidence"].extend(item for item in evidence if (item["run_id"], item["block_id"]) not in retained)
                    if reference.get("id") not in edge["reference_ids"]:
                        edge["reference_ids"].append(reference.get("id"))
                    edge["citation_ids"] = sorted(set(edge["citation_ids"] + reference.get("citation_ids", [])))
                    matched_reference_count += 1

        edges = list(edge_map.values())
        scores = pagerank(list(nodes), edges)
        incoming = defaultdict(int)
        outgoing = defaultdict(int)
        for edge in edges:
            incoming[edge["target"]] += 1
            outgoing[edge["source"]] += 1
        ordered = sorted(nodes, key=lambda identifier: (-scores[identifier], identifier))
        for index, identifier in enumerate(ordered):
            node = nodes[identifier]
            node.update(in_degree=incoming[identifier], out_degree=outgoing[identifier], pagerank=round(scores[identifier], 8),
                        centrality=round(scores[identifier], 8), rank=index + 1,
                        role="isolated" if incoming[identifier] + outgoing[identifier] == 0 else ("locally_cited" if incoming[identifier] else "citing_document"))
        return {"nodes": [nodes[identifier] for identifier in ordered], "edges": edges,
                "unresolved": unresolved[:500], "unresolved_count": len(unresolved),
                "matched_reference_count": matched_reference_count, "generated_at": datetime.now(timezone.utc).isoformat(),
                "method": {"name": "PageRank", "damping": .85, "direction": "A → B 表示 A 的参考书目引用 B。",
                           "scope": "当前选定文献与其中可匹配的已核验外部书目", "reference_extraction": sorted(extraction_methods),
                           "explanation": "分数沿引用方向传播，并均匀分配无出边节点的权重。分数只反映当前局域样本，不能据此认定学界整体地位。"},
                "network_used": False,
                "limitations": ["未能提取参考文献、缺少标识或存在版本歧义的关系保持未确认，不按主题相似度补边。",
                                "元数据节点未下载全文，不能视为可全文问答的文献。", "这里不自动判断某条引用的学术价值或是否属于应付式引用。"]}
