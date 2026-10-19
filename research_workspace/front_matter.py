"""Local, conservative front-matter candidates with immutable text evidence.

This does not promote inferred names to catalog metadata. Email domains and
known author identities are never used to fill an affiliation.
"""
from __future__ import annotations

import re


_ORG = re.compile(r"\b(?:university|universit[éeät]+|institute|institution|laborator(?:y|ies)|"
                  r"department|school|college|research|google|openai|microsoft|meta|"
                  r"deepmind|nvidia|ibm|amazon|corporation)\b|大学|学院|研究院|研究所|实验室", re.I)
_BOUNDARY = re.compile(r"^(?:abstract|摘要|(?:1\.?\s+)?introduction|引言|绪论)\s*$", re.I)
_NON_NAME = re.compile(r"\b(?:attention|learning|understanding|model|models|language|"
                       r"transformer|transformers|network|networks|neural|deep|title|"
                       r"conference|proceedings|copyright|provided|permission|abstract|"
                       r"contributions?|equal|work|performed|arxiv|volume|journal|preprint|"
                       r"corresponding|author|independent|researcher|street|parkway|"
                       r"kingdom|usa|campus|center|centre|physics)\b", re.I)
_MARKS = "0123456789⁰¹²³⁴⁵⁶⁷⁸⁹*∗†‡⋆"
_DIGITS = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹", "0123456789")


def _numeric_marks(text):
    return sorted(set(re.findall(r"\d+", text.translate(_DIGITS))) | {c for c in text if c in "†‡⋆"})


def _name(text):
    """Return exact character offsets, never a rewritten personal name."""
    left = len(text) - len(text.lstrip(_MARKS + " ,"))
    right = len(text.rstrip(_MARKS + " ,"))
    name = text[left:right]
    if not name or _NON_NAME.search(name) or _ORG.search(name) or any(c in name for c in "@:/(){}[]=;"):
        return None
    # Bare Chinese short phrases are too ambiguous for this rule set.
    # Latin names may contain initials and particles.
    words = name.split()
    if not 2 <= len(words) <= 6:
        return None
    for word in words:
        if word in {"de", "del", "van", "von", "da", "di", "la", "der"}:
            continue
        letters = [c for c in word if c.isalpha()]
        if not letters or not letters[0].isupper() or any(not(c.isalpha() or c in ".-'’") for c in word):
            return None
    return left, right, _numeric_marks(text[:left] + text[right:])


