"""Add missing, independently checked review facts without rewriting sound cards."""
from __future__ import annotations

from .review_retrieval import supplement_review_evidence


async def complete_review(service, result, evidence, retrieval, documents, instruction,
                          shared_state, *, check_cancel=None):
    from .intelligence import (MAX_ISOLATED_CLAIMS, ModelOutputError, ReviewCompletionPayload,
                               Section, SynthesisPayload, _normalized, _prompt)

    audit = shared_state["audit"]
    completion = audit.setdefault("review_completion", {
        "prompt_version": "review-completion-v1.1", "max_rounds": 2, "model_calls": 0,
        "policy": "preserve_verified_claims_add_missing_facts_then_reaudit", "rounds": [],
    })
    for round_number in range(3):
        await service._check_cancel(check_cancel)
        records = audit.get("review_coverage", [])
        record = records[-1] if records else {}
        gap_message = service._review_gap_message(record, documents, evidence)
        if result.status != "generated" or gap_message is None:
            raise ModelOutputError("综述未形成有效的逐项覆盖记录，不能作为完整综述返回。")
        missing = [(row["document_id"], row["aspect"]) for row in record["assessments"]
                   if not row["answer_claim_indices"]]
        if not missing:
            completion.update(status="complete", answered_pairs=len(record["assessments"]), missing_pairs=[])
            return result, evidence, audit
        if round_number == 2:
            raise ModelOutputError(f"已补充检索和原文核验，但仍有{len(missing)}项文献维度没有可靠答案；未将缺口说明计作完整综述。")

        evidence, search = supplement_review_evidence(service, retrieval["_snapshot_blocks"], evidence,
            retrieval["queries"], request=instruction, missing_pairs=missing)
        catalog = service._citation_spans(evidence, omit_trailing_fragment=True)
        frozen = [claim for section in result.sections for claim in section.claims]
        original_ids = [service._review_claim_identity(claim, evidence)["claim_id"] for claim in frozen]
        entry = {"round": round_number + 1, "missing_pairs": [
            {"document_id": doc, "aspect": aspect} for doc, aspect in missing],
            "retained_claim_ids": original_ids, "retrieval": search}
        completion["rounds"].append(entry)
        # Feedback must include why a proposed fact failed, not only the name of
        # the dimension it was supposed to fill. Otherwise retries repeat it.
        failed_feedback = [{"attempt": attempt["attempt"], "problems": attempt["problems"],
                            "rejected_claims": [claim for claim in attempt.get("claims", [])
                                if any(review["claim_index"] == claim["claim_index"]
                                       and review["verdict"] == "unsupported"
                                       for review in attempt["reviews"])]}
                           for attempt in audit["attempts"] if attempt["problems"]][-3:]
        entry["received_feedback_attempts"] = [item["attempt"] for item in failed_feedback]
        await service._check_cancel(check_cancel)
        completion["model_calls"] += 1
        additions = await service.provider.generate(_prompt("review_completion_v1"), {
            "task": "complete_review_missing_dimensions", "instruction": instruction,
            "missing_pairs": entry["missing_pairs"], "requested_aspects": record["requested_aspects"],
            "retained_claims": record["claim_catalog"],
            "evidence": [{**block.model_dump(), "citation_spans": [
                {"quote_id": identifier, "text": text}
                for identifier, text in catalog[block.evidence_id].items()]} for block in evidence],
            "remaining_pairs": completion.get("remaining_pairs", []),
            "previous_rejected_fact_feedback": failed_feedback,
        }, ReviewCompletionPayload)
        await service._check_cancel(check_cancel)
        if not additions.additions:
            raise ModelOutputError(f"补充检索后未获得{len(missing)}项缺口的可靠新结论，未将局部结果标作完整综述。")
        if len(frozen) + len(additions.additions) > MAX_ISOLATED_CLAIMS:
            raise ModelOutputError("补充后综述超过24条独立结论上限，请减少文献或维度。")
        sources = {item.evidence_id: item for item in evidence}
        seen_pairs, identities = set(), set(original_ids)
        rebuilt = result.model_copy(deep=True)
        rebuilt.message = ""
        for addition in additions.additions:
            pair = (addition.document_id, addition.aspect)
            if pair not in missing or pair in seen_pairs:
                raise ModelOutputError("补充结论的文献维度无效或重复，结果已拒绝。")
            seen_pairs.add(pair)
            claim = addition.claim.model_copy(deep=True)
            service._resolve_citation_spans([claim], catalog)
            service._validate_claims([claim], evidence)
            if any(sources[cite.evidence_id].document_id != addition.document_id for cite in claim.citations):
                raise ModelOutputError("补充结论借用了其他文献的引用，结果已拒绝。")
            identity = service._review_claim_identity(claim, evidence)["claim_id"]
            if identity in identities:
                raise ModelOutputError("补充结论重复已有事实，不能据此填充缺口。")
            identities.add(identity)
            section = next((section for section in rebuilt.sections
                            if _normalized(section.heading) == _normalized(addition.aspect)), None)
            if section is None:
                section = Section(heading=addition.aspect)
                rebuilt.sections.append(section)
            section.claims.append(claim)
        entry["proposed_added_claims"] = [item.model_dump() for item in additions.additions]
        result, _, audit = await service._generate_grounded(_prompt("review_synthesis_v1"), {
            "task": "review", "instruction": instruction,
            "external_freshness_verified": False,
        }, SynthesisPayload, evidence, required_documents=documents, check_cancel=check_cancel,
            initial_result=rebuilt, shared_state=shared_state)
        final_ids = [service._review_claim_identity(claim, evidence)["claim_id"]
                     for section in result.sections for claim in section.claims]
        if not set(original_ids).issubset(final_ids):
            raise ModelOutputError("补充流程改变了已核验结论，结果已拒绝。")
        entry.update(retained_claims_unchanged=True, result_claim_ids=final_ids)
        completion["remaining_pairs"] = [
            {"document_id": row["document_id"], "aspect": row["aspect"]}
            for row in audit["review_coverage"][-1]["assessments"] if not row["answer_claim_indices"]]
    raise ModelOutputError("综述补充未完成。")
