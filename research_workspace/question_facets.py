"""Bounded original header/table context for compound bibliographic questions."""
import re


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
        'policy':'original_frozen_snapshot_only; layout_candidates_not_generated_answers'}
