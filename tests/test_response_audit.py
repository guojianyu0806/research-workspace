"""Non-review answers retain exact claim identities through repair and failure."""
import asyncio
import hashlib
import json

import pytest

from research_workspace.intelligence import (AnswerPayload, ClaimSupportPayload, ClaimRepairPayload, CoveragePayload,
    Evidence, IntelligenceService, ModelOutputError)
from research_workspace.library import Library


def source():
    return Evidence(evidence_id="E_scope",document_id="doc",document_title="ORION",filename="orion.pdf",
        run_id="frozen",block_id="scope",page=2,bbox=[.1,.2,.8,.3],
        text="ORION models use attention. ORION predicts removed words.")


def answer(text="ORION使用注意力。",qid="Q1"):
    return AnswerPayload(status="answered",claims=[{"text":text,"citations":[{"evidence_id":"E_scope","quote_id":qid}]}])


class Sequence:
    configured=True
    def __init__(self,items):self.items=iter(items)
    async def generate(self,prompt,payload,model):
        value=next(self.items)
        assert isinstance(value,model)
        return value


def generate(tmp_path,items):
    service=IntelligenceService(Library(tmp_path),Sequence(items))
    return asyncio.run(service._generate_grounded("fixture",{"task":"qa","question":"Architecture?"},
        AnswerPayload,[source()]))


def identity(c):
    data={"text":c["text"],"citations":c["citations"]}
    return "C_"+hashlib.sha256(json.dumps(data,ensure_ascii=False,sort_keys=True).encode()).hexdigest()


def test_repair_audit_keeps_each_attempt_identity_and_final_verdict_on_its_own_fact(tmp_path):
    _,_,g=generate(tmp_path,[answer("ORION预测99个词。","Q2"),ClaimSupportPayload(verdict="unsupported",reason="Wrong quantity."),
        answer(),ClaimSupportPayload(verdict="supported",reason="Q1 names ORION and its use of attention."),
        CoveragePayload(unverified_statements=[],missing_aspects=[])])
    first,last=g["attempts"]
    for a in (first,last):
        assert a["claims"][0]["claim_id"]==identity(a["claims"][0])
        assert a["reviews"][0]["claim_id"]==a["claims"][0]["claim_id"]
    assert first["claims"][0]["claim_id"]!=last["claims"][0]["claim_id"]
    assert g["claim_catalog"]==last["claims"]
    assert g["claim_reviews"]==last["reviews"]
    coverage=g["response_coverage"][-1]
    assert coverage["claim_ids"]==[g["claim_catalog"][0]["claim_id"]]
    assert coverage["missing_aspects"]==[] and coverage["unverified_statements"]==[]
    assert g["claim_catalog"][0]["citations"][0]["citation_id"]=="R1"


def test_empty_refusal_catalog_cannot_reuse_previous_claim_or_verdict(tmp_path):
    _,_,g=generate(tmp_path,[AnswerPayload(status="insufficient_evidence",message="No matching evidence.")])
    assert g["claim_catalog"]==[] and g["claim_reviews"]==[]
    assert g["coverage_method"]=="not_run_no_asserted_claims"
    assert g["status"]=="no_asserted_claims"


def test_failed_non_review_keeps_diagnostics_without_publishing_an_answer(tmp_path):
    bad=answer("ORION预测99个词。","Q2")
    with pytest.raises(ModelOutputError) as caught:
        generate(tmp_path,[bad,ClaimSupportPayload(verdict="unsupported",reason="Wrong quantity."),bad])
    d=caught.value.grounding_diagnostics
    assert d["diagnostic_only"] and d["status"]=="validation_failed"
    assert len(d["grounding"]["attempts"])==2
    assert "answer" not in d and "sections" not in d
    assert d["grounding"]["attempts"][-1]["reviews"][0]["claim_id"]==d["grounding"]["attempts"][-1]["claims"][0]["claim_id"]


