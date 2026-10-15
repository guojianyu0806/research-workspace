"""Missing review facts need new evidence and checks, not just gap acknowledgements."""
import asyncio

import pytest
from pydantic import ValidationError

from research_workspace.intelligence import (
    ClaimSupportPayload, Evidence, IntelligenceService, ModelOutputError,
    ReviewClaimCoveragePayload, ReviewCompletionPayload, ReviewCoveragePayload, SynthesisPayload,
)
from research_workspace.library import Library
from research_workspace.review_completion import complete_review


def source(identifier, text):
    return Evidence(evidence_id=f"E_{identifier}", document_id="doc", document_title="ORION",
                    filename="orion.pdf", run_id="frozen", block_id=identifier, page=1,
                    bbox=[0.1, 0.1, 0.9, 0.3], text=text)


SOURCES = [source("architecture", "ORION models use attention."),
           source("objective", "ORION pre-training predicts masked words."),
           source("foreign", "NOVA predicts the next sentence.").model_copy(update={"document_id": "other"})]
GAP = "ORION的training objective未由当前结论完整回答。"


def addition(*, text="ORION 的预训练目标是预测被遮盖的词。", evidence="E_objective", aspect="training objective"):
    return {"document_id": "doc", "aspect": aspect, "claim": {
        "text": text, "citations": [{"evidence_id": evidence, "quote_id": "Q1"}]}}


class Provider:
    configured = True

    def __init__(self, additions):
        self.additions = iter(additions)
        self.calls = []

    async def generate(self, prompt, payload, model):
        self.calls.append((model, payload))
        if model is SynthesisPayload:
            return SynthesisPayload(status="generated", sections=[{"heading": "architecture", "claims": [{
                "text": SOURCES[0].text, "citations": [{"evidence_id": "E_architecture", "quote_id": "Q1"}]}]}], message=GAP)
        if model is ReviewCompletionPayload:
            return ReviewCompletionPayload(additions=next(self.additions))
        if model is ClaimSupportPayload:
            wrong = "99" in payload["claim"]["text"]
            return ClaimSupportPayload(verdict="unsupported" if wrong else "supported",
                                       reason="Wrong quantity." if wrong else "Explicit own model and prediction target.")
        if model is ReviewClaimCoveragePayload:
            current = payload["claim"]
            aspect = "architecture" if "attention" in current["text"] else "training objective"
            return ReviewClaimCoveragePayload(answers=[{"document_id": "doc", "aspect": aspect,
                "subject_citation_id": "R1", "answer_citation_id": "R1", "reason": "Own quotation names ORION and its property."}])
        assert model is ReviewCoveragePayload
        return ReviewCoveragePayload(requested_aspects=["architecture", "training objective"],
            assessments=[{"document_id": "doc", "aspect": aspect,
                "answer_claim_indices": [claim["claim_index"] for claim in payload["claims"]
                    if ("attention" in claim["text"]) == (aspect == "architecture")],
                "gap_quote": GAP if GAP in payload["response_message"] and aspect == "training objective" else "",
                "gap_is_appropriate": GAP in payload["response_message"] and aspect == "training objective",
                "reason": "Fixture dimension coverage."} for aspect in ["architecture", "training objective"]],
            unverified_statements=[])


def run(tmp_path, provider, *, cancel=None):
    async def scenario():
        service = IntelligenceService(Library(tmp_path), provider)
        state = {}
        result, _, first_audit = await service._generate_grounded("fixture", {
            "task": "review", "instruction": "architecture and training objectives"}, SynthesisPayload,
            SOURCES[:1], required_documents=["doc"], shared_state=state)
        first_identity = first_audit["review_coverage"][-1]["claim_catalog"][0]["claim_id"]
        completed, evidence, audit = await complete_review(service, result, SOURCES[:1],
            {"_snapshot_blocks": SOURCES, "queries": []}, ["doc"], "architecture and training objectives",
            state, check_cancel=cancel)
        return completed, evidence, audit, first_identity
    return asyncio.run(scenario())


def test_missing_objective_is_retrieved_appended_and_audited_without_rewriting_sound_fact(tmp_path):
    provider = Provider([[addition()]])
    result, evidence, audit, original = run(tmp_path, provider)
    assert result.message == ""
    assert len([c for section in result.sections for c in section.claims]) == 2
    assert any(block.block_id == "objective" for block in evidence)
    final = audit["review_coverage"][-1]
    assert final["claim_catalog"][0]["claim_id"] == original
    assert final["assessments"][1]["answer_claim_indices"] == [1]
    assert audit["review_completion"]["status"] == "complete"
    assert audit["review_completion"]["answered_pairs"] == 2
    assert audit["review_completion"]["rounds"][0]["retained_claims_unchanged"]
    assert sum(model is SynthesisPayload for model, _ in provider.calls) == 1
    assert sum(model is ReviewCompletionPayload for model, _ in provider.calls) == 1
    assert len(audit["attempts"]) == 2
    assert audit["coverage_link_cache_hits"] == 1


