"""Explicit original task declarations bound completeness without borrowing facts."""
from research_workspace.intelligence import Claim, Evidence, IntelligenceService, ReviewClaimCoveragePayload
from research_workspace.library import Library
from research_workspace.review_retrieval import training_task_requirements, training_eligibility, supplement_review_evidence, is_training_aspect


def paragraph(key, text, y):
    return Evidence(evidence_id="E_"+key, block_id=key, document_id="doc", document_title="ORION",
                    filename="orion.pdf", run_id="frozen", page=1, bbox=[.1,y,.9,y+.05], text=text)


def sources():
    return [paragraph("scope", "ORION uses an encoder. We pre-train ORION using two unsupervised tasks described in this section.", .1),
            paragraph("word", "Task #1: Word Reconstruction\nWe predict the removed word from its context.", .2),
            paragraph("order", "Task #2: Sentence Order\nWe classify whether the second sentence follows the first.", .3)]


def current_card(service, selected, text="ORION预训练恢复被移除的词，并判断两句顺序。"):
    spans=service._citation_spans(selected, omit_trailing_fragment=True)
    return Claim(text=text, citations=[{"evidence_id":b.evidence_id,"quote_id":next(reversed(spans[b.evidence_id])),
        "quote":next(reversed(spans[b.evidence_id].values()))} for b in selected])


def test_explicit_named_declaration_extracts_actual_numbered_tasks_and_frozen_scope(tmp_path):
    service=IntelligenceService(Library(tmp_path)); original=sources()
    req=training_task_requirements(service,original,["orion"])
    assert req["scope_evidence_id"]=="E_scope" and req["scope_quote_id"]=="Q2"
    assert [task["label"] for task in req["tasks"]]==["Word Reconstruction","Sentence Order"]
    assert training_eligibility(service,current_card(service,original),["orion"],req)==[]
    partial=current_card(service,[original[0],original[1]],"ORION预训练恢复被移除的词。")
    assert "Sentence Order" in training_eligibility(service,partial,["orion"],req)


def test_a_named_one_task_card_cannot_borrow_the_missing_task_scope(tmp_path):
    service=IntelligenceService(Library(tmp_path)); original=sources()
    req=training_task_requirements(service,original,["orion"])
    card=current_card(service,original)
    card.citations[0].quote_id="Q1"
    card.citations[0].quote=service._citation_spans(original)["E_scope"]["Q1"]
    assert "named multiple-task scope declaration" in training_eligibility(service,card,["orion"],req)


def test_background_name_or_missing_numbered_task_does_not_manufacture_checklist(tmp_path):
    service=IntelligenceService(Library(tmp_path)); original=sources()
    background=paragraph("scope","ORION uses an encoder. We pre-train NOVA using two unsupervised tasks described in this section.",.1)
    assert training_task_requirements(service,[background,*original[1:]],["orion"]) is None
    assert training_task_requirements(service,original[:2],["orion"]) is None


def test_task_scope_and_every_task_reserve_search_slots_before_unrelated_context(tmp_path):
    service=IntelligenceService(Library(tmp_path)); original=sources()
    evidence,audit=supplement_review_evidence(service,original,[],[],request="training objective",
        missing_pairs=[("doc","training objective")],max_evidence=3,subject_names={"doc":["orion"]})
    assert {b.evidence_id for b in evidence}=={"E_scope","E_word","E_order"}
    assert audit["searches"][0]["role_anchor_block_ids"]==["scope","word","order"]


def test_softmax_without_actual_training_relation_is_ineligible_even_if_model_proposes_it(tmp_path):
    service=IntelligenceService(Library(tmp_path))
    source=paragraph("softmax","ORION converts decoder outputs into next-token probabilities using softmax.",.1)
    card=current_card(service,[source],"ORION使用softmax转换下一词预测概率。")
    assert training_eligibility(service,card,["orion"])
    identity={"citations":[{"citation_id":"R1","document_id":"doc"}]}
    proposed=ReviewClaimCoveragePayload(answers=[{"document_id":"doc","aspect":"training objective",
        "subject_citation_id":"R1","answer_citation_id":"R1","reason":"An incorrect model guess."}])
    assert service._review_link_problems(proposed,identity,["doc"],["architecture","training objective"],
        eligible_pairs={("doc","architecture")})


def test_training_eligibility_does_not_relabel_general_research_goals_as_training():
    assert is_training_aspect("训练目标") and is_training_aspect("training objective")
    assert not is_training_aspect("研究目标") and not is_training_aspect("research objectives")
