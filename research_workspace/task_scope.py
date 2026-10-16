"""Attach an actual named task declaration; never infer or approve a predicate."""
from __future__ import annotations
import re

def attach_task_scope(service,claim,evidence,catalog,requirements,audit,context):
    from .intelligence import Citation
    if not requirements or not re.search(r"训练|任务|预测|train|predict",claim.text,re.I):return
    sources={b.evidence_id:b for b in evidence}
    selected={q.evidence_id for q in claim.citations}
    for doc,req in requirements.items():
        own={key for key in selected if sources[key].document_id==doc}
        if not own.intersection(key for task in req["tasks"] for key in task["evidence_ids"]):continue
        names=set(req.get("subject_identifiers",[]))
        if not names.intersection(service._identifier_tokens(claim.text)):continue
        eid,qid=req["scope_evidence_id"],req["scope_quote_id"]
        quote=catalog.get(eid,{}).get(qid)
        if (not quote or eid not in sources or sources[eid].document_id!=doc
                or not names.intersection(service._identifier_tokens(quote))):continue
        if any(q.evidence_id==eid and q.quote_id==qid for q in claim.citations) or len(claim.citations)>=12:continue
        original=service._review_claim_identity(claim,evidence)
        claim.citations.append(Citation(evidence_id=eid,quote_id=qid,quote=quote))
        service._validate_claims([claim],evidence)
        audit.setdefault("task_scope_attachments",[]).append({**context,"model_claim":original,
            "effective_claim_id":service._review_claim_identity(claim,evidence)["claim_id"],
            "attached_scope":{"evidence_id":eid,"quote_id":qid,"quote":quote,"document_id":doc},
            "policy":"attach_only_explicit_named_task_scope_to_its_actual_task_passage_then_independently_review"})
