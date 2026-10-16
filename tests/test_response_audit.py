"""Non-review answers retain exact claim identities through repair and failure."""
import asyncio
import hashlib
import json

import pytest

from research_workspace.intelligence import (AnswerPayload, ClaimSupportPayload, ClaimRepairPayload, CoveragePayload, SynthesisPayload,
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
                assert payload['coverage_contract']['training_goal_standard']=='actual_training_task_and_prediction_content'
                assert payload['coverage_contract']['optimization_loss_requested'] is False
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
    class Inspect(Sequence):
        async def generate(self,prompt,payload,model):
            if model is ClaimRepairPayload:assert payload["request"]=="Architecture and prediction?"
            if model is ClaimSupportPayload:assert "request" not in payload and "question" not in payload
            return await super().generate(prompt,payload,model)
    provider=Inspect([AnswerPayload(status="answered",claims=[first,bad]),
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


def test_numeric_result_cannot_evade_own_identity_by_saying_the_paper_reports_it():
    c=answer("论文报告英语到德语的BLEU分数为28.4。").claims[0]
    c.citations[0].quote="Our model achieves 28.4 BLEU on English-to-German."
    assert IntelligenceService._missing_quantitative_result_scope(c,{"orion"})
    c.text="ORION (big)在英语到德语任务的BLEU分数为28.4。"
    c.citations[0].quote="The ORION (big) model achieves 28.4 BLEU on English-to-German."
    assert IntelligenceService._missing_quantitative_result_scope(c,{"orion"})==[]
    c.text="ORION在英语到德语任务的BLEU分数为28.4。"
    assert "the evaluated big variant" in IntelligenceService._missing_quantitative_result_scope(c,{"orion"})


def test_corpus_counts_are_not_mistaken_for_measured_performance_results():
    c=answer("ORION训练使用36M句。").claims[0]
    c.citations[0].quote="Our model trains on 36M sentences."
    assert IntelligenceService._missing_quantitative_result_scope(c,{"orion"})==[]


def test_public_response_catalog_does_not_offer_unfinished_page_tail_as_a_citable_fact(tmp_path):
    prose=source().model_copy(update={"text":"ORION predicts removed words. The objective is to predict the original vocabulary id of the masked"})
    class Inspect:
        configured=True
        async def generate(self,prompt,payload,model):
            if model is AnswerPayload:
                spans=payload["evidence"][0]["citation_spans"]
                assert spans==[{"quote_id":"Q1","text":"ORION predicts removed words."}]
                return AnswerPayload(status="answered",claims=[{"text":"ORION predicts removed words.","citations":[{"evidence_id":"E_scope","quote_id":"Q1"}]}])
            assert model is CoveragePayload
            return CoveragePayload(unverified_statements=[],missing_aspects=[])
    service=IntelligenceService(Library(tmp_path),Inspect())
    result,_,g=asyncio.run(service._generate_grounded("fixture",{"task":"qa","question":"Prediction?",
        "allow_isolated_claim_repair":True,"require_request_coverage":True},AnswerPayload,[prose]))
    assert result.claims[0].citations[0].quote=="ORION predicts removed words."
    assert g["citation_catalog_policy"].startswith("omit_unfinished_trailing_prose")


@pytest.mark.parametrize("complete",[True,False])
def test_rejected_extra_can_only_be_removed_after_actual_positive_coverage_not_gap_acknowledgement(tmp_path,complete):
    valid=answer().claims[0];bad=answer("ORION预测99个词。","Q2").claims[0]
    initial=AnswerPayload(status="answered",claims=[valid,bad],message="所给摘录未支持预测任务。" if not complete else "")
    class Inspect(Sequence):
        async def generate(self,prompt,payload,model):
            if model is CoveragePayload:
                assert payload["actual_answers_only"] is True
                assert len(payload["claims"])==1
            return await super().generate(prompt,payload,model)
    provider=Inspect([initial,ClaimSupportPayload(verdict="supported",reason="Own attention relation."),
        ClaimSupportPayload(verdict="unsupported",reason="Wrong quantity."),ClaimRepairPayload(claim=bad),
        CoveragePayload(unverified_statements=[],missing_aspects=[] if complete else ["Actual prediction task is not answered."])])
    service=IntelligenceService(Library(tmp_path),provider)
    scenario=service._generate_grounded("fixture",{"task":"qa","question":"Architecture?" if complete else "Architecture and prediction?",
        "allow_isolated_claim_repair":True,"require_request_coverage":True},AnswerPayload,[source()])
    if not complete:
        with pytest.raises(ModelOutputError) as caught:asyncio.run(scenario)
        g=caught.value.grounding_diagnostics["grounding"]
        assert g["optional_prune_checks"][-1]["missing_aspects"]
        assert "optional_prune" not in g
        return
    result,_,g=asyncio.run(scenario)
    assert [c.text for c in result.claims]==[valid.text]
    assert g["optional_prune"]["source_attempt"]==2
    assert g["optional_prune"]["removed_original_indices"]==[1]
    assert g["attempts"][-1]["method"]=="supported_subset_checkpoint_not_new_generation"
    assert g["claim_reviews"][0]["claim_id"]==g["claim_catalog"][0]["claim_id"]
    repair=g["claim_repairs"][0]
    assert repair["result_claim_index"] is None and repair["retained_in_final"] is False
    assert repair["repaired_claim_id"] in g["optional_prune"]["removed_claim_ids"]


def test_draft_coverage_receives_and_records_actual_proposed_diagram(tmp_path):
    graph={"nodes":[{"id":"question","label":"问题"},{"id":"retriever","label":"检索器"},
        {"id":"evidence","label":"证据文段"},{"id":"generator","label":"生成器"}],
        "edges":[{"source":"question","target":"retriever"},{"source":"retriever","target":"evidence"},
                 {"source":"evidence","target":"generator"}]}
    class Inspect:
        configured=True
        async def generate(self,prompt,payload,model):
            if model is SynthesisPayload:
                c={"text":"ORION models use attention.","citations":[{"evidence_id":"E_scope","quote_id":"Q1"}]}
                return SynthesisPayload(status="generated",sections=[{"heading":h,"claims":[c],"proposed_work":"拟开展实验，尚未执行。"}
                    for h in ['Abstract','Introduction','Related Work']],architecture=graph)
            assert model is CoveragePayload
            assert payload["proposed_architecture"]==graph
            return CoveragePayload(unverified_statements=[],missing_aspects=[])
    service=IntelligenceService(Library(tmp_path),Inspect())
    result,_,g=asyncio.run(service._generate_grounded("fixture",{"task":"draft","instruction":"提供四节点概念图。",
        "allow_isolated_claim_repair":True,"require_request_coverage":True},SynthesisPayload,[source()]))
    assert result.architecture.model_dump()==graph
    assert g["attempts"][-1]["proposed_architecture"]==g["response_coverage"][-1]["proposed_architecture"]==graph


def task_scope_fixture():
    scope=source().model_copy(update={"text":"We pre-train ORION using two tasks described below."})
    definition=source().model_copy(update={"evidence_id":"E_task","block_id":"task","text":"We predict removed words from context."})
    req={"doc":{"scope_evidence_id":"E_scope","scope_quote_id":"Q1","scope_text":scope.text,
        "subject_identifiers":["orion"],"tasks":[{"label":"Word Prediction","evidence_ids":["E_task"]}]}}
    c=answer("ORION的预训练任务预测被移除的词。")
    c.claims[0].citations[0].evidence_id="E_task"
    return [scope,definition],req,c


def test_actual_task_scope_is_attached_before_isolated_review_with_original_selection_preserved(tmp_path):
    e,req,c=task_scope_fixture()
    class Inspect(Sequence):
        async def generate(self,prompt,payload,model):
            if model is ClaimSupportPayload:
                assert {q['evidence_id'] for q in payload['claim']['citations']}=={'E_scope','E_task'}
                assert 'request' not in payload and 'evidence' not in payload
            return await super().generate(prompt,payload,model)
    provider=Inspect([c,ClaimSupportPayload(verdict="supported",reason="Own named task scope and its definition."),
        CoveragePayload(unverified_statements=[],missing_aspects=[])])
    service=IntelligenceService(Library(tmp_path),provider)
    result,_,g=asyncio.run(service._generate_grounded("fixture",{"task":"qa","question":"Word prediction?",
        "required_training_tasks":req,"allow_isolated_claim_repair":True,"require_request_coverage":True},AnswerPayload,e))
    record=g["task_scope_attachments"][0]
    assert len(record["model_claim"]["citations"])==1
    assert record["model_claim"]["claim_id"]==identity(record["model_claim"])
    assert record["effective_claim_id"]==g["claim_catalog"][0]["claim_id"]
    assert result.claims[0].citations[-1].quote==e[0].text


@pytest.mark.parametrize("change",["wrong_subject","foreign_task","mixed_subject_hints"])
def test_scope_attachment_never_supplies_another_subject_or_documents_training_context(tmp_path,change):
    from research_workspace.task_scope import attach_task_scope
    e,req,result=task_scope_fixture();c=result.claims[0]
    service=IntelligenceService(Library(tmp_path),Sequence([]))
    service._resolve_citation_spans([c],service._citation_spans(e))
    if change in {"wrong_subject","mixed_subject_hints"}:c.text="NOVA的训练任务预测被移除的词。"
    else:e[1]=e[1].model_copy(update={"document_id":"other"})
    if change=="mixed_subject_hints":req['doc']['subject_identifiers']=['orion','nova']
    audit={};attach_task_scope(service,c,e,service._citation_spans(e),req,audit,{"stage":"fixture"})
    assert len(c.citations)==1 and audit=={}


def test_attaching_scope_does_not_override_an_unsupported_predicate(tmp_path):
    e,req,result=task_scope_fixture();result.claims[0].text="ORION预测99个词。"
    provider=Sequence([result,ClaimSupportPayload(verdict="unsupported",reason="The attached scope does not support the number."),
        ClaimRepairPayload(claim=result.claims[0])])
    service=IntelligenceService(Library(tmp_path),provider)
    with pytest.raises(ModelOutputError) as caught:
        asyncio.run(service._generate_grounded("fixture",{"task":"qa","question":"Word prediction?",
            "required_training_tasks":req,"allow_isolated_claim_repair":True},AnswerPayload,e))
    g=caught.value.grounding_diagnostics["grounding"]
    assert g["task_scope_attachments"] and all(v["verdict"]=="unsupported" for a in g["attempts"] for v in a["reviews"])

@pytest.mark.parametrize('complete',[True,False])
def test_literal_clause_repair_preserves_supported_required_fact_but_still_requires_full_coverage(tmp_path,complete):
    initial=answer('ORION预测被移除的词；输出概率达到99。','Q2')
    class Inspect(Sequence):
        async def generate(self,prompt,payload,model):
            if model is ClaimSupportPayload:
                assert set(payload)=={'task','claim_index','claim','response_kind'}
            if model is CoveragePayload:
                assert payload['claims'][0]['text']=='ORION预测被移除的词'
            return await super().generate(prompt,payload,model)
    provider=Inspect([initial,ClaimSupportPayload(verdict='unsupported',reason='Prediction supported; probability detail unsupported.'),
        ClaimSupportPayload(verdict='supported',reason='Own original-word prediction definition.'),
        ClaimSupportPayload(verdict='supported',reason='Final whole card independently supported.'),
        CoveragePayload(unverified_statements=[],missing_aspects=[] if complete else ['Requested second task is still absent.'])])
    service=IntelligenceService(Library(tmp_path),provider)
    run=service._generate_grounded('fixture',{'task':'qa','question':'Prediction?' if complete else 'Both tasks?',
        'allow_isolated_claim_repair':True,'require_request_coverage':True},AnswerPayload,[source()])
    if not complete:
        with pytest.raises(ModelOutputError) as caught:asyncio.run(run)
        audit=caught.value.grounding_diagnostics['grounding']
        assert audit['response_coverage'][-1]['missing_aspects']
    else:
        result,_,audit=asyncio.run(run)
        assert result.claims[0].text=='ORION预测被移除的词'
        assert audit['claim_reviews'][0]['claim_id']==audit['claim_catalog'][0]['claim_id']
    projection=audit['literal_clause_projections'][0]
    assert projection['original_claim']['text']==initial.claims[0].text
    assert projection['checks'][0]['verdict']=='supported'
    assert projection['checks'][1]['status']=='not_reviewed_subject_not_explicit'
    assert audit['claim_repairs'][0]['repair_method']=='literal_supported_clause_projection'
    assert audit['claim_repairs'][0]['original_claim_id']==projection['original_claim']['claim_id']
    assert 'claim_repair_prompt_version' not in audit


def test_literal_projection_cannot_approve_wrong_clause_or_exceed_recorded_budget(tmp_path):
    from research_workspace.claim_projection import project_supported_clauses
    initial=answer('ORION预测99个词；输出异常。','Q2').claims[0]
    service=IntelligenceService(Library(tmp_path),Sequence([ClaimSupportPayload(verdict='unsupported',reason='Wrong quantity.')]))
    catalog=service._citation_spans([source()]);service._resolve_citation_spans([initial],catalog)
    audit={'response_kind':'qa','claim_model_calls':0}
    assert asyncio.run(project_supported_clauses(service,initial,[source()],catalog,audit)) is None
    assert audit['literal_clause_projections'][0]['checks'][0]['verdict']=='unsupported'
    audit['literal_projection_model_calls']=4
    assert asyncio.run(project_supported_clauses(service,initial,[source()],catalog,audit)) is None
    assert audit['literal_clause_projections'][-1]['stopped_reason']=='four_projection_check_budget_reached'
    assert audit['claim_model_calls']==1
