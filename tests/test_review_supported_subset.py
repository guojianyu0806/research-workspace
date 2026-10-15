"""A review repair may retain a frozen supported subset, never rewrite its proof."""
import asyncio

import pytest
from pydantic import ValidationError

from research_workspace.intelligence import (
    Claim, ClaimSupportPayload, CoveragePayload, Evidence, IntelligenceService,
    ModelOutputError, ReviewClaimCoveragePayload, ReviewCoveragePayload, ReviewMessageRepairPayload, SynthesisPayload,
)
from research_workspace.library import Library


def evidence(block_id, document_id, text):
    return Evidence(evidence_id=f"E_{block_id}", document_id=document_id, document_title=document_id,
                    filename=f"{document_id}.pdf", run_id=f"run-{document_id}", block_id=block_id,
                    page=1, bbox=[0.1, 0.1, 0.9, 0.3], text=text)


SOURCES = [
    evidence("a", "doc-a", "ORION models use attention and score 28."),
    evidence("b", "doc-b", "NOVA models use convolution and score 30."),
    evidence("a-other", "doc-a", "ORION models use attention."),
]


def claim(text, source_id):
    return Claim(text=text, citations=[{"evidence_id": f"E_{source_id}", "quote_id": "Q1"}])


def good_a():
    return claim("ORION 使用注意力。", "a")


def good_b():
    return claim("NOVA 使用卷积。", "b")


def bad_a():
    return claim("ORION 得分为 99。", "a")


def review(*claims, message=""):
    return SynthesisPayload(status="generated", sections=[{"heading": "结构与得分", "claims": claims}], message=message)


def repair(message="", *, status="generated"):
    return ReviewMessageRepairPayload(status=status, message=message)


class Provider:
    configured = True

    def __init__(self, results, coverage=None):
        self.results = iter(results)
        self.coverage = coverage
        self.calls = []

    async def generate(self, prompt, payload, output_model):
        self.calls.append((payload, output_model))
        if output_model in {SynthesisPayload, ReviewMessageRepairPayload}:
            response = next(self.results)
            assert isinstance(response, output_model)
            return response
        if output_model is ClaimSupportPayload:
            rejected = "99" in payload["claim"]["text"]
            return ClaimSupportPayload(verdict="unsupported" if rejected else "supported",
                                       reason="The quoted score is not 99." if rejected else "Identity and mechanism are quoted.")
        if output_model is ReviewClaimCoveragePayload:
            current = payload["claim"]
            return ReviewClaimCoveragePayload(answers=[{
                "document_id": citation["document_id"], "aspect": aspect,
                "subject_citation_id": citation["citation_id"], "answer_citation_id": citation["citation_id"],
                "reason": "The fixture names its model and structure.",
            } for name in ("ORION", "NOVA") if name in current["text"]
              for citation in current["citations"] if name in citation["quote"]
              for aspect in payload["requested_aspects"] if aspect == "structure"])
        if output_model is ReviewCoveragePayload:
            if self.coverage is not None:
                return self.coverage(payload) if callable(self.coverage) else self.coverage
            source_documents = {source.evidence_id: source.document_id for source in SOURCES}
            return ReviewCoveragePayload(requested_aspects=["structure"], assessments=[{
                "document_id": document_id, "aspect": "structure",
                "answer_claim_indices": [next(item["claim_index"] for item in payload["claims"]
                    if any(source_documents[cite["evidence_id"]] == document_id for cite in item["citations"]))],
                "gap_is_appropriate": False, "reason": "Fixture structural coverage.",
            } for document_id in ("doc-a", "doc-b")], unverified_statements=[])
        assert output_model is CoveragePayload
        return self.coverage or CoveragePayload(unverified_statements=[], missing_aspects=[])


def run(tmp_path, provider, *, task="review", instruction="Compare structures and scores."):
    service = IntelligenceService(Library(tmp_path), provider)
    return asyncio.run(service._generate_grounded("fixture", {"task": task, "instruction": instruction},
                        SynthesisPayload, SOURCES, required_documents=["doc-a", "doc-b"]))


def correction(provider):
    return [payload for payload, output_model in provider.calls
            if output_model in {SynthesisPayload, ReviewMessageRepairPayload}][1]["correction"]


def incomplete_coverage(payload):
    return ReviewCoveragePayload(requested_aspects=["structure"], assessments=[{
        "document_id": "doc-a", "aspect": "structure", "answer_claim_indices": [0],
        "gap_is_appropriate": False, "reason": "Fixture deliberately omits the other selected document.",
    }], unverified_statements=[])


