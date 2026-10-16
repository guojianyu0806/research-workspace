"""Repair only rejected cards against their own frozen-document passages."""
from __future__ import annotations
import re

async def repair_rejected_claims(service, result, reviews, evidence, catalog, payload, audit, *, check_cancel=None):
    from .intelligence import ClaimRepairPayload, MAX_ISOLATED_CLAIMS, ModelOutputError, _prompt
    rebuilt=result.model_copy(deep=True)
    claims=(rebuilt.claims if hasattr(rebuilt,"claims") else [c for section in rebuilt.sections for c in section.claims])
    sources={b.evidence_id:b for b in evidence}
    records=audit.setdefault("claim_repairs",[])
    first_record=len(records)
    removed=set()
    for review in reviews:
        if review["verdict"]!="unsupported":continue
        if len(records)>=MAX_ISOLATED_CLAIMS:
            raise ModelOutputError("单次结果的局部结论纠正超过24条上限；未展示无效回答。")
        await service._check_cancel(check_cancel)
        i=review["claim_index"];claim=claims[i]
        documents={sources[q.evidence_id].document_id for q in claim.citations}
        own=[b for b in evidence if b.document_id in documents]
        reserved={q.evidence_id for q in claim.citations}
        for doc in documents:
            requirement=payload.get("required_training_tasks",{}).get(doc)
            if requirement:
                reserved.add(requirement["scope_evidence_id"])
        names=service._identifier_tokens(claim.text)&service._named_attribution_identifiers(catalog)
        own.sort(key=lambda b:(b.evidence_id not in reserved,
            not bool(service._identifier_tokens(b.text)&names),b.page))
        focused=own[:max(12,len(reserved))]
        conditional_methodology=bool(re.search(
            r"不同.{0,15}(?:任务|数据集).{0,45}(?:不构成|不能|无法).{0,35}(?:比较|优劣)|"
            r"(?:different|unlike).{0,40}(?:tasks|datasets).{0,70}(?:not|cannot).{0,35}(?:compar|superior|rank)",claim.text,re.I))
        from .claim_projection import project_supported_clauses
        projected=await project_supported_clauses(service,claim,evidence,catalog,audit,check_cancel=check_cancel)
        if projected is not None:
            repaired=ClaimRepairPayload(claim=projected)
        else:
            audit["claim_repair_prompt_version"]="claim-repair-v1.1"
            repaired=await service.provider.generate(_prompt("claim_repair_v1"),{
                "task":"repair_grounded_claim","response_kind":payload["task"],
                "request":payload.get("question") or payload.get("instruction",""),
                "claim":claim.model_dump(),"rejection_reason":review["reason"],
                "allow_methodology_relocation":conditional_methodology,
                "required_training_tasks":{d:payload["required_training_tasks"][d] for d in documents
                    if d in payload.get("required_training_tasks",{})},
                "evidence":[{**b.model_dump(),"citation_spans":[{"quote_id":qid,"text":text}
                    for qid,text in catalog[b.evidence_id].items()]} for b in focused],
            },ClaimRepairPayload)
        await service._check_cancel(check_cancel)
        if repaired.claim is None:
            if not conditional_methodology or not repaired.limitation:
                raise ModelOutputError("局部纠正不能用缺口说明删除事实结论；结果已拒绝。")
            records.append({"claim_index":i,"original_claim_id":service._review_claim_identity(claim,evidence)["claim_id"],
                "repaired_claim_id":None,"relocated_limitation":repaired.limitation,
                "policy":"move_conditional_methodology_to_message_then_verify_full_coverage"})
            rebuilt.message="\n".join(filter(None,[rebuilt.message,repaired.limitation]))
            removed.add(i)
            continue
        allowed={b.evidence_id:catalog[b.evidence_id] for b in focused}
        service._resolve_citation_spans([repaired.claim],allowed)
        service._validate_claims([repaired.claim],focused)
        from .task_scope import attach_task_scope
        attach_task_scope(service,repaired.claim,evidence,catalog,payload.get("required_training_tasks",{}),audit,
            {"stage":"isolated_repair","source_attempt":len(audit["attempts"]),"claim_index":i})
        records.append({"claim_index":i,"original_claim_id":service._review_claim_identity(claim,evidence)["claim_id"],
            "repaired_claim_id":service._review_claim_identity(repaired.claim,evidence)["claim_id"],
            "repair_method":"literal_supported_clause_projection" if projected is not None else "model_local_rewrite",
            "rejection_reason":review["reason"],"focused_evidence_ids":[b.evidence_id for b in focused],
            "policy":"replace_rejected_card_only_then_reaudit; supported_cards_and_plans_unchanged"})
        claim.text=repaired.claim.text;claim.citations=repaired.claim.citations
        if repaired.limitation:
            rebuilt.message="\n".join(filter(None,[rebuilt.message,repaired.limitation]))
    if removed:
        if hasattr(rebuilt,"claims"):
            rebuilt.claims=[c for i,c in enumerate(rebuilt.claims) if i not in removed]
        else:
            offset=0
            for section in rebuilt.sections:
                count=len(section.claims)
                section.claims=[c for i,c in enumerate(section.claims,offset) if i not in removed]
                offset+=count
    final=(rebuilt.claims if hasattr(rebuilt,"claims") else [c for s in rebuilt.sections for c in s.claims])
    final_ids=[service._review_claim_identity(c,evidence)["claim_id"] for c in final]
    for record in records[first_record:]:
        target=record["repaired_claim_id"]
        record.update(source_attempt=len(audit["attempts"]),
            index_space="claim_index is zero-based in source_attempt.claims; result_claim_index is zero-based after local repairs",
            result_claim_index=final_ids.index(target) if target is not None else None)
    return type(result).model_validate(rebuilt.model_dump())
