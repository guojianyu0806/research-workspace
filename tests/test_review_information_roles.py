"""Supported corpus roles organize broad comparisons without asserting retrieval."""
import asyncio
import pytest
from research_workspace.intelligence import (Claim, Evidence, IntelligenceService, ModelOutputError,
    SynthesisPayload, ClaimSupportPayload, ReviewCoveragePayload, ReviewClaimCoveragePayload)
from research_workspace.library import Library
from research_workspace.review_retrieval import verified_training_corpus_role, is_information_role_comparison


REQUEST="撰写ORION综述，分清外部知识的作用。"
ASPECT="外部知识的作用"


def inputs():
    metadata=dict(document_id="doc",document_title="ORION",filename="orion.pdf",run_id="frozen",page=1,bbox=[.1,.1,.9,.3])
    evidence=[Evidence(evidence_id="E_scope",block_id="scope",text="The ORION large model is trained for translation.",**metadata),
              Evidence(evidence_id="E_corpus",block_id="corpus",text="For this setting we use the PublicArchive dataset.",**metadata)]
    claim=Claim(text="ORION large的训练使用PublicArchive数据集作为训练数据。",citations=[{
        "evidence_id":b.evidence_id,"quote_id":"Q1","quote":b.text} for b in evidence])
    return evidence,claim


def test_corpus_role_uses_own_named_training_scope_and_own_dataset_quote(tmp_path):
    service=IntelligenceService(Library(tmp_path));e,c=inputs()
    role=verified_training_corpus_role(service,service._review_claim_identity(c,e),"doc",["orion"])
    assert role["role"]=="training_corpus"
    assert role["subject_citation_id"]=="R1" and role["answer_citation_id"]=="R2"
    assert "does not assert inference-time retrieval" in role["interpretation"]


@pytest.mark.parametrize("text",["ORION使用PublicArchive数据集。","一些混合系统的训练使用PublicArchive数据集。","ORION未使用PublicArchive数据集训练。",
    "ORION训练很快，PublicArchive数据集包含许多文本。", "ORION已经完成训练；另一个PublicArchive数据集包含样本。",
    "ORION不在PublicArchive数据集上训练。", "ORION is never trained on the PublicArchive dataset."])
def test_dataset_name_background_or_negative_statement_cannot_supply_positive_corpus_role(tmp_path,text):
    service=IntelligenceService(Library(tmp_path));e,c=inputs();c.text=text
    assert verified_training_corpus_role(service,service._review_claim_identity(c,e),"doc",["orion"]) is None


def test_architecture_name_or_another_documents_dataset_cannot_supply_a_training_source(tmp_path):
    service=IntelligenceService(Library(tmp_path));e,c=inputs()
    identity=service._review_claim_identity(c,e)
    identity["citations"][0]["quote"]="ORION uses an attention encoder."
    assert verified_training_corpus_role(service,identity,"doc",["orion"]) is None
    identity=service._review_claim_identity(c,e);identity["citations"][1]["document_id"]="other"
    assert verified_training_corpus_role(service,identity,"doc",["orion"]) is None
    identity=service._review_claim_identity(c,e);identity["citations"][1]["quote"]="PublicArchive is a dataset of many sentences."
    assert verified_training_corpus_role(service,identity,"doc",["orion"]) is None


def test_training_source_policy_cannot_answer_yes_no_retrieval_questions():
    assert is_information_role_comparison(REQUEST,ASPECT)
    assert is_information_role_comparison(REQUEST,"外部知识")
    assert not is_information_role_comparison("综述外部知识的作用，判断是否在推理时检索知识。",ASPECT)
    assert not is_information_role_comparison(REQUEST,"模型结构")


class Provider:
    configured=True
    def __init__(self,supported=True):self.supported=supported
    async def generate(self,prompt,payload,model):
        e,c=inputs()
        if model is SynthesisPayload:return SynthesisPayload(status="generated",sections=[{"heading":ASPECT,"claims":[c.model_dump()]}])
        if model is ClaimSupportPayload:return ClaimSupportPayload(verdict="supported" if self.supported else "unsupported",reason="Own source relation." if self.supported else "A rejected factual relation.")
        if model is ReviewClaimCoveragePayload:return ReviewClaimCoveragePayload(answers=[])
        assert model is ReviewCoveragePayload
        return ReviewCoveragePayload(requested_aspects=[ASPECT],assessments=[{"document_id":"doc","aspect":ASPECT,
            "answer_claim_indices":[],"gap_quote":"","gap_is_appropriate":False,"reason":"Model omitted the stage comparison link."}],unverified_statements=[])


def test_supported_training_role_fills_only_its_own_source_stage_row_and_preserves_model_omission(tmp_path):
    service=IntelligenceService(Library(tmp_path),Provider());e,_=inputs()
    result,_,audit=asyncio.run(service._generate_grounded("fixture",{"task":"review","instruction":REQUEST},
        SynthesisPayload,e,required_documents=["doc"]))
    record=audit["review_coverage"][-1];link=record["claim_link_reviews"][0]
    assert result.status=="generated" and record["assessments"][0]["answer_claim_indices"]==[0]
    assert link["model_answers"]==[] and link["derived_source_role_links"][0]["role"]=="training_corpus"
    assert record["proposed_assessments"][0]["answer_claim_indices"]==[]
    assert audit["claim_model_calls"]==1 and audit["coverage_link_model_calls"]==1


def test_source_role_policy_never_overrides_an_unsupported_factual_claim(tmp_path):
    service=IntelligenceService(Library(tmp_path),Provider(supported=False));e,_=inputs()
    with pytest.raises(ModelOutputError):
        asyncio.run(service._generate_grounded("fixture",{"task":"review","instruction":REQUEST},
            SynthesisPayload,e,required_documents=["doc"]))