def test_deterministic_repair_reconstructs_supported_claims_and_quotes_without_retyping(tmp_path):
    provider = Provider([review(good_a(), bad_a(), good_b())])
    result, used, audit = run(tmp_path, provider)
    assert [item.text for section in result.sections for item in section.claims] == [good_a().text, good_b().text]
    assert result.sections[0].heading == "结构与得分"
    assert [item.citations[0].quote for item in result.sections[0].claims] == [SOURCES[0].text, SOURCES[1].text]
    assert used == {"E_a", "E_b"}
    subset = audit["review_subset_repair"]
    assert subset["retained_original_indices"] == [0, 2]
    assert subset["removed_original_indices"] == [1]
    assert result.message == ""
    assert sum(model is SynthesisPayload for _, model in provider.calls) == 1
    assert not any(model is ReviewMessageRepairPayload for _, model in provider.calls)
    assert audit["review_subset_repair"]["mode"] == "preserve_supported_review_claims"
    assert audit["review_subset_repair"]["repair_output"] == "deterministic_gap_message"
    assert audit["review_subset_repair"]["reconstructed_by_program"] is True
    assert audit["review_subset_repair"]["message_source"] == "verified_claim_coverage"
    assert "prompt_version" not in audit["review_subset_repair"]
    assert audit["claim_model_calls"] == 3 and audit["cache_hits"] == 2
    assert audit["coverage_model_calls"] == 2 and audit["status"] == "passed"
    assert audit["review_coverage"][0]["original_claim_indices"] == [0, 2]


@pytest.mark.parametrize("field,value", [
    ("claims", [good_a().model_dump(), bad_a().model_dump()]),
    ("sections", [{"heading": "Changed layout", "claims": []}]),
    ("citations", [{"evidence_id": "E_a-other", "quote_id": "Q1"}]),
    ("architecture", {"nodes": [], "edges": []}),
    ("content", "A replacement review or repeated claims."),
])
def test_message_repair_schema_cannot_add_drop_reorder_or_rewrite_facts_and_layout(field, value):
    with pytest.raises(ValidationError):
        ReviewMessageRepairPayload.model_validate({"status": "generated", "message": "", field: value})


def test_subset_does_not_trigger_when_it_would_remove_an_entire_required_document(tmp_path):
    bad_b = claim("NOVA 得分为 99。", "b")
    provider = Provider([review(good_a(), bad_b), review(good_a(), good_b())])
    _, used, audit = run(tmp_path, provider)
    assert used == {"E_a", "E_b"}
    assert "supported_subset" not in correction(provider)
    assert "review_subset_repair" not in audit
    assert audit["coverage_model_calls"] == 1
    assert audit["review_coverage"][0]["attempt"] == 2


def test_invalid_citation_keeps_original_repair_path_instead_of_salvaging(tmp_path):
    invalid = good_a()
    invalid.citations[0].quote_id = "Q99"
    provider = Provider([review(invalid, good_b()), review(good_a(), good_b())])
    _, _, audit = run(tmp_path, provider)
    assert "supported_subset" not in correction(provider)
    assert audit["attempts"][0]["reviews"] == []
    assert "review_subset_repair" not in audit
    assert audit["coverage_model_calls"] == 1
    assert audit["review_coverage"][0]["attempt"] == 2


def test_program_gap_corrects_false_global_acknowledgement_and_preserves_the_proposal(tmp_path):
    provider = Provider([review(good_a(), bad_a(), good_b())],
                        ReviewCoveragePayload(requested_aspects=["training objective"], assessments=[
                            {"document_id": "doc-a", "aspect": "training objective", "answer_claim_indices": [],
                             "gap_is_appropriate": False, "reason": "Requested objective is unacknowledged."},
                            {"document_id": "doc-b", "aspect": "training objective", "answer_claim_indices": [],
                             "gap_is_appropriate": False, "reason": "Requested objective is unacknowledged."},
                        ], unverified_statements=[]))
    result, _, audit = run(tmp_path, provider)
    assert "《doc-a》：training objective" in result.message
    assert "《doc-b》：training objective" in result.message
    final = audit["review_coverage"][-1]
    assert final["gap_observation_source"] == "program_generated_verified_coverage"
    assert all(item["gap_is_appropriate"] and item["gap_quote"] in result.message for item in final["assessments"])
    assert all(not item["gap_is_appropriate"] and item["gap_quote"] == "" for item in final["proposed_assessments"])
    assert all(item["reason"] == "Requested objective is unacknowledged." for item in final["proposed_assessments"])
    assert not any(model is ReviewMessageRepairPayload for _, model in provider.calls)
    assert sum(model is ReviewCoveragePayload for _, model in provider.calls) == 2


