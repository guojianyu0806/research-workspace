"""A review repair may retain a frozen supported subset, never rewrite its proof."""
import asyncio

import pytest

from research_workspace.intelligence import (
    Claim, ClaimSupportPayload, CoveragePayload, Evidence, IntelligenceService,
    ModelOutputError, ReviewClaimCoveragePayload, ReviewCoveragePayload, SynthesisPayload,
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


class Provider:
    configured = True

    def __init__(self, results, coverage=None):
        self.results = iter(results)
        self.coverage = coverage
        self.calls = []

    async def generate(self, prompt, payload, output_model):
        self.calls.append((payload, output_model))
        if output_model is SynthesisPayload:
            return next(self.results)
        if output_model is ClaimSupportPayload:
            rejected = "99" in payload["claim"]["text"]
            return ClaimSupportPayload(verdict="unsupported" if rejected else "supported",
                                       reason="The quoted score is not 99." if rejected else "Identity and mechanism are quoted.")
        if output_model is ReviewClaimCoveragePayload:
            current = payload["claim"]
            return ReviewClaimCoveragePayload(answers=[{
                "document_id": citation["document_id"], "aspect": aspect,
                "subject_text": name, "subject_quote": name, "answer_text": current["text"],
                "answer_quote": citation["quote"], "reason": "The fixture names its model and structure.",
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
    return [payload for payload, output_model in provider.calls if output_model is SynthesisPayload][1]["correction"]


def test_supported_subset_can_be_reordered_but_keeps_each_claim_and_its_quotes(tmp_path):
    provider = Provider([review(good_a(), bad_a(), good_b()),
                         review(good_b(), good_a(), message="所引片段不足以回答其余请求维度。")])
    result, used, audit = run(tmp_path, provider)
    assert [item.text for section in result.sections for item in section.claims] == [good_b().text, good_a().text]
    assert used == {"E_a", "E_b"}
    subset = correction(provider)["supported_subset"]
    assert subset["retained_original_indices"] == [0, 2]
    assert subset["removed_original_indices"] == [1]
    previous = correction(provider)["previous_result"]["sections"][0]["claims"]
    assert [item["text"] for item in previous] == [good_a().text, good_b().text]
    assert all(not cite["quote"] for item in previous for cite in item["citations"])
    assert audit["review_subset_repair"]["mode"] == "preserve_supported_review_claims"
    assert audit["claim_model_calls"] == 3 and audit["cache_hits"] == 2
    assert audit["coverage_model_calls"] == 2 and audit["status"] == "passed"
    assert audit["review_coverage"][0]["original_claim_indices"] == [0, 2]


@pytest.mark.parametrize("mutation", ["reinstate", "rewrite", "replace_citation", "duplicate", "drop"])
def test_supported_subset_repair_cannot_change_the_claim_multiset(tmp_path, mutation):
    claims = [good_a(), good_b()]
    if mutation == "reinstate":
        # Even a corrected, now supportable replacement for the rejected fact
        # cannot be introduced after the supported subset has been frozen.
        claims.append(claim("ORION 得分为 28。", "a"))
    elif mutation == "rewrite":
        claims[0].text = "ORION 的模型使用注意力。"
    elif mutation == "replace_citation":
        claims[0] = claim(good_a().text, "a-other")
    elif mutation == "duplicate":
        claims.append(good_a())
    else:
        claims.pop(0)
    provider = Provider([review(good_a(), bad_a(), good_b()), review(*claims)])
    with pytest.raises(ModelOutputError, match="完整保留"):
        run(tmp_path, provider)
    assert "supported_subset" in correction(provider)
    assert sum(model is ReviewCoveragePayload for _, model in provider.calls) == 1
    assert all(model is not CoveragePayload for _, model in provider.calls)
    assert sum(model is ClaimSupportPayload for _, model in provider.calls) == 3


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


def test_subset_still_fails_if_coverage_or_message_is_unacceptable(tmp_path):
    provider = Provider([review(good_a(), bad_a(), good_b()), review(good_a(), good_b())],
                        ReviewCoveragePayload(requested_aspects=["training objective"], assessments=[
                            {"document_id": "doc-a", "aspect": "training objective", "answer_claim_indices": [],
                             "gap_is_appropriate": False, "reason": "Requested objective is unacknowledged."},
                            {"document_id": "doc-b", "aspect": "training objective", "answer_claim_indices": [],
                             "gap_is_appropriate": False, "reason": "Requested objective is unacknowledged."},
                        ], unverified_statements=[]))
    with pytest.raises(ModelOutputError, match="Unanswered request aspect"):
        run(tmp_path, provider)
    assert "supported_subset" in correction(provider)
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
                         SynthesisPayload(status="insufficient_evidence", sections=[], message="所引片段不足以形成有用综述。")])
    result, used, audit = run(tmp_path, provider)
    assert result.status == "insufficient_evidence" and not result.sections and used == set()
    assert audit["status"] == "no_asserted_claims"
    assert "supported_subset" in correction(provider)


def test_review_exact_quotations_still_require_the_coverage_matrix(tmp_path):
    direct = [claim(source.text, source.block_id) for source in SOURCES[:2]]
    provider = Provider([review(*direct)])
    _, _, audit = run(tmp_path, provider)
    assert audit["claim_model_calls"] == 0
    assert audit["coverage_model_calls"] == 1
    models = [model for _, model in provider.calls]
    assert models[:2] == [SynthesisPayload, ReviewCoveragePayload]
    assert models[2:] == [ReviewClaimCoveragePayload, ReviewClaimCoveragePayload]


def test_first_repair_receives_coverage_gaps_from_only_supported_claims(tmp_path):
    gap_a = "所给摘录未独立支持 ORION 的训练目标。"
    gap_b = "所给摘录未独立支持 NOVA 的训练目标。"
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

    provider = Provider([review(good_a(), bad_a(), good_b()),
                         review(good_a(), good_b(), message=gap_a + gap_b)], check_coverage)
    result, _, audit = run(tmp_path, provider, instruction="Compare structures and training objectives.")
    first = coverage_inputs[0]
    assert [item["claim_index"] for item in first["claims"]] == [0, 1]
    assert [item["text"] for item in first["claims"]] == [good_a().text, good_b().text]
    assert all("99" not in item["text"] for item in first["claims"])
    feedback = correction(provider)["problems"]
    assert any("The quoted score is not 99" in item for item in feedback)
    assert any("Unanswered request aspect" in item and "training objective" in item for item in feedback)
    assert correction(provider)["supported_subset"]["removed_original_indices"] == [1]
    assert audit["review_coverage"][0]["original_claim_indices"] == [0, 2]
    assert audit["coverage_model_calls"] == 2 and audit["status"] == "passed"
    assert result.message == gap_a + gap_b


@pytest.mark.parametrize("rewrite_claim", [False, True])
def test_coverage_only_repair_freezes_every_supported_claim(tmp_path, rewrite_claim):
    gap_a = "所给摘录未独立支持 ORION 的训练目标。"
    gap_b = "所给摘录未独立支持 NOVA 的训练目标。"

    def check_coverage(payload):
        return ReviewCoveragePayload(requested_aspects=["training objective"], assessments=[{
            "document_id": document_id, "aspect": "training objective", "answer_claim_indices": [],
            "gap_quote": gap if gap in payload["response_message"] else "",
            "gap_is_appropriate": gap in payload["response_message"],
            "reason": "The requested training objective needs an explicit evidence gap.",
        } for document_id, gap in (("doc-a", gap_a), ("doc-b", gap_b))], unverified_statements=[])

    corrected_claims = [good_a(), good_b()]
    if rewrite_claim:
        corrected_claims[0].text = "ORION 的模型使用注意力。"
    provider = Provider([review(good_a(), good_b()), review(*corrected_claims, message=gap_a + gap_b)], check_coverage)
    if rewrite_claim:
        with pytest.raises(ModelOutputError, match="完整保留"):
            run(tmp_path, provider, instruction="Compare structures and training objectives.")
        assert sum(model is ReviewCoveragePayload for _, model in provider.calls) == 1
    else:
        result, _, audit = run(tmp_path, provider, instruction="Compare structures and training objectives.")
        assert result.message == gap_a + gap_b
        assert audit["coverage_model_calls"] == 2 and audit["claim_model_calls"] == 2
        assert audit["cache_hits"] == 2 and audit["status"] == "passed"
        assert audit["review_subset_repair"]["removed_original_indices"] == []
    subset = correction(provider)["supported_subset"]
    assert subset["retained_original_indices"] == [0, 1]
    assert subset["removed_original_indices"] == []
