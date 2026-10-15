"""Review coverage is a complete matrix of cited answers OR precise valid gaps."""
import pytest

from research_workspace.intelligence import (
    Claim, Evidence, IntelligenceService, ReviewCoverageAspect, ReviewCoveragePayload,
)


DOCUMENTS = ["doc-a", "doc-b"]
EVIDENCE = [Evidence(evidence_id=f"E_{name}", document_id=f"doc-{name}", document_title=name,
                    filename=f"{name}.pdf", run_id=f"run-{name}", block_id=name, page=1,
                    bbox=[0.1, 0.1, 0.9, 0.3], text=f"Model {name} uses attention.") for name in ("a", "b")]
CLAIMS = [Claim(text=f"模型{name}使用注意力。", citations=[{"evidence_id": f"E_{name}", "quote": source.text}])
          for name, source in zip(("a", "b"), EVIDENCE)]


def assessment(document_id, aspect="structure", indices=(), gap="", appropriate=False):
    return ReviewCoverageAspect(document_id=document_id, aspect=aspect, answer_claim_indices=list(indices),
                                gap_quote=gap, gap_is_appropriate=appropriate, reason="Fixture assessment.")


def coverage(*assessments, aspects=("structure",), unverified=()):
    return ReviewCoveragePayload(requested_aspects=list(aspects), assessments=list(assessments),
                                 unverified_statements=list(unverified))


def problems(result, message=""):
    return IntelligenceService._review_coverage_problems(result, CLAIMS, EVIDENCE, DOCUMENTS, message)


def valid_answers():
    return [assessment("doc-a", indices=[0]), assessment("doc-b", indices=[1])]


def test_same_document_claims_cover_the_complete_matrix():
    assert problems(coverage(*valid_answers())) == []


def test_precise_valid_message_acknowledgement_is_an_alternative_to_answer_content():
    gap_a = "所给摘录未独立支持模型A的训练目标。"
    gap_b = "所给摘录未独立支持模型B的训练目标。"
    result = coverage(*valid_answers(), assessment("doc-a", "training", gap=gap_a, appropriate=True),
                      assessment("doc-b", "training", gap=gap_b, appropriate=True), aspects=("structure", "training"))
    # Acknowledged dimensions need no answer claim: this is the real regression
    # that a model previously called missing merely because claims omitted them.
    assert problems(result, gap_a + "\n" + gap_b) == []


@pytest.mark.parametrize("case", ["missing", "duplicate", "extra_document", "extra_aspect"])
def test_matrix_cannot_omit_duplicate_or_add_document_dimension_entries(case):
    assessments = valid_answers()
    if case == "missing":
        assessments.pop()
    elif case == "duplicate":
        assessments.append(assessment("doc-a", indices=[0]))
    elif case == "extra_document":
        assessments.append(assessment("doc-other", indices=[0]))
    else:
        assessments.append(assessment("doc-a", "unrequested", indices=[0]))
    assert problems(coverage(*assessments))


@pytest.mark.parametrize("aspects", [("structure", "structure"), ("",)])
def test_requested_dimension_names_must_be_nonempty_and_distinct(aspects):
    assert problems(coverage(*valid_answers(), aspects=aspects))


def test_one_document_cannot_borrow_a_different_documents_claim_for_coverage():
    result = coverage(assessment("doc-a", indices=[1]), assessment("doc-b", indices=[1]))
    assert problems(result)


def test_even_one_foreign_claim_index_invalidates_a_document_answer_list():
    result = coverage(assessment("doc-a", indices=[0, 1]), assessment("doc-b", indices=[1]))
    assert problems(result)


def test_out_of_range_answer_claim_indices_cannot_be_approved():
    result = coverage(assessment("doc-a", indices=[len(CLAIMS)]), assessment("doc-b", indices=[1]))
    assert problems(result)


@pytest.mark.parametrize("indices", [[-1], [0, 0]])
def test_negative_or_duplicate_answer_indices_are_invalid(indices):
    assert problems(coverage(assessment("doc-a", indices=indices), assessment("doc-b", indices=[1])))


@pytest.mark.parametrize("gap,appropriate,message", [
    ("", True, "所给摘录未独立支持模型A的结构。"),
    ("模型A缺少结构证据。", True, "模型B缺少结构证据。"),
    ("模型A缺少结构证据。", False, "模型A缺少结构证据。"),
    ("证据不足。", False, "证据不足。"),
])
def test_empty_invented_or_inappropriate_acknowledgements_remain_missing(gap, appropriate, message):
    result = coverage(assessment("doc-a", gap=gap, appropriate=appropriate), assessment("doc-b", indices=[1]))
    assert problems(result, message)


def test_an_acknowledgement_cannot_hide_uncited_facts_in_message():
    gap = "所给摘录未独立支持模型A的结构。"
    result = coverage(assessment("doc-a", gap=gap, appropriate=True), assessment("doc-b", indices=[1]),
                      unverified=["The message attributes an uncited loss function to model A."])
    found = problems(result, gap + "模型A最小化某个损失函数。")
    assert found and any("uncited loss function" in problem for problem in found)


def test_invalid_answer_reference_cannot_be_rescued_by_an_otherwise_valid_gap():
    gap = "所给摘录未独立支持模型A的结构。"
    result = coverage(assessment("doc-a", indices=[1], gap=gap, appropriate=True), assessment("doc-b", indices=[1]))
    assert problems(result, gap)