def test_other_synthesis_modes_keep_the_original_bounded_repair(tmp_path):
    provider = Provider([review(good_a(), bad_a(), good_b()),
                         review(good_a(), claim("ORION 得分为 28。", "a"), good_b())])
    result, _, audit = run(tmp_path, provider, task="draft")
    assert len(result.sections[0].claims) == 3
    assert "supported_subset" not in correction(provider)
    assert "review_subset_repair" not in audit


def test_repair_can_refuse_without_publishing_a_partial_review(tmp_path):
    provider = Provider([review(good_a(), bad_a(), good_b()),
                         repair("所引片段不足以形成有用综述。", status="insufficient_evidence")], incomplete_coverage)
    result, used, audit = run(tmp_path, provider)
    assert result.status == "insufficient_evidence" and not result.sections and used == set()
    assert audit["status"] == "no_asserted_claims"
    assert "supported_subset" in correction(provider)
    assert audit["review_subset_repair"]["repair_output"] == "message_only"
    assert audit["review_subset_repair"]["prompt_version"] == "review-message-repair-v1.0"
    assert len(correction(provider)["retained_coverage"]["assessments"]) == 1


def test_review_exact_quotations_still_require_the_coverage_matrix(tmp_path):
    direct = [claim(source.text, source.block_id) for source in SOURCES[:2]]
    provider = Provider([review(*direct)])
    _, _, audit = run(tmp_path, provider)
    assert audit["claim_model_calls"] == 0
    assert audit["coverage_model_calls"] == 1
    models = [model for _, model in provider.calls]
    assert models[:2] == [SynthesisPayload, ReviewCoveragePayload]
    assert models[2:] == [ReviewClaimCoveragePayload, ReviewClaimCoveragePayload]


def test_deterministic_gap_uses_only_supported_claims_and_is_fully_rechecked(tmp_path):
    gap_a = "《doc-a》：training objective"
    gap_b = "《doc-b》：training objective"
    coverage_inputs = []

    def check_coverage(payload):
        coverage_inputs.append(payload)
        assessments = []
        for index, document_id in enumerate(("doc-a", "doc-b")):
            assessments.append({"document_id": document_id, "aspect": "structure", "answer_claim_indices": [index],
                                "gap_is_appropriate": False, "reason": "Structure is directly supported."})
        for document_id, gap in (("doc-a", gap_a), ("doc-b", gap_b)):
            exists = gap in payload["response_message"]
            assessments.append({"document_id": document_id, "aspect": "training objective",
                                "gap_quote": gap if exists else "", "gap_is_appropriate": exists,
                                "reason": "A precise training-objective evidence gap must be acknowledged."})
        return ReviewCoveragePayload(requested_aspects=["structure", "training objective"],
                                     assessments=assessments, unverified_statements=[])

    provider = Provider([review(good_a(), bad_a(), good_b())], check_coverage)
    result, _, audit = run(tmp_path, provider, instruction="Compare structures and training objectives.")
    first = coverage_inputs[0]
    assert [item["claim_index"] for item in first["claims"]] == [0, 1]
    assert [item["text"] for item in first["claims"]] == [good_a().text, good_b().text]
    assert all("99" not in item["text"] for item in first["claims"])
    feedback = audit["attempts"][0]["problems"]
    assert any("The quoted score is not 99" in item for item in feedback)
    assert any("Unanswered request aspect" in item and "training objective" in item for item in feedback)
    assert audit["review_subset_repair"]["removed_original_indices"] == [1]
    assert audit["review_coverage"][0]["original_claim_indices"] == [0, 2]
    assert audit["coverage_model_calls"] == 2 and audit["status"] == "passed"
    assert gap_a in result.message and gap_b in result.message
    assert "structure" not in result.message
    assert "尚未由本次保留的结论形成独立完整回答" in result.message
    assert "仅限当前结果与所用摘录" in result.message
    assert coverage_inputs[1]["response_message"] == result.message
    assert not any(model is ReviewMessageRepairPayload for _, model in provider.calls)


