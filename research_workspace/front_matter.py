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
_SYMBOLS = "†‡⋆⋄"
_MARKS = "0123456789⁰¹²³⁴⁵⁶⁷⁸⁹*∗" + _SYMBOLS
_DIGITS = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹", "0123456789")
_UNIT = re.compile(r"\b(?:department|school|college|laborator(?:y|ies))\b|学院|实验室|学系", re.I)
_ROOT_ORG = re.compile(r"\b(?:university|universit\S*|institute|google|openai|microsoft|meta|"
                       r"deepmind|nvidia|ibm|amazon|corporation)\b|大学|研究院|研究所", re.I)
_ROLE = re.compile(r"\b(?:fellow|corresponding|equal|contribution|performed|permission|provided|"
                   r"propose|copyright|independent|researcher)\b|"
                   r"通讯作者|同等贡献|邮编", re.I)
_ADDRESS = re.compile(r"\b(?:street|parkway|room|usa|kingdom)\b|\b\d{4,}\b", re.I)
_DISCIPLINE_WORDS = frozenset("computer science engineering electrical electronic information technology "
                             "artificial intelligence mathematics statistics physics chemistry biology data "
                             "computing systems applied and of the university".split())


def _numeric_marks(text):
    return sorted(set(re.findall(r"\d+", text.translate(_DIGITS))) | {c for c in text if c in _SYMBOLS})


def _name(text, *, labeled=False):
    """Return exact character offsets, never a rewritten personal name."""
    left = len(text) - len(text.lstrip(_MARKS + " ,"))
    right = len(text.rstrip(_MARKS + " ,"))
    name = text[left:right]
    if not name or _NON_NAME.search(name) or _ORG.search(name) or any(c in name for c in "@:/(){}[]=;"):
        return None
    if labeled and re.fullmatch(r"[\u3400-\u9fff]{2,4}", name):
        return left, right, _numeric_marks(text[:left] + text[right:])
    # Bare Chinese short phrases are too ambiguous for this rule set.
    # Latin names may contain initials and particles.
    words = name.split()
    if not 2 <= len(words) <= 6:
        return None
    for word in words:
        if word in {"de", "del", "van", "von", "da", "di", "la", "der", "den"}:
            continue
        letters = [c for c in word if c.isalpha()]
        if not letters or not letters[0].isupper() or any(not(c.isalpha() or c in ".-'’¸¨´") for c in word):
            return None
    return left, right, _numeric_marks(text[:left] + text[right:])


def _institution_before_contact(text):
    """Trim only a visible contact suffix; never derive an org from a domain."""
    if "@" not in text:
        return text
    contact = re.search(r"\{[^{}]*\}@\S+|[^\s,;{}]+@\S+", text)
    if not contact:
        return ""
    return text[:contact.start()].rstrip(" ,;\t")


def _org_value_allowed(value):
    # A legal-name abbreviation is not a body-sentence boundary.
    legal_period = re.search(r"\b(?:Inc|Ltd|Corp)\.$", value, re.I)
    return bool(value and len(value) <= 180 and not _ROLE.search(value) and
                (not re.search(r"[.!?。]$", value) or legal_period))


def _continuation_kind(left, right):
    if (not _org_value_allowed(right) or _ADDRESS.search(right) or "@" in right or
            re.match(r"[\d" + _SYMBOLS + r"]", right)):
        return None
    if re.search(r"\b(?:of|for|the)\s*$", left, re.I):
        # Explicitly unfinished institutional phrase; do not swallow a new
        # institution or an entire author list to complete it.
        if (1 <= len(right.split()) <= 8 and
                all(word.lower() in _DISCIPLINE_WORDS for word in right.split()) and
                (not _ORG.search(right) or re.fullmatch(r"(?:Technology|University)", right, re.I))):
            return "unfinished_institution_phrase"
    if re.fullmatch(r"University", left, re.I) and re.match(r"of\s+", right, re.I):
        return "unfinished_institution_phrase"
    if _UNIT.search(left) and not _ROOT_ORG.search(left):
        if _ROOT_ORG.search(right) and not re.match(r"and\b", right, re.I):
            return "department_parent_institution"
        if re.search(r"\b(?:department|laboratory)\b", left, re.I) and re.search(r"\b(?:school|college)\b", right, re.I):
            return "department_parent_institution"
    return None


