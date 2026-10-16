"""Bounded original header/table context for compound bibliographic questions."""
import re


def header_author_links(blocks):
    """Conservative unique same-column pairs; never infer from an email name."""
    authors=[];institutions=[]
    for block in blocks:
        lines=[x.strip() for x in block.text.splitlines() if x.strip()]
        if block.page!=1 or not lines or block.bbox[3]>.55:continue
        if (len(lines)==1 and re.search(r'[*∗†‡]',lines[0]) and '@' not in lines[0]
                and 2<=len(lines[0].split())<=5 and not re.search(r'\d',lines[0])):authors.append(block)
        if (2<=len(lines)<=3 and '@' in lines[-1] and
                re.search(r'\b(?:brain|research|university|institute|laboratory|college|school)\b|大学|学院|研究所',lines[0],re.I)):
            institutions.append(block)
    pairs=[]
    for author in authors:
        a=author.bbox;matches=[]
        for institution in institutions:
            b=institution.bbox
            if (author.document_id,author.run_id)!=(institution.document_id,institution.run_id):continue
            overlap=max(0,min(a[2],b[2])-max(a[0],b[0]))/max(.0001,min(a[2]-a[0],b[2]-b[0]))
            if 0<=b[1]-a[3]<=.025 and abs((a[0]+a[2]-b[0]-b[2])/2)<=.025 and overlap>=.8:matches.append(institution)
        if len(matches)==1:pairs.append((author,matches[0]))
    return [{'author_evidence_id':a.evidence_id,'affiliation_evidence_id':b.evidence_id,
        'document_id':a.document_id,'run_id':a.run_id,'page':1,
        'method':'unique_nearby_centered_header_blocks_v1','author_bbox':a.bbox,'affiliation_bbox':b.bbox}
        for a,b in pairs if sum(other.evidence_id==b.evidence_id for _,other in pairs)==1]


def supplement_question_facets(service,snapshot,evidence,question,document_ids,names):
    bibliographic=bool(re.search(r'作者|机构|单位|隶属|\bauthors?\b|\baffiliations?\b',question,re.I))
    metrics=bool(re.search(r'BLEU|得分|分数|准确率|指标|\b(?:score|accuracy|metric)',question,re.I))
    additions=[];header_ids=[];table_ids=[]
    for document_id in document_ids:
        own=[b for b in snapshot if b.document_id==document_id]
        if not own:continue
        if bibliographic:
            page=min(b.page for b in own)
            opening=[]
            for b in own:
                if b.page!=page:continue
                if re.fullmatch(r'abstract|摘要|(?:1\s+)?introduction',b.text.strip(),re.I):break
                if re.search(r'permission|copyright|proper attribution|rights reserved|^\s*arxiv\s*:',b.text,re.I):continue
                if len(b.text)>1400:continue
                opening.append(b)
            for b in opening[:16]:additions.append(b);header_ids.append(b.evidence_id)
            for b in own:
                if b.page==page and re.search(r'[†‡].*Work performed while',b.text,re.I):
                    additions.append(b);header_ids.append(b.evidence_id)
        if metrics:
            pages={b.page for b in evidence if b.document_id==document_id}
            for page in pages:
                page_blocks=[b for b in own if b.page==page]
                for i,b in enumerate(page_blocks):
                    if not (re.match(r'^Table\s+\d+',b.text,re.I) and re.search(r'BLEU|accuracy|score',b.text,re.I)):continue
                    candidates=[b]
                    for row in page_blocks[i+1:i+18]:
                        text=' '.join(row.text.split())
                        label=service._identifier_tokens(text).intersection(names.get(document_id,[]))
                        header=(len(text)<100 and not re.search(r'\d',text) and
                            re.search(r'BLEU|Model|FLOPs|EN-DE|EN-FR|accuracy|score',text,re.I))
                        if (label and len(text)<450 and len(re.findall(r'\d',text))>=2) or header:candidates.append(row)
                    for row in candidates[:8]:additions.append(row);table_ids.append(row.evidence_id)
    selected=service._fair_blocks(additions,document_ids,24)
    known={b.evidence_id for b in evidence};added=service._excerpts([b for b in selected if b.evidence_id not in known])
    return evidence+added,{'bibliographic_requested':bibliographic,'metrics_requested':metrics,
        'header_evidence_ids':[x for x in header_ids if x in {b.evidence_id for b in selected}],
        'table_evidence_ids':[x for x in table_ids if x in {b.evidence_id for b in selected}],
        'added_evidence_ids':[b.evidence_id for b in added],'max_added':24,
        'bibliographic_layout_links':header_author_links(selected) if bibliographic else [],
        'policy':'original_frozen_snapshot_only; layout_candidates_not_generated_answers'}