def test_coverage_only_deterministic_gap_preserves_every_supported_claim(tmp_path):
    gap_a = "《doc-a》：training objective"
    gap_b = "《doc-b》：training objective"

    def check_coverage(payload):
        return ReviewCoveragePayload(requested_aspects=["training objective"], assessments=[{
            "document_id": document_id, "aspect": "training objective", "answer_claim_indices": [],
            "gap_quote": gap if gap in payload["response_message"] else "",
            "gap_is_appropriate": gap in payload["response_message"],
            "reason": "The requested training objective needs an explicit evidence gap.",
        } for document_id, gap in (("doc-a", gap_a), ("doc-b", gap_b))], unverified_statements=[])

    provider = Provider([review(good_a(), good_b())], check_coverage)
    result, _, audit = run(tmp_path, provider, instruction="Compare structures and training objectives.")
    assert gap_a in result.message and gap_b in result.message
    assert [item.text for item in result.sections[0].claims] == [good_a().text, good_b().text]
    assert audit["coverage_model_calls"] == 2 and audit["claim_model_calls"] == 2
    assert audit["cache_hits"] == 2 and audit["status"] == "passed"
    assert audit["review_subset_repair"]["removed_original_indices"] == []
    subset = audit["review_subset_repair"]
    assert subset["retained_original_indices"] == [0, 1]
    assert subset["removed_original_indices"] == []


def test_message_only_repair_cannot_smuggle_an_uncited_fact_into_the_result(tmp_path):
    rounds = []
    def check_coverage(payload):
        rounds.append(payload)
        if len(rounds) == 1:
            return incomplete_coverage(payload)
        return ReviewCoveragePayload(requested_aspects=["structure"], assessments=[{
            "document_id": document, "aspect": "structure", "answer_claim_indices": [index],
            "gap_is_appropriate": False, "reason": "Structure is quoted.",
        } for index, document in enumerate(("doc-a", "doc-b"))],
            unverified_statements=["Uncited score 99 in the repair message."] if "99" in payload["response_message"] else [])
    provider = Provider([review(good_a(), bad_a(), good_b()), repair("ORION 得分为 99。")], check_coverage)
    with pytest.raises(ModelOutputError, match="Uncited score 99"):
        run(tmp_path, provider)
    assert sum(model is ReviewMessageRepairPayload for _, model in provider.calls) == 1


def test_message_repair_provider_failure_does_not_publish_the_supported_subset(tmp_path):
    class FailingProvider(Provider):
        async def generate(self, prompt, payload, output_model):
            if output_model is ReviewMessageRepairPayload:
                raise RuntimeError("Fixture message repair failure")
            return await super().generate(prompt, payload, output_model)
    provider = FailingProvider([review(good_a(), bad_a(), good_b())], incomplete_coverage)
    with pytest.raises(RuntimeError, match="Fixture message repair failure"):
        run(tmp_path, provider)


@pytest.mark.parametrize("initial_message", ["ORION 得分为 99。", "ORION 的结构未获回答。"])
def test_complete_answers_clear_unverified_or_conflicting_message_without_model_repair(tmp_path, initial_message):
    coverage_inputs = []

    def check_coverage(payload):
        coverage_inputs.append(payload)
        message = payload["response_message"]
        return ReviewCoveragePayload(requested_aspects=["structure"], assessments=[{
            "document_id": document, "aspect": "structure", "answer_claim_indices": [index],
            "gap_quote": message if document == "doc-a" and "未获回答" in message else "",
            "gap_is_appropriate": False, "reason": "Each retained named structure is independently cited.",
        } for index, document in enumerate(("doc-a", "doc-b"))],
            unverified_statements=["Uncited score 99."] if "99" in message else [])

    provider = Provider([review(good_a(), good_b(), message=initial_message)], check_coverage)
    result, _, audit = run(tmp_path, provider)
    assert result.message == ""
    assert [item.text for item in result.sections[0].claims] == [good_a().text, good_b().text]
    assert coverage_inputs[0]["response_message"] == initial_message
    assert coverage_inputs[1]["response_message"] == ""
    assert audit["coverage_model_calls"] == 2 and audit["status"] == "passed"
    assert audit["review_subset_repair"]["repair_output"] == "deterministic_gap_message"
    assert not any(model is ReviewMessageRepairPayload for _, model in provider.calls)


def test_invalid_matrix_requires_fallback_and_cannot_publish_without_rechecking(tmp_path):
    provider = Provider([review(good_a(), good_b()), repair()], incomplete_coverage)
    with pytest.raises(ModelOutputError, match="every selected document"):
        run(tmp_path, provider)
    assert correction(provider)["supported_subset"]["repair_output"] == "message_only"
    assert sum(model is ReviewMessageRepairPayload for _, model in provider.calls) == 1
    assert sum(model is ReviewCoveragePayload for _, model in provider.calls) == 2