def test_job_persists_only_typed_grounding_failure_and_never_marks_it_completed(tmp_path):
    from research_workspace.jobs import JobStore
    error=ModelOutputError("Invalid supported relation.")
    error.grounding_diagnostics={"diagnostic_only":True,"status":"validation_failed","grounding":{"attempts":[]}}
    class Failed:
        async def answer(self,*args,**kwargs):raise error
    store=JobStore(Library(tmp_path));job=store.create("qa",{"document_ids":["doc"],"question":"Architecture?"})
    asyncio.run(store.process_next(Failed()));saved=store.get(job["id"])
    assert saved["status"]=="failed" and saved["result"] is None and saved["progress"]==0
    assert saved["completed_items"]==[{"stage":"grounding_validation","result":error.grounding_diagnostics}]


def test_common_architecture_descriptors_are_not_treated_as_named_models():
    names=IntelligenceService._named_attribution_identifiers({"E":{"Q1":
        "Recurrent models are sequential. Convolutional models use kernels. ORION models use attention. The Transformer model uses attention."}})
    assert "orion" in names and "transformer" in names
    assert not {"recurrent","convolutional"}.intersection(names)


def test_exact_quotes_still_require_requested_coverage_when_public_answer_requires_it(tmp_path):
    initial=answer(source().text.split('. ')[0]+'.')
    initial.claims[0].citations[0].quote=initial.claims[0].text
    fixed=answer("ORION预测被移除的词。","Q2")
    provider=Sequence([initial,CoveragePayload(unverified_statements=[],missing_aspects=["Prediction objective was not answered."]),
        fixed,ClaimSupportPayload(verdict="supported",reason="Q2 explicitly names ORION and its prediction task."),
        CoveragePayload(unverified_statements=[],missing_aspects=[])])
    service=IntelligenceService(Library(tmp_path),provider)
    result,_,g=asyncio.run(service._generate_grounded("fixture",{"task":"qa","question":"Prediction objective?",
        "require_request_coverage":True},AnswerPayload,[source()]))
    assert result.claims[0].text==fixed.claims[0].text
    assert len(g["response_coverage"])==2
    assert g["response_coverage"][0]["missing_aspects"]


def test_public_comparison_adds_actual_main_task_definitions_from_one_frozen_snapshot(tmp_path,monkeypatch):
    def block(key,text,y):return source().model_copy(update={"evidence_id":"E_"+key,"block_id":key,
        "text":text,"bbox":[.1,y,.8,y+.04]})
    architecture=block("architecture","ORION models use attention.",.1)
    scope=block("scope","We pre-train ORION with two unsupervised tasks described below.",.2)
    word=block("word","Task #1: Word Prediction\nWe predict removed words from context.",.3)
    order=block("order","Task #2: Sentence Order\nWe classify whether sentence B follows A.",.4)
    other=block("other","NOVA models use attention.",.1).model_copy(update={"document_id":"other"})
    snapshot=[architecture,scope,word,order,other]
    class Inspect:
        configured=True
        async def generate(self,prompt,payload,model):
            if model is AnswerPayload:
                assert payload["require_request_coverage"]
                ids={b["evidence_id"] for b in payload["evidence"]}
                assert {"E_scope","E_word","E_order"}<=ids
                assert all(b["run_id"]=="frozen" for b in payload["evidence"])
                assert len(payload["required_training_tasks"]["doc"]["tasks"])==2
                return AnswerPayload(status="answered",claims=[{"text":b.text,"citations":[{"evidence_id":b.evidence_id,"quote_id":"Q1"}]} for b in [architecture,other]])
            assert model is CoveragePayload
            return CoveragePayload(unverified_statements=[],missing_aspects=[])
    service=IntelligenceService(Library(tmp_path),Inspect())
    monkeypatch.setattr(service,"_validate_documents",lambda x:x)
    async def retrieve(*args,**kwargs):
        assert kwargs["include_attribution_context"]
        return {"evidence":[architecture,other],"queries":[],"trace":[],"_snapshot_blocks":snapshot}
    monkeypatch.setattr(service,"retrieve_for_question",retrieve)
    monkeypatch.setattr(service,"_blocks",lambda *x:(_ for _ in ()).throw(AssertionError("Snapshot reopened")))
    result=asyncio.run(service.answer("比较模型结构和训练目标。",["doc","other"],mode="compare"))
    assert result["retrieval"]["response_dimension_search"]["selected_runs"]=={"doc":"frozen","other":"frozen"}