def _successor(line, header):
    """Only a consecutive line in the same block or unambiguous column.

    Block bboxes are all that saved parses provide: these tests are layout
    heuristics, not invented line coordinates. Contacts and author lines are
    returned as barriers instead of being skipped.
    """
    same = [other for other in header if other["block"] is line["block"] and other["start"] > line["start"]]
    if same:
        return min(same, key=lambda other: other["start"])
    box = line["block"]["bbox"]
    candidates = []
    for other in header:
        ob = other["block"]["bbox"]
        if other["block"] is line["block"] or any(
                prior["block"] is other["block"] and prior["start"] < other["start"] for prior in header):
            continue
        overlap = min(box[2], ob[2]) - max(box[0], ob[0])
        width = min(box[2]-box[0], ob[2]-ob[0])
        if width > 0 and -.006 <= ob[1]-box[3] <= .035 and overlap/width >= .6:
            candidates.append(other)
    if not candidates:
        return None
    candidates.sort(key=lambda other: other["block"]["bbox"][1])
    first_y = candidates[0]["block"]["bbox"][1]
    # Multiple plausible blocks on the same row must stay separate.
    return candidates[0] if sum(abs(c["block"]["bbox"][1]-first_y) < .006 for c in candidates) == 1 else None


def _affiliations(header, anchor):
    org_lines, inline_names = [], []
    for line in header:
        text = _institution_before_contact(line["text"])
        for part in re.finditer(r"[^;]+", text):
            raw = part[0]
            prefix = re.match(r"^[" + re.escape(_MARKS) + r", ]*", raw)[0]
            value = raw[len(prefix):].strip()
            if not _org_value_allowed(value) or not _ORG.search(value):
                continue
            inline = None
            if "," in value:
                name_part, following = value.split(",", 1)
                parsed = _name(name_part)
                if parsed and _ORG.search(following):
                    inline = name_part, parsed
                    value = following.strip()
            offset = part.start()+raw.index(value)
            evidence = anchor(line["block"], line["start"]+offset, line["start"]+offset+len(value))
            org = {"id": f"affiliation-{len(org_lines)+1}", "text": value,
                   "markers": _numeric_marks(prefix), "status": "candidate",
                   "evidence": evidence, "evidence_parts": [evidence], "continuations": []}
            org_lines.append((line, org))
            if inline:
                name_part, (left, right, marks) = inline
                start = line["start"]+part.start()+raw.index(name_part)+left
                inline_names.append({"id": "", "name": name_part[left:right], "markers": marks,
                    "status": "candidate", "affiliation_ids": [], "inline_affiliation_id": org["id"],
                    "evidence": anchor(line["block"], start, start+right-left)})
    removed, consumed_lines = set(), []
    for line, org in org_lines:
        if org["id"] in removed:
            continue
        if sum(other is line for other, _ in org_lines) > 1:
            continue
        current = line
        for _ in range(6):  # bounded header chain, not a whole-page concatenation
            following = _successor(current, header)
            if following is None or any(following is used for used in consumed_lines):
                break
            # A suffix contact ends this chain even if an org was read before it.
            if "@" in current["text"]:
                break
            next_orgs = [o for li, o in org_lines if li is following and o["id"] not in removed]
            if len(next_orgs) > 1 or any(o["markers"] for o in next_orgs):
                break
            if any(o["inline_affiliation_id"] in {item["id"] for item in next_orgs} for o in inline_names):
                break
            value = next_orgs[0]["text"] if next_orgs else following["text"]
            kind = _continuation_kind(org["text"], value)
            if not kind:
                break
            evidence = next_orgs[0]["evidence"] if next_orgs else anchor(
                following["block"], following["start"], following["end"])
            org["evidence_parts"].append(evidence)
            org["text"] = " ".join(p["text"] for p in org["evidence_parts"])
            org["continuations"].append({"method": kind, "same_block": current["block"] is following["block"]})
            removed.update(o["id"] for o in next_orgs)
            consumed_lines.append(following)
            current = following
    kept = [(li, org) for li, org in org_lines if org["id"] not in removed]
    return kept, inline_names, consumed_lines


def _name_segments(text):
    start = 0
    for separator in re.finditer(r"[,;、，]|\s+(?:and|&)\s+", text):
        yield start, text[start:separator.start()]
        start = separator.end()
    yield start, text[start:]


