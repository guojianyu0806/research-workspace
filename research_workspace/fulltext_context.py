"""Bounded structural recall from the same immutable PDF snapshot."""
import re


def supplement_fulltext_context(service, snapshot, evidence, question, document_ids):
    additions=[];table_ids=[];limitation_ids=[]
    # A retrieval miss is not a statement about the full paper. Preserve nearby
    # actual rows/headers and explicit validity sections as additional candidates.
    for document_id in document_ids:
        own=[b for b in snapshot if b.document_id==document_id]
        captions=[(i,b) for i,b in enumerate(own) if re.match(r'^\s*(?:Table|表)\s*\d+',b.text,re.I)]
        terms=set(service._search_terms(question))
        captions.sort(key=lambda item:(-len(terms.intersection(service._search_terms(item[1].text))),item[1].page))
        for _,caption in captions[:2]:
            candidates=[caption]
            for block in own:
                if block.page!=caption.page or block.evidence_id==caption.evidence_id:continue
                # Include either placement (table above or below caption). Do not
                # attach arbitrary next-column prose as an inferred table row.
                distance=max(0,caption.bbox[1]-block.bbox[3],block.bbox[1]-caption.bbox[3])
                overlap=max(0,min(caption.bbox[2],block.bbox[2])-max(caption.bbox[0],block.bbox[0]))
                if overlap>0 and distance<=.22 and len(re.findall(r'\d',block.text))>=3:
                    candidates.append(block)
            for block in candidates[:4]:additions.append(block);table_ids.append(block.evidence_id)
        for index,block in enumerate(own):
            heading=' '.join(block.text.split())
            if not re.match(r'^(?:\d+(?:\.\d+)*\s*[.)]?\s*)?(?:limitations?|threats? to validity|研究限制|局限性)(?:\b|$)',heading,re.I):continue
            count=0
            for candidate in own[index:index+10]:
                if candidate.page>block.page+1:break
                text=' '.join(candidate.text.split())
                if len(text)<140 and (re.match(r'^Manuscript submitted',text,re.I) or
                    re.fullmatch(r'\d+\s+.*\bet al\.?',text,re.I)):continue
                if candidate is not block and re.match(r'^\d+(?:\.\d+)*\s+[A-Z]',candidate.text.strip()):break
                additions.append(candidate);limitation_ids.append(candidate.evidence_id)
                count+=1
                if count>=6:break
    selected=service._fair_blocks(additions,document_ids,16)
    known={b.evidence_id for b in evidence}
    added=service._excerpts([b for b in selected if b.evidence_id not in known])
    selected_ids={b.evidence_id for b in selected}
    return evidence+added,{'policy':'same_frozen_pdf_structural_candidates_not_approved_facts',
        'max_added':16,'max_tables_per_document':2,
        'added_evidence_ids':[b.evidence_id for b in added],
        'table_evidence_ids':[i for i in table_ids if i in selected_ids],
        'limitation_evidence_ids':[i for i in limitation_ids if i in selected_ids]}
