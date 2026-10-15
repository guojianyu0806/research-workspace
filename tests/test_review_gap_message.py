"""Deterministic limitations describe retained answers, not what a paper lacks."""
from copy import deepcopy
import asyncio

import pytest

from research_workspace.intelligence import (
    Claim, Evidence, IntelligenceService, ReviewClaimCoveragePayload, ReviewCoveragePayload,
)
from research_workspace.library import Library


DOCUMENTS = ["doc-a", "doc-b"]
EVIDENCE = [Evidence(evidence_id=f"E_{name}", document_id=f"doc-{name}", document_title=title,
                    filename=f"{name}.pdf", run_id="frozen", block_id=name, page=1,
                    bbox=[0.1, 0.1, 0.9, 0.3], text=f"{title} describes part of its training procedure.")
            for name, title in (("a", "ORION"), ("b", "NOVA"))]


def verified_record():
    catalog = [{"claim_index": index, "original_claim_index": index, "claim_id": f"C_{index}",
                "text": "A cited partial training procedure does not by itself answer the full objective."}
               for index in range(2)]
    links = [{"claim_index": index, "claim_id": f"C_{index}", "problems": [], "answers": [{
        "document_id": document, "aspect": "structure", "subject_citation_id": "R1", "answer_citation_id": "R1",
        "reason": "This retained claim independently answers the named model structure.",
    }]} for index, document in enumerate(DOCUMENTS)]
    rows = [{"document_id": document, "aspect": aspect,
             "answer_claim_indices": [index] if aspect == "structure" else [],
             "answer_claim_ids": [f"C_{index}"] if aspect == "structure" else [],
             "gap_quote": "", "gap_is_appropriate": False, "reason": "Verified retained-answer coverage."}
            for index, document in enumerate(DOCUMENTS) for aspect in ("structure", "training objective")]
    return {"requested_aspects": ["structure", "training objective"], "assessments": rows,
            "claim_catalog": catalog, "claim_link_reviews": links, "unverified_statements": []}


def test_gaps_name_only_unanswered_dimensions_without_claiming_absent_source_information():
    message = IntelligenceService._review_gap_message(verified_record(), DOCUMENTS, EVIDENCE)
    assert message == ("以下维度尚未由本次保留的结论形成独立完整回答（仅限当前结果与所用摘录）："
                       "《ORION》：training objective；《NOVA》：training objective。")
    assert "structure" not in message
    assert "没有" not in message and "未独立支持" not in message


def test_no_missing_dimension_produces_an_empty_message():
    record = verified_record()
    record["requested_aspects"] = ["structure"]
    record["assessments"] = [row for row in record["assessments"] if row["aspect"] == "structure"]
    assert IntelligenceService._review_gap_message(record, DOCUMENTS, EVIDENCE) == ""


def test_multiple_gap_dimensions_are_stable_when_assessments_and_aspects_are_reordered():
    record = verified_record()
    record["requested_aspects"].append("external knowledge")
    for document in DOCUMENTS:
        record["assessments"].append({
            "document_id": document, "aspect": "external knowledge", "answer_claim_indices": [],
            "answer_claim_ids": [], "gap_quote": "", "gap_is_appropriate": False,
            "reason": "This dimension is not independently answered by the retained claims.",
        })
    expected = IntelligenceService._review_gap_message(record, DOCUMENTS, EVIDENCE)
    assert "《ORION》：external knowledge、training objective" in expected
    assert "《NOVA》：external knowledge、training objective" in expected
    reordered = deepcopy(record)
    reordered["requested_aspects"].reverse()
    reordered["assessments"].reverse()
    assert IntelligenceService._review_gap_message(reordered, DOCUMENTS, EVIDENCE) == expected


@pytest.mark.parametrize("damage", [
    "missing_row", "duplicate_row", "duplicate_aspect", "missing_link_review", "invalid_link",
    "catalog_identity", "catalog_index", "wrong_answer_index", "wrong_answer_id", "rejected_status",
])
def test_unverified_or_inconsistent_records_cannot_generate_a_gap_message(damage):
    record = verified_record()
    if damage == "missing_row":
        record["assessments"].pop()
    elif damage == "duplicate_row":
        record["assessments"].append(deepcopy(record["assessments"][0]))
    elif damage == "duplicate_aspect":
        record["requested_aspects"].append("structure")
    elif damage == "missing_link_review":
        record["claim_link_reviews"].pop()
    elif damage == "invalid_link":
        record["claim_link_reviews"][0]["problems"] = ["Unknown citation ID."]
    elif damage == "catalog_identity":
        record["claim_catalog"][0]["claim_id"] = "C_other"
    elif damage == "catalog_index":
        record["claim_catalog"][0]["claim_index"] = 1
    elif damage == "wrong_answer_index":
        record["assessments"][0]["answer_claim_indices"] = [1]
    elif damage == "wrong_answer_id":
        record["assessments"][0]["answer_claim_ids"] = ["C_other"]
    else:
        record["status"] = "rejected_dimension_change"
    assert IntelligenceService._review_gap_message(record, DOCUMENTS, EVIDENCE) is None