def _labeled_authors(header, anchor):
    authors, used = [], []
    for index, line in enumerate(header):
        label = re.match(r"^(?:作者|authors?)(?:\s*[:：]\s*|\s*$)", line["text"], re.I)
        if not label:
            continue
        used.append(line)
        offset = label.end()
        value = line["text"][offset:]
        if not value and index+1 < len(header) and header[index+1]["block"] is line["block"]:
            line = header[index+1]
            used.append(line)
            offset, value = 0, line["text"]
        for part_start, raw in _name_segments(value):
            parsed = _name(raw, labeled=True)
            if not parsed:
                continue
            left, right, marks = parsed
            start = line["start"]+offset+part_start+left
            authors.append({"id": "", "name": raw[left:right], "markers": marks,
                "status": "candidate", "affiliation_ids": [],
                "evidence": anchor(line["block"], start, start+right-left)})
    return authors, used


def extract_front_matter(lines, anchor):
    first = [line for line in lines if line["block"]["page"] == 1]
    boundary = next((i for i, line in enumerate(first) if _BOUNDARY.fullmatch(line["text"])), None)
    result = {"status": "not_detected", "method": "front-matter-v2-evidence-parts",
              "authors": [], "affiliations": [], "relations": [],
              "limitations": ["首页规则候选，须人工核对；不自动覆盖目录作者信息。",
                              "只依据明确标记、同一行姓名机构、同块唯一机构或无歧义近邻栏位关联；邮箱域名不作为机构依据。",
                              "机构仅在同栏连续且层级或断句明确时组合；未知版式与未标注中文姓名仍可能漏检。",
                              "页码、字符范围和文本块坐标来自固定解析版本，不声称精确字形坐标。"]}
    if boundary is None or boundary > 250:
        result["reason"] = "no_bounded_front_matter"
        result["audit"] = {"source_spans_valid": True, "relation_count": 0, "unresolved_author_count": 0}
        return result
    header = first[:boundary]
    # Reject rotated stamps and footers even if extraction order precedes Abstract.
    header = [line for line in header if line["block"]["bbox"][1] < .75 and
              line["block"]["bbox"][2] - line["block"]["bbox"][0] >= .07]
    org_lines, inline_names, consumed_lines = _affiliations(header, anchor)
    result["affiliations"] = [org for _, org in org_lines]
    labeled_authors, labeled_lines = _labeled_authors(header, anchor)
    for line in header:
        text = line["text"]
        if (any(line is item for item, _ in org_lines) or any(line is item for item in consumed_lines)
                or any(line is item for item in labeled_lines) or "@" in text or _NON_NAME.search(text)):
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
        parts = list(_name_segments(text))
        # Commas inside numeric footnote lists are ambiguous; keep unresolved.
        if re.search(r"[\d⁰¹²³⁴⁵⁶⁷⁸⁹],\s*[\d⁰¹²³⁴⁵⁶⁷⁸⁹]", text):
            parts = [(0, text)]
        for part_start, raw in parts:
            name = _name(raw)
            if not name:
                continue
            left, right, markers = name
            start = line["start"]+part_start+left
            result["authors"].append({"id": f"author-{len(result['authors'])+1}", "name": raw[left:right],
                "markers": markers, "status": "candidate", "affiliation_ids": [],
                "evidence": anchor(line["block"], start, start+right-left)})

    result["authors"].extend(inline_names)
    result["authors"].extend(labeled_authors)
    result["authors"].sort(key=lambda a: (next(i for i,line in enumerate(header) if line["block"]["block_id"]==a["evidence"]["block_id"]),a["evidence"]["char_start"]))
    authors = result["authors"]
    for i,author in enumerate(authors):
        author["id"] = f"author-{i+1}"
    org_symbols = {m for _,org in org_lines for m in org["markers"] if m in _SYMBOLS}
    for author in authors:
        ev = author["evidence"]
        possible, method = [], None
        effective_marks = [m for m in author["markers"] if m not in _SYMBOLS or org_symbols]
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
                "status": "candidate", "method": method, "evidence": [ev,*org["evidence_parts"]]})
        author["affiliation_status"] = "candidate" if author["affiliation_ids"] else "unresolved"
    result["status"] = "candidates" if authors or org_lines else "not_detected"
    sources = {line["block"]["block_id"]:line["block"] for line in header}
    evidence = [a["evidence"] for a in authors] + [p for org in result["affiliations"] for p in org["evidence_parts"]]
    result["audit"] = {"source_spans_valid": all(
        sources[e["block_id"]]["text"][e["char_start"]:e["char_end"]] == e["text"] and
        all(e[k] == sources[e["block_id"]][k] for k in ("document_id", "run_id", "page", "bbox")) for e in evidence),
        "source_part_count": len(evidence),
        "relation_count": len(result["relations"]),
        "unresolved_author_count": sum(not a["affiliation_ids"] for a in authors)}
    return result