def test_isolated_repair_freezes_supported_fact_and_rechecks_replacement_identity(tmp_path):
    first=answer().claims[0];bad=answer("ORION预测99个词。","Q2").claims[0]
    fixed=answer("ORION预测被移除的词。","Q2").claims[0]
    provider=Sequence([AnswerPayload(status="answered",claims=[first,bad]),
        ClaimSupportPayload(verdict="supported",reason="Own attention relation."),
        ClaimSupportPayload(verdict="unsupported",reason="Wrong quantity."),ClaimRepairPayload(claim=fixed),
        ClaimSupportPayload(verdict="supported",reason="Own prediction statement."),
        CoveragePayload(unverified_statements=[],missing_aspects=[])])
    service=IntelligenceService(Library(tmp_path),provider)
    result,_,g=asyncio.run(service._generate_grounded("fixture",{"task":"qa","question":"Architecture and prediction?",
        "allow_isolated_claim_repair":True,"require_request_coverage":True},AnswerPayload,[source()]))
    assert result.claims[0].text==first.text and result.claims[1].text==fixed.text
    assert g["attempts"][0]["claims"][0]["claim_id"]==g["claim_catalog"][0]["claim_id"]
    repair=g["claim_repairs"][0]
    assert repair["original_claim_id"]==g["attempts"][0]["claims"][1]["claim_id"]
    assert repair["repaired_claim_id"]==g["claim_catalog"][1]["claim_id"]
    assert repair["source_attempt"]==1 and repair["claim_index"]==repair["result_claim_index"]==1
    assert g["attempts"][-1]["reviews"][0]["method"]=="cached_isolated_review"


def test_only_conditional_methodology_can_move_outside_fact_cards_without_being_deleted(tmp_path):
    valid=answer().claims[0]
    caution=answer("不同任务上的分数不能直接比较。","Q2").claims[0]
    provider=Sequence([AnswerPayload(status="answered",claims=[caution,valid]),
        ClaimSupportPayload(verdict="unsupported",reason="A methodological caution belongs in message."),
        ClaimSupportPayload(verdict="supported",reason="Own attention relation."),
        ClaimRepairPayload(claim=None,limitation=caution.text),CoveragePayload(unverified_statements=[],missing_aspects=[])])
    service=IntelligenceService(Library(tmp_path),provider)
    result,_,g=asyncio.run(service._generate_grounded("fixture",{"task":"compare","question":"Compare?",
        "allow_isolated_claim_repair":True,"require_request_coverage":True},AnswerPayload,[source()]))
    assert len(result.claims)==1 and result.message==caution.text
    assert g["claim_repairs"][0]["repaired_claim_id"] is None
    assert g["response_coverage"][-1]["response_message"]==caution.text
    assert g["attempts"][0]["claims"][1]["claim_id"]==g["claim_catalog"][0]["claim_id"]
    assert g["claim_reviews"][0]["claim_index"]==0
    assert g["claim_repairs"][0]["claim_index"]==0 and g["claim_repairs"][0]["result_claim_index"] is None


def test_isolated_repair_cannot_replace_requested_positive_fact_with_gap_text(tmp_path):
    provider=Sequence([answer("ORION预测99个词。","Q2"),ClaimSupportPayload(verdict="unsupported",reason="Wrong quantity."),
        ClaimRepairPayload(claim=None,limitation="Prediction not supplied.")])
    service=IntelligenceService(Library(tmp_path),provider)
    with pytest.raises(ModelOutputError,match="不能用缺口说明删除事实"):
        asyncio.run(service._generate_grounded("fixture",{"task":"qa","question":"Prediction?",
            "allow_isolated_claim_repair":True},AnswerPayload,[source()]))
