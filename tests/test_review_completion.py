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


def run(tmp_path, provider, *, cancel=None, initial_evidence=None):
    async def scenario():
        service = IntelligenceService(Library(tmp_path), provider)
        state = {}
        result, _, first_audit = await service._generate_grounded("fixture", {
            "task": "review", "instruction": "architecture and training objectives"}, SynthesisPayload,
            initial_evidence or SOURCES[:1], required_documents=["doc"], shared_state=state)
        first_identity = first_audit["review_coverage"][-1]["claim_catalog"][0]["claim_id"]
        completed, evidence, audit = await complete_review(service, result, initial_evidence or SOURCES[:1],
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
    synthesis_request = next(payload for model, payload in service.provider.calls if model is SynthesisPayload)
    assert synthesis_request["dimension_reading_sets"]
    for reading_set in synthesis_request["dimension_reading_sets"]:
        assert reading_set["document_id"] == "doc"
        assert set(reading_set["preferred_evidence_ids"]) <= {"E_architecture", "E_objective"}


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
    provider = Provider([[value], [value]])
    with pytest.raises(ModelOutputError):
        run(tmp_path, provider)


def test_completion_cannot_duplicate_a_missing_pair(tmp_path):
    with pytest.raises(ModelOutputError, match="无效或重复"):
        run(tmp_path, Provider([[addition(), addition()]]))


def test_completion_output_schema_cannot_replace_frozen_sections():
    with pytest.raises(ValidationError):
        ReviewCompletionPayload.model_validate({"additions": [], "sections": []})


def test_missing_literal_subject_gets_focused_citation_repair_before_fact_audit(tmp_path, monkeypatch):
    import sys
    anonymous = source("anonymous", "The pre-training task predicts masked words.")
    monkeypatch.setattr(sys.modules[__name__], "SOURCES", SOURCES + [anonymous])
    provider = Provider([[addition(evidence="E_anonymous")], [addition()]])
    result, _, audit, _ = run(tmp_path, provider)
    calls = [payload for model, payload in provider.calls if model is ReviewCompletionPayload]
    assert len(calls) == 2
    assert all(len(payload["missing_pairs"]) == 1 for payload in calls)
    assert calls[1]["citation_correction"]["missing_subject_identifiers"] == ["orion"]
    assert calls[1]["own_named_passages"]
    assert audit["review_completion"]["status"] == "complete"
    assert audit["claim_model_calls"] == 1  # The invalid literal attribution was repaired before semantic spending.
    assert result.sections[-1].claims[0].citations[0].evidence_id == "E_objective"


def test_invalid_span_gets_one_explicit_protocol_repair_without_fabricating_quote(tmp_path):
    invalid = addition()
    invalid["claim"]["citations"][0]["quote_id"] = "Q99"
    provider = Provider([[invalid], [addition()]])
    result, _, audit, _ = run(tmp_path, provider)
    requests = [payload for model, payload in provider.calls if model is ReviewCompletionPayload]
    assert len(requests) == 2 and requests[1]["citation_correction"]["preflight_problems"]
    assert result.sections[-1].claims[0].citations[0].quote == SOURCES[1].text
    assert result.sections[-1].claims[0].citations[0].quote_id == "Q1"
    assert audit["claim_model_calls"] == 1


def test_missing_scope_is_repaired_before_spending_a_completion_round(tmp_path, monkeypatch):
    import sys
    scoped = source("scoped", "ORION pre-training predicts English masked words.")
    monkeypatch.setattr(sys.modules[__name__], "SOURCES", SOURCES + [scoped])
    text = "ORION的预训练目标是预测被遮蔽的英语词。"
    provider = Provider([[addition(text=text)], [addition(text=text, evidence="E_scoped")]])
    result, _, audit, _ = run(tmp_path, provider)
    requests = [payload for model, payload in provider.calls if model is ReviewCompletionPayload]
    assert any("English language" in p for p in requests[-1]["citation_correction"]["preflight_problems"])
    assert audit["claim_model_calls"] == 1 and len(audit["review_completion"]["rounds"]) == 1
    assert result.sections[-1].claims[0].citations[0].evidence_id == "E_scoped"


def test_corpus_role_repair_keeps_real_data_without_adding_unquoted_knowledge_interpretation(tmp_path, monkeypatch):
    import sys
    corpus = source("objective", "ORION is trained on BookArchive.")
    monkeypatch.setattr(sys.modules[__name__], "SOURCES", [SOURCES[0], corpus])
    class CorpusProvider(Provider):
        async def generate(self, prompt, payload, model):
            if model is ReviewCoveragePayload:
                return ReviewCoveragePayload(requested_aspects=["architecture", "corpus"], assessments=[{
                    "document_id":"doc", "aspect":aspect,
                    "answer_claim_indices":[c["claim_index"] for c in payload["claims"]
                        if ("attention" in c["text"]) == (aspect == "architecture")],
                    "gap_quote":"", "gap_is_appropriate":False, "reason":"Check corpus's actual training role."}
                    for aspect in ["architecture", "corpus"]], unverified_statements=[])
            if model is ReviewClaimCoveragePayload:
                return ReviewClaimCoveragePayload(answers=[{"document_id":"doc",
                    "aspect":"architecture" if "attention" in payload["claim"]["text"] else "corpus",
                    "subject_citation_id":"R1", "answer_citation_id":"R1", "reason":"Actual own corpus role."}])
            return await super().generate(prompt, payload, model)
    provider = CorpusProvider([[addition(text="ORION使用BookArchive训练，这些语料作为外部知识来源。", aspect="corpus")],
                               [addition(text="ORION使用BookArchive训练。", aspect="corpus")]])
    result, _, audit, _ = run(tmp_path, provider)
    requests = [payload for model, payload in provider.calls if model is ReviewCompletionPayload]
    assert any("knowledge-source interpretation" in p for p in requests[-1]["citation_correction"]["preflight_problems"])
    assert result.sections[-1].claims[0].text == "ORION使用BookArchive训练。"
    assert audit["claim_model_calls"] == 1 and len(audit["review_completion"]["rounds"]) == 1


def test_uncited_initial_hits_do_not_exhaust_completion_source_budget(tmp_path):
    unused = [source(f"irrelevant-{i}", f"Copper sensors measured temperature sample {i}.") for i in range(95)]
    provider = Provider([[addition()]])
    result, evidence, audit, original = run(tmp_path, provider, initial_evidence=SOURCES[:1] + unused)
    assert result.status == "generated" and len(evidence) < 96
    round_search = audit["review_completion"]["rounds"][0]["retrieval"]
    assert len(round_search["retired_uncited_block_ids"]) == 95
    assert "objective" in round_search["added_block_ids"]
    assert audit["review_coverage"][-1]["claim_catalog"][0]["claim_id"] == original


def test_one_new_fact_can_answer_two_dimensions_only_after_independent_link_checks(tmp_path, monkeypatch):
    import sys
    details = source("objective", "ORION pre-trains on BookArchive to predict masked words.")
    monkeypatch.setattr(sys.modules[__name__], "SOURCES", [SOURCES[0], details])
    text = "ORION 使用 BookArchive 预训练以预测被遮盖的词。"
    class SharedFactProvider(Provider):
        async def generate(self, prompt, payload, model):
            if model is ReviewCoveragePayload:
                return ReviewCoveragePayload(requested_aspects=["architecture", "training objective", "corpus"],
                    assessments=[{"document_id": "doc", "aspect": aspect,
                        "answer_claim_indices": [c["claim_index"] for c in payload["claims"]
                            if ("attention" in c["text"]) == (aspect == "architecture")],
                        "gap_quote": "", "gap_is_appropriate": False, "reason": "Check this actual prediction and corpus relation."}
                        for aspect in ["architecture", "training objective", "corpus"]], unverified_statements=[])
            if model is ReviewClaimCoveragePayload:
                aspects = (["architecture"] if "attention" in payload["claim"]["text"] else
                           ["training objective", "corpus"])
                return ReviewClaimCoveragePayload(answers=[{"document_id": "doc", "aspect": aspect,
                    "subject_citation_id": "R1", "answer_citation_id": "R1",
                    "reason": "ORION, the training corpus and prediction objective are in this same quotation."} for aspect in aspects])
            return await super().generate(prompt, payload, model)
    provider = SharedFactProvider([[addition(text=text)], [addition(text=text, aspect="corpus")]])
    result, _, audit, _ = run(tmp_path, provider)
    assert len([claim for section in result.sections for claim in section.claims]) == 2
    final = audit["review_coverage"][-1]
    assert final["assessments"][1]["answer_claim_indices"] == [1]
    assert final["assessments"][2]["answer_claim_indices"] == [1]
    assert audit["review_completion"]["rounds"][0]["shared_new_candidates"]
    assert audit["review_completion"]["answered_pairs"] == 3


def test_cancellation_happens_before_paid_completion(tmp_path):
    class Cancelled(Exception):
        pass
    def cancel():
        raise Cancelled()
    provider = Provider([[addition()]])
    with pytest.raises(Cancelled):
        run(tmp_path, provider, cancel=cancel)
    assert not any(model is ReviewCompletionPayload for model, _ in provider.calls)