def extract_front_matter(lines, anchor):
    first = [line for line in lines if line["block"]["page"] == 1]
    boundary = next((i for i, line in enumerate(first) if _BOUNDARY.fullmatch(line["text"])), None)
    result = {"status": "not_detected", "method": "front-matter-v1-local-candidates",
              "authors": [], "affiliations": [], "relations": [],
              "limitations": ["首页规则候选，须人工核对；不自动覆盖目录作者信息。",
                              "只依据明确标记、同一行姓名机构、同块唯一机构或无歧义近邻栏位关联；邮箱域名不作为机构依据。",
                              "当前规则侧重拉丁字母姓名；未标注中文姓名、跨块机构续行及未知版式可能漏检，机构文本不保证完整。",
                              "页码、字符范围和文本块坐标来自固定解析版本，不声称精确字形坐标。"]}
    if boundary is None or boundary > 250:
        result["reason"] = "no_bounded_front_matter"
        result["audit"] = {"source_spans_valid": True, "relation_count": 0, "unresolved_author_count": 0}
        return result
    header = first[:boundary]
    # Reject rotated stamps and footers even if extraction order precedes Abstract.
    header = [line for line in header if line["block"]["bbox"][1] < .75 and
              line["block"]["bbox"][2] - line["block"]["bbox"][0] >= .07]
    org_lines, inline_names = [], []
    for line in header:
        text = line["text"]
        if (len(text) > 180 or "@" in text or
                re.search(r"[.!?。]$|\b(?:provided|permission|propose|performed|contribution)\b", text, re.I)):
            continue
        for part in re.finditer(r"[^;]+", text):
            raw = part[0]; prefix = re.match(r"^[\d⁰¹²³⁴⁵⁶⁷⁸⁹, *∗†‡⋆]*", raw)[0]
            value = raw[len(prefix):].strip()
            if not value or not _ORG.search(value):
                continue
            inline = None
            if "," in value:
                name_part, following = value.split(",",1)
                parsed = _name(name_part)
                if parsed and _ORG.search(following):
                    inline = (name_part,parsed)
                    value = following.strip()
            offset = part.start()+raw.index(value)
            org = {"id": f"affiliation-{len(org_lines)+1}", "text": value,
                   "markers": _numeric_marks(prefix), "status": "candidate",
                   "evidence": anchor(line["block"], line["start"]+offset, line["start"]+offset+len(value))}
            org_lines.append((line, org))
            if inline:
                name_part,(left,right,marks) = inline
                start = line["start"]+part.start()+raw.index(name_part)+left
                inline_names.append({"id": "", "name": name_part[left:right], "markers": marks,
                    "status": "candidate", "affiliation_ids": [], "inline_affiliation_id": org["id"],
                    "evidence": anchor(line["block"],start,start+right-left)})
    result["affiliations"] = [org for _, org in org_lines]
    for line in header:
        text = line["text"]
        if any(line is item for item, _ in org_lines) or "@" in text or _NON_NAME.search(text):
            continue
        # A name candidate must have an actual affiliation/contact nearby. No
        # title-only parsing and no inference from names in the abstract/body.
        box = line["block"]["bbox"]
        # Addresses following an institution/contact are not name candidates.
        previous = [other for other in header if other["block"] is line["block"] and other["start"] < line["start"]]
        if any("@" in other["text"] or _ORG.search(other["text"]) or _NON_NAME.search(other["text"]) for other in previous):
            continue
        context = [other for other in header if "@" in other["text"] or any(other is item for item, _ in org_lines)]
        marked_context = bool(set(_numeric_marks(text)) & {mark for _,org in org_lines for mark in org["markers"]})
        if not marked_context and not any(other["block"] is line["block"] or
                   (other["start"] == 0 and (_ORG.search(other["text"]) or "@" in other["text"]) and
                    not ("," in other["text"] and _name(other["text"].split(",",1)[0])) and
                    -.006 <= other["block"]["bbox"][1]-box[3] <= .05 and
                    min(box[2],other["block"]["bbox"][2])-max(box[0],other["block"]["bbox"][0]) > 0)
                   for other in context):
            continue
        parts = list(re.finditer(r"[^,;]+", text))
        # Commas inside numeric footnote lists are ambiguous; keep unresolved.
        if re.search(r"[\d⁰¹²³⁴⁵⁶⁷⁸⁹],\s*[\d⁰¹²³⁴⁵⁶⁷⁸⁹]", text):
            parts = [re.match(r".*", text)]
        for part in parts:
            raw = part[0]
            name = _name(raw)
            if not name:
                continue
            left, right, markers = name
            start = line["start"]+part.start()+left
            result["authors"].append({"id": f"author-{len(result['authors'])+1}", "name": raw[left:right],
                "markers": markers, "status": "candidate", "affiliation_ids": [],
                "evidence": anchor(line["block"], start, start+right-left)})

    result["authors"].extend(inline_names)
    result["authors"].sort(key=lambda a: (next(i for i,line in enumerate(header) if line["block"]["block_id"]==a["evidence"]["block_id"]),a["evidence"]["char_start"]))
    authors = result["authors"]
    for i,author in enumerate(authors):
        author["id"] = f"author-{i+1}"
    org_symbols = {m for _,org in org_lines for m in org["markers"] if m in "†‡⋆"}
    for author in authors:
        ev = author["evidence"]
        possible, method = [], None
        effective_marks = [m for m in author["markers"] if m not in "†‡⋆" or org_symbols]
        inline_id = author.pop("inline_affiliation_id",None)
        if inline_id:
            possible = [org for _,org in org_lines if org["id"] == inline_id]
            method = "inline_author_affiliation"
        elif effective_marks:
            for marker in effective_marks:
                matches = [org for _,org in org_lines if marker in org["markers"]]
                if len(matches) != 1:
                    possible = []
                    break
                possible.extend(matches)
            method = "explicit_affiliation_marker"
        else:
            same_block = [org for _,org in org_lines if org["evidence"]["block_id"] == ev["block_id"]]
            # Never propagate an institution carrying a marker to unmarked names.
            if len(same_block) == 1 and not same_block[0]["markers"]:
                possible, method = same_block, "single_affiliation_in_author_block"
            elif not same_block:
                box = ev["bbox"]
                for _,org in org_lines:
                    ob = org["evidence"]["bbox"]
                    overlap = min(box[2],ob[2])-max(box[0],ob[0])
                    if org["markers"] or not (-.006 <= ob[1]-box[3] <= .04 and overlap/min(box[2]-box[0],ob[2]-ob[0]) >= .6):
                        continue
                    competitors = [a for a in authors if a["id"] != author["id"] and
                        a["evidence"]["block_id"] != ev["block_id"] and
                        abs(a["evidence"]["bbox"][1]-box[1]) < .012 and
                        -.006 <= ob[1]-a["evidence"]["bbox"][3] <= .04 and
                        min(ob[2],a["evidence"]["bbox"][2])-max(ob[0],a["evidence"]["bbox"][0]) > 0]
                    if not competitors:
                        possible.append(org)
                if len(possible) != 1:
                    possible = []
                method = "unique_nearby_column"
        for org in {org["id"]:org for org in possible}.values():
            author["affiliation_ids"].append(org["id"])
            result["relations"].append({"author_id": author["id"], "affiliation_id": org["id"],
                "status": "candidate", "method": method, "evidence": [ev,org["evidence"]]})
        author["affiliation_status"] = "candidate" if author["affiliation_ids"] else "unresolved"
    result["status"] = "candidates" if authors or org_lines else "not_detected"
    sources = {line["block"]["block_id"]:line["block"] for line in header}
    evidence = [item["evidence"] for item in authors + result["affiliations"]]
    result["audit"] = {"source_spans_valid": all(
        sources[e["block_id"]]["text"][e["char_start"]:e["char_end"]] == e["text"] for e in evidence),
        "relation_count": len(result["relations"]),
        "unresolved_author_count": sum(not a["affiliation_ids"] for a in authors)}
    return result
