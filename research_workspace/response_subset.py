"""Discard rejected extras only when retained own facts actually answer the request."""
from __future__ import annotations

async def complete_supported_subset(service,result,reviews,evidence,payload,audit,*,required_documents=None,check_cancel=None):
    from .intelligence import CoveragePayload, _prompt
    claims=(result.claims if hasattr(result,"claims") else [c for s in result.sections for c in s.claims])
    kept=[v["claim_index"] for v in reviews if v["verdict"]=="supported"]
    removed=[v["claim_index"] for v in reviews if v["verdict"]=="unsupported"]
    if not kept or not removed:return None
    rebuilt=result.model_copy(deep=True)
    if hasattr(rebuilt,"claims"):rebuilt.claims=[claims[i].model_copy(deep=True) for i in kept]
    else:
        offset=0
        for s in rebuilt.sections:
            count=len(s.claims);s.claims=[c for i,c in enumerate(s.claims,offset) if i in kept];offset+=count
    final=(rebuilt.claims if hasattr(rebuilt,"claims") else [c for s in rebuilt.sections for c in s.claims])
    used=service._validate_claims(final,evidence)
    if required_documents and {e.document_id for e in evidence if e.evidence_id in used}!=set(required_documents):return None
    source_attempt=audit["attempts"][-1]["attempt"]
    await service._check_cancel(check_cancel)
    verdict=await service.provider.generate(_prompt("coverage_v1"),{
        "task":"verify_response_coverage","response_kind":payload["task"],"actual_answers_only":True,
        "request":payload.get("question") or payload.get("instruction",""),
        "selected_document_ids":payload.get("selected_document_ids",required_documents or []),
        "response_message":rebuilt.message,
        "proposed_sections":[s.model_dump(include={"heading","proposed_work"}) for s in getattr(rebuilt,"sections",[])],
        "proposed_architecture":rebuilt.architecture.model_dump() if getattr(rebuilt,"architecture",None) else None,
        "claims":[{"claim_index":i,**c.model_dump()} for i,c in enumerate(final)],
        "evidence":[b.model_dump() for b in evidence],
    },CoveragePayload)
    await service._check_cancel(check_cancel)
    audit["coverage_model_calls"]+=1
    audit["model_checked"]=True
    identities=[{"claim_index":i,**service._review_claim_identity(c,evidence)} for i,c in enumerate(final)]
    check={"source_attempt":source_attempt,"candidate_claim_ids":[c["claim_id"] for c in identities],
        "actual_answers_only":True,**verdict.model_dump()}
    audit.setdefault("optional_prune_checks",[]).append(check)
    if verdict.missing_aspects or verdict.unverified_statements:return None
    source=audit["attempts"][-1]["claims"]
    final_reviews=[{**reviews[old],"claim_index":i,"claim_id":identities[i]["claim_id"],
        "method":"retained_supported_after_actual_coverage_check"} for i,old in enumerate(kept)]
    attempt=source_attempt+1
    audit["attempts"].append({"attempt":attempt,"problems":[],"claims":identities,"reviews":final_reviews,
        "proposed_architecture":rebuilt.architecture.model_dump() if getattr(rebuilt,"architecture",None) else None,
        "method":"supported_subset_checkpoint_not_new_generation"})
    audit.setdefault("response_coverage",[]).append({"attempt":attempt,"index_space":"this attempt's claims.claim_index (zero-based)",
        "claim_ids":[c["claim_id"] for c in identities],"actual_answers_only":True,
        "response_message":rebuilt.message,"proposed_sections":[s.model_dump(include={"heading","proposed_work"}) for s in getattr(rebuilt,"sections",[])],
        "proposed_architecture":rebuilt.architecture.model_dump() if getattr(rebuilt,"architecture",None) else None,
        **verdict.model_dump()})
    audit["optional_prune"]={"source_attempt":source_attempt,"removed_original_indices":removed,
        "removed_claim_ids":[source[i]["claim_id"] for i in removed],"retained_claim_ids":[c["claim_id"] for c in identities],
        "policy":"remove_rejected_extras_only_after_all_requested_positive_aspects_are_actually_answered"}
    audit.update(status="passed",claim_catalog=identities,claim_reviews=final_reviews,coverage_method="model_actual_answer_coverage")
    for repair in audit.get("claim_repairs",[]):
        target=repair["repaired_claim_id"]
        repair["result_claim_index"]=next((i for i,c in enumerate(identities) if c["claim_id"]==target),None)
        repair["retained_in_final"]=repair["result_claim_index"] is not None
    return type(result).model_validate(rebuilt.model_dump()),used,audit
