"""Deterministic limitations describe retained answers, not what a paper lacks."""
from copy import deepcopy

import pytest

from research_workspace.intelligence import Evidence, IntelligenceService


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
