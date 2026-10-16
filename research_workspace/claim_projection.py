"""Recover independently supported literal clauses without inventing replacement text."""
from __future__ import annotations
import re

async def project_supported_clauses(service, claim, evidence, catalog, audit, *, check_cancel=None):
    from .intelligence import ClaimSupportPayload, _prompt
    parts=[part.strip() for part in re.split(r"[；;]",claim.text) if part.strip()]
    if not 2 <= len(parts) <= 4:return None
    names=service._named_attribution_identifiers(catalog)
    retained=[];checks=[]
    record={"original_claim":service._review_claim_identity(claim,evidence),"checks":checks,
        "policy":"literal_semicolon_clauses_only; own_citations_unchanged; final_fact_and_request_coverage_still_required"}
    audit.setdefault("literal_clause_projections",[]).append(record)
    for part in parts:
        # A standalone projected clause must name its own subject. Never infer
        # an omitted subject from a neighbouring clause that might be removed.
        candidate=claim.model_copy(deep=True);candidate.text=part
        if not service._identifier_tokens(part).intersection(names):
            checks.append({"text":part,"status":"not_reviewed_subject_not_explicit"});continue
        if audit.get("literal_projection_model_calls",0)>=4:
            record["stopped_reason"]="four_projection_check_budget_reached";return None
        if (service._missing_named_attribution(candidate,names)
                or service._missing_quantitative_result_scope(candidate,names)):
            record["stopped_reason"]="literal_subject_or_result_scope_guard";return None
        await service._check_cancel(check_cancel)
        verdict=await service.provider.generate(_prompt("claim_support_v1"),{
            "task":"verify_single_claim","claim_index":len(checks),
            "claim":candidate.model_dump(),"response_kind":audit["response_kind"],
        },ClaimSupportPayload)
        await service._check_cancel(check_cancel)
        audit["literal_projection_model_calls"]=audit.get("literal_projection_model_calls",0)+1
        audit["claim_model_calls"]+=1;audit["model_checked"]=True
        checks.append({"claim":service._review_claim_identity(candidate,evidence),**verdict.model_dump()})
        if verdict.verdict=="supported":retained.append(part)
    if not retained or len(retained)==len(parts):return None
    projected=claim.model_copy(deep=True);projected.text="；".join(retained)
    record["projected_claim_id"]=service._review_claim_identity(projected,evidence)["claim_id"]
    return projected
