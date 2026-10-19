"""Conservative, evidence-backed structure heuristics; no invented metadata."""

from __future__ import annotations

import re


_REFERENCES = re.compile(r"^(?:references|bibliography|参考文献)\s*$", re.I)
_SECTION = re.compile(r"^(\d+(?:\.\d+){0,2})\.?\s+([^\W\d_].{1,150})$", re.UNICODE)
_NAMED = re.compile(r"^(?:abstract|introduction|conclusions?|acknowledg(?:e)?ments?|references|bibliography|摘要|参考文献|结论|附录|appendix(?:\s+[A-Z])?)\s*$", re.I)
_REF_ENTRY = re.compile(r"^\s*(?:\[(\d{1,4})\]|(\d{1,4})\.)\s+(.+)$")
_CITATION = re.compile(r"\[\s*\d{1,4}(?:\s*(?:[,;，]|[-–])\s*\d{1,4})*\s*\]")


def _anchor(block: dict, start: int = 0, end: int | None = None) -> dict:
    end = len(block["text"]) if end is None else end
    return {key: block[key] for key in ("document_id", "run_id", "page", "block_id", "bbox")} | {
        "text": block["text"][start:end], "char_start": start, "char_end": end,
        "location_precision": "text_block",
    }


def extract_structure(blocks: list[dict], document_id: str, run_id: str) -> dict:
    lines = []
    for block in blocks:
        offset = 0
        for raw in block["text"].splitlines(keepends=True):
            text = raw.strip()
            start = offset + len(raw) - len(raw.lstrip())
            if text:
                lines.append({"text": text, "block": block, "start": start,
                              "end": start + len(text)})
            offset += len(raw)

    reference_start = next((i for i, line in enumerate(lines) if _REFERENCES.fullmatch(line["text"])), None)
    reference_end = len(lines)
    if reference_start is not None:
        reference_end = next((i for i in range(reference_start + 1, len(lines))
                              if re.match(r"^(?:appendix(?:\s|$)|附录)", lines[i]["text"], re.I)), len(lines))
    headings, parents = [], []
    consumed, heading_line_indexes = set(), set()
    for index, line in enumerate(lines):
        if index in consumed:
            continue
        if reference_start is not None and reference_start < index < reference_end:
            continue
        numbered = _SECTION.fullmatch(line["text"])
        if numbered and (any(int(part) > 30 for part in numbered[1].split(".")) or
                         not (numbered[2][0].isupper() or re.match(r"[\u3400-\u9fff]", numbered[2]))):
            numbered = None
        number_evidence = None
        title = line["text"]
        number = numbered[1] if numbered else None
        # Many real PDFs extract a heading's number and title on separate lines.
        if re.fullmatch(r"\d{1,2}(?:\.\d{1,2}){0,2}", title) and index + 1 < len(lines):
            following = lines[index + 1]
            candidate = following["text"]
            if (all(int(part) <= 30 for part in title.split(".")) and len(candidate) <= 150 and len(candidate.split()) <= 20
                    and candidate and (candidate[0].isupper() or re.match(r"[\u3400-\u9fff]", candidate))
                    and not candidate.endswith(".") and line["block"]["page"] == following["block"]["page"]):
                number = title
                number_evidence = _anchor(line["block"], line["start"], line["end"])
                title = f"{number} {candidate}"
                line = following
                consumed.add(index + 1)
        if not number and not _NAMED.fullmatch(line["text"]):
            continue
        level = min(3, number.count(".") + 1) if number else 1
        while parents and parents[-1]["level"] >= level:
            parents.pop()
        evidence = _anchor(line["block"], line["start"], line["end"])
        heading = {"id": f"heading-{len(headings) + 1}", "title": title, "level": level,
                   "parent_id": parents[-1]["id"] if parents else None,
                   "page": evidence["page"], "bbox": evidence["bbox"], "evidence": evidence,
                   "method": "numbered_or_named_heading_heuristic"}
        if number_evidence:
            heading["number_evidence"] = number_evidence
        headings.append(heading)
        parents.append(heading)
        heading_line_indexes.add(index)

    references = []
    current = None
    if reference_start is not None:
        for line in lines[reference_start + 1:reference_end]:
            match = _REF_ENTRY.match(line["text"])
            # An author-year entry may wrap its publication year onto a new
            # line. "2020." is a year here, not numbered reference 2020.
            if match and match[2] and 1900 <= int(match[2]) <= 2099:
                match = None
            if match:
                current = {"id": f"reference-{len(references) + 1}", "label": match[1] or match[2],
                           "text": line["text"], "evidence": _anchor(line["block"], line["start"], line["end"]),
                           "continuation_evidence": [], "citation_ids": []}
                references.append(current)
            elif current:
                current["text"] += " " + line["text"]
                current["continuation_evidence"].append(_anchor(line["block"], line["start"], line["end"]))
    if not references and reference_start is not None:
        from .author_year import bibliography
        references=bibliography(lines[reference_start+1:reference_end],_anchor)
    for reference in references:
        doi = re.search(r"\b10\.\d{4,9}/[^\s<>]+", reference["text"], re.I)
        arxiv = re.search(r"(?:arxiv\s*:\s*|arxiv\.org/(?:abs|pdf)/)(\d{4}\.\d{4,5}|[a-z][a-z.\-]+/\d{7})(?:v\d+)?", reference["text"], re.I)
        years = re.findall(r"\b(?:19|20)\d{2}\b", reference["text"])
        reference.update(doi=doi[0].rstrip(".,;)") if doi else None,
                         arxiv_id=arxiv[1].lower() if arxiv else None,
                         year=int(years[0]) if years else None,
                         method=reference.get('method','numbered_reference_heuristic'))
    by_label = {reference["label"]: reference for reference in references}
    citations = []
    # Reference-list brackets are not treated as in-text citation occurrences.
    body_lines = lines if reference_start is None else lines[:reference_start] + lines[reference_end:]
    for line in body_lines:
        for match in _CITATION.finditer(line["text"]):
            labels = []
            for component in re.split(r"[,;，]", match[0][1:-1]):
                numbers = [int(item) for item in re.findall(r"\d+", component)]
                if len(numbers) == 2 and 0 <= numbers[1] - numbers[0] < 100:
                    labels.extend(str(number) for number in range(numbers[0], numbers[1] + 1))
                elif len(numbers) == 1:
                    labels.append(str(numbers[0]))
            ids = [by_label[label]["id"] for label in dict.fromkeys(labels) if label in by_label]
            citation = {"id": f"citation-{len(citations) + 1}", "marker": match[0], "reference_ids": ids,
                        "unresolved_labels": [label for label in labels if label not in by_label],
                        "evidence": _anchor(line["block"], line["start"] + match.start(), line["start"] + match.end()),
                        "method": "numeric_citation_heuristic"}
            citations.append(citation)
            for label in dict.fromkeys(labels):
                if label in by_label:
                    by_label[label]["citation_ids"].append(citation["id"])

    from .author_year import link_citations
    citations.extend(link_citations(body_lines,references,_anchor,len(citations)))

    abstract = None
    abstract_start = next((i for i, line in enumerate(lines)
                           if re.fullmatch(r"abstract|摘要", line["text"], re.I)), None)
    if abstract_start is not None:
        collected = []
        truncated = False
        for index in range(abstract_start + 1, len(lines)):
            line = lines[index]
            if index in heading_line_indexes:
                break
            collected.append(line)
            if sum(len(item["text"]) for item in collected) > 6000:
                truncated = True
                break
        if collected:
            abstract = {"text": " ".join(line["text"] for line in collected),
                        "evidence": [_anchor(line["block"], line["start"], line["end"]) for line in collected],
                        "method": "abstract_heading_heuristic", "truncated": truncated}
    from .front_matter import extract_front_matter
    front_matter = extract_front_matter(lines, _anchor)
    unavailable = ["latex_formulas", "table_cell_structure", "figure_structure",
                   "complex_or_cross_line_author_year_alignment"]
    if not front_matter["authors"] or front_matter["audit"]["unresolved_author_count"]:
        unavailable.append("author_affiliations_not_fully_resolved")
    if not references:
        unavailable.append("numbered_references_not_detected")
    return {"document_id": document_id, "run_id": run_id, "method": "heuristic", "version": "structure-v3-front-matter",
            "headings": headings, "references": references, "citations": citations, "abstract": abstract,
            "front_matter": front_matter,
            "unavailable_fields": unavailable,
            "limitations": ["标题与参考文献使用规则识别，可能漏检或误判，需核对原文。",
                            "位置精度为已保存的文本块；字符偏移帮助定位块内内容，不声称精确字形坐标。",
                            "作者年份书目按首行作者、年份与悬挂缩进识别；跨行引文、同作者同年份歧义及复杂书目保持未确认。"]}