def test_unsupported_addition_is_filtered_then_actual_new_fact_is_required(tmp_path):
    provider = Provider([[addition(text="ORION predicts 99 masked words.")], [addition()]])
    result, _, audit, original = run(tmp_path, provider)
    texts = [c.text for section in result.sections for c in section.claims]
    assert not any("99" in text for text in texts)
    assert audit["review_completion"]["model_calls"] == 2
    assert len(audit["attempts"]) == 4
    assert [attempt["attempt"] for attempt in audit["attempts"]] == [1, 2, 3, 4]
    assert audit["review_coverage"][-1]["claim_catalog"][0]["claim_id"] == original
    assert audit["claim_model_calls"] == 2
    completion_requests = [payload for model, payload in provider.calls if model is ReviewCompletionPayload]
    feedback = completion_requests[1]["previous_rejected_fact_feedback"]
    assert any("Wrong quantity" in problem for item in feedback for problem in item["problems"])
    assert any("99" in claim["text"] for item in feedback for claim in item["rejected_claims"])
    assert all(claim["citations"] for item in feedback for claim in item["rejected_claims"])


def test_gap_only_result_cannot_be_reported_as_complete(tmp_path):
    provider = Provider([[]])
    with pytest.raises(ModelOutputError, match="未将局部结果标作完整综述"):
        run(tmp_path, provider)


def test_two_failed_additions_do_not_turn_gap_notice_into_completion(tmp_path):
    provider = Provider([[addition(text="ORION predicts 99 masked words.")],
                         [addition(text="ORION predicts 99 masked words.")]])
    with pytest.raises(ModelOutputError, match="未将缺口说明计作完整综述"):
        run(tmp_path, provider)
    assert sum(model is ReviewCompletionPayload for model, _ in provider.calls) == 2


def test_public_review_also_requires_completing_partial_result(tmp_path, monkeypatch):
    service = IntelligenceService(Library(tmp_path), Provider([[]]))
    async def retrieve(*args, **kwargs):
        return {"evidence": SOURCES[:1], "trace": [], "queries": [],
                "_snapshot_blocks": SOURCES, "_opening_candidates": SOURCES[:1]}
    monkeypatch.setattr(service, "retrieve_for_question", retrieve)
    with pytest.raises(ModelOutputError, match="未将局部结果标作完整综述") as caught:
        asyncio.run(service.synthesize("review", ["doc"], instruction="architecture and training objectives"))
    diagnostic = caught.value.review_diagnostics
    assert diagnostic["status"] == "validation_failed" and diagnostic["diagnostic_only"]
    assert diagnostic["grounding"]["review_completion"]["rounds"]
    assert diagnostic["source_snapshot"] and "sections" not in diagnostic


def test_failed_review_audit_is_saved_without_an_answer_or_completed_progress(tmp_path):
    from research_workspace.jobs import JobStore
    error = ModelOutputError("Incomplete review fixture.")
    error.review_diagnostics = {"status": "validation_failed", "diagnostic_only": True,
                                "grounding": {"attempts": [{"problems": ["Missing objective."]}]}}
    class FailedService:
        async def synthesize(self, *args, **kwargs):
            raise error
    store = JobStore(Library(tmp_path))
    job = store.create("review", {"document_ids": ["doc"]})
    asyncio.run(store.process_next(FailedService()))
    failed = store.get(job["id"])
    assert failed["status"] == "failed" and failed["result"] is None and failed["progress"] == 0
    assert not store.summary(job["id"])["has_result"]
    assert failed["completed_items"] == [{"stage": "review_validation", "result": error.review_diagnostics}]


def test_unrelated_exception_does_not_get_saved_as_review_diagnostics(tmp_path):
    from research_workspace.jobs import JobStore
    error = RuntimeError("private transport detail")
    error.review_diagnostics = {"untrusted": "not an audited review failure"}
    class FailedService:
        async def synthesize(self, *args, **kwargs):
            raise error
    store = JobStore(Library(tmp_path))
    job = store.create("review", {"document_ids": ["doc"]})
    asyncio.run(store.process_next(FailedService()))
    failed = store.get(job["id"])
    assert failed["completed_items"] == [] and "private transport" not in failed["error"]


@pytest.mark.parametrize("value", [
    addition(evidence="E_foreign"),
    addition(evidence="E_missing"),
    addition(aspect="invented dimension"),
])
def test_completion_cannot_borrow_document_or_invent_source_or_dimension(tmp_path, value):
    provider = Provider([[value]])
    with pytest.raises(ModelOutputError):
        run(tmp_path, provider)


def test_completion_cannot_duplicate_a_missing_pair(tmp_path):
    with pytest.raises(ModelOutputError, match="无效或重复"):
        run(tmp_path, Provider([[addition(), addition()]]))


def test_completion_output_schema_cannot_replace_frozen_sections():
    with pytest.raises(ValidationError):
        ReviewCompletionPayload.model_validate({"additions": [], "sections": []})


def test_cancellation_happens_before_paid_completion(tmp_path):
    class Cancelled(Exception):
        pass
    def cancel():
        raise Cancelled()
    provider = Provider([[addition()]])
    with pytest.raises(Cancelled):
        run(tmp_path, provider, cancel=cancel)
    assert not any(model is ReviewCompletionPayload for model, _ in provider.calls)