def resolve_program_gap(tmp_path, *, damage=None, message=None, unverified=None):
    values = [Claim(text=item.text, citations=[{"evidence_id": item.evidence_id, "quote": item.text}])
              for item in EVIDENCE]
    proposal = ReviewCoveragePayload(requested_aspects=["structure", "training objective"], assessments=[{
        "document_id": document, "aspect": aspect, "answer_claim_indices": [],
        "gap_quote": "The model incorrectly claims the answered structure is absent." if aspect == "structure" else "",
        "gap_is_appropriate": False, "reason": "The message names the gap, but this fixture marks it false.",
    } for document in DOCUMENTS for aspect in ("structure", "training objective")],
        unverified_statements=unverified or [])
    if damage == "invalid_matrix":
        proposal.assessments.pop()

    class Provider:
        configured = True

        async def generate(self, prompt, payload, output_model):
            assert output_model is ReviewClaimCoveragePayload
            citation = payload["claim"]["citations"][0]
            return ReviewClaimCoveragePayload(answers=[{
                "document_id": citation["document_id"], "aspect": "structure",
                "subject_citation_id": "R99" if damage == "invalid_link" else citation["citation_id"],
                "answer_citation_id": citation["citation_id"], "reason": "Fixture independently linked structure.",
            }])

    message = message if message is not None else IntelligenceService._review_gap_message(verified_record(), DOCUMENTS, EVIDENCE)
    service = IntelligenceService(Library(tmp_path), Provider())
    resolved, record, problems = asyncio.run(service._resolve_review_coverage(proposal, values, EVIDENCE,
        DOCUMENTS, "Compare structures and training objectives.", [0, 1], {},
        {"coverage_link_model_calls": 0, "coverage_link_cache_hits": 0}, generated_gap_message=message))
    return resolved, record, problems, message, values


def test_matching_program_message_uses_verified_gaps_and_clears_answered_row_gap_flags(tmp_path):
    resolved, record, problems, message, values = resolve_program_gap(tmp_path)
    assert not problems
    assert record["gap_observation_source"] == "program_generated_verified_coverage"
    assert not IntelligenceService._review_coverage_problems(resolved, values, EVIDENCE, DOCUMENTS, message)
    for row in resolved.assessments:
        if row.aspect == "structure":
            assert row.answer_claim_indices and row.gap_quote == "" and not row.gap_is_appropriate
        else:
            assert not row.answer_claim_indices and row.gap_is_appropriate and row.gap_quote in message
    proposals = record["proposed_assessments"]
    assert all(not row["gap_is_appropriate"] for row in proposals)
    assert all(row["gap_quote"] for row in proposals if row["aspect"] == "structure")
    assert all(row["reason"] == "The message names the gap, but this fixture marks it false." for row in proposals)


def test_a_different_program_message_cannot_receive_verified_gap_observations(tmp_path):
    message = IntelligenceService._review_gap_message(verified_record(), DOCUMENTS, EVIDENCE) + " Additional unsupported facts."
    _, record, problems, _, _ = resolve_program_gap(tmp_path, message=message)
    assert any("does not match" in problem for problem in problems)
    assert "gap_observation_source" not in record


@pytest.mark.parametrize("damage", ["invalid_link", "invalid_matrix"])
def test_program_gap_marker_cannot_bypass_invalid_fresh_coverage(tmp_path, damage):
    _, record, problems, _, _ = resolve_program_gap(tmp_path, damage=damage)
    assert problems
    assert any("does not match" in problem for problem in problems)
    assert "gap_observation_source" not in record


def test_correct_program_gap_does_not_waive_unverified_facts(tmp_path):
    resolved, record, problems, message, values = resolve_program_gap(tmp_path, unverified=["An uncited score of 99."])
    assert not problems
    assert record["gap_observation_source"] == "program_generated_verified_coverage"
    assert record["unverified_statements"] == ["An uncited score of 99."]
    failures = IntelligenceService._review_coverage_problems(resolved, values, EVIDENCE, DOCUMENTS, message)
    assert any("Uncited statement" in problem and "99" in problem for problem in failures)
