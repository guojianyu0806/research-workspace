"""Offline regression for semantically unsupported but text-matching citations."""
import asyncio
import json

import httpx
import pytest

from research_workspace.intelligence import (
    AnswerPayload, ChatCompletionProvider, Evidence, GroundingPayload,
    IntelligenceService, ModelConfig, ModelOutputError,
)
from research_workspace.library import Library


def evidence():
    return Evidence(evidence_id="E_fixture", document_id="doc", document_title="Test paper",
                    filename="test.pdf", run_id="run", block_id="fixture", page=2,
                    bbox=[1, 2, 3, 4], text="Figure 1: Model architecture. The model uses an encoder and a decoder. The score is 28.4.")


def answer(text, quote):
    return AnswerPayload(status="answered", claims=[{"text": text, "citations": [
        {"evidence_id": "E_fixture", "quote": quote}]}])


class SequenceProvider:
    configured = True

    def __init__(self, replies):
        self.replies = iter(replies)
        self.calls = []

    async def generate(self, prompt, payload, output_model):
        self.calls.append((payload, output_model))
        result = next(self.replies)
        assert isinstance(result, output_model)
        return result


def generate(tmp_path, replies, **kwargs):
    provider = SequenceProvider(replies)
    service = IntelligenceService(Library(tmp_path), provider)
    result = asyncio.run(service._generate_grounded("fixture", {"task": "qa", "question": "Architecture?"},
        AnswerPayload, [evidence()], **kwargs))
    return result, provider


def review(verdict, reason="", *, unverified=(), missing=()):
    return GroundingPayload(reviews=[{"claim_index": 0, "verdict": verdict, "reason": reason}],
                            unverified_statements=list(unverified), missing_aspects=list(missing))


def test_caption_matching_quote_is_not_enough_and_is_repaired(tmp_path):
    unsupported = answer("模型采用编码器和解码器。", "Figure 1: Model architecture.")
    corrected = answer("模型采用编码器和解码器。", "The model uses an encoder and a decoder.")
    (result, used, audit), provider = generate(tmp_path, [unsupported,
        review("unsupported", "Caption does not specify encoder/decoder."), corrected, review("supported")])
    assert result == corrected and used == {"E_fixture"}
    assert audit["status"] == "passed" and len(audit["attempts"]) == 2
    assert provider.calls[2][0]["correction"]["problems"]
    assert all(call[0].get("evidence", [evidence().model_dump()])[0]["run_id"] == "run"
               for call in provider.calls)


def test_repeated_semantic_failure_is_rejected_after_one_repair(tmp_path):
    bad = answer("模型得分为 99。", "The score is 28.4.")
    with pytest.raises(ModelOutputError, match="复核未通过"):
        generate(tmp_path, [bad, review("unsupported", "Wrong number"), bad, review("unsupported", "Wrong number")])


def test_missing_semantic_verdict_fails_closed(tmp_path):
    with pytest.raises(ModelOutputError, match="完整覆盖"):
        generate(tmp_path, [answer("模型得分为28.4。", "The score is 28.4."),
                           GroundingPayload(reviews=[],unverified_statements=[],missing_aspects=[])])


def test_bad_quote_can_be_repaired_but_cannot_skip_source_check(tmp_path):
    bad = answer("Model score", "The score is 100.")
    good = answer("The score is 28.4.", "The score is 28.4.")
    (_, used, audit), provider = generate(tmp_path, [bad, good])
    assert used == {"E_fixture"} and len(provider.calls) == 2
    assert not audit["model_checked"]  # Final claim is literally the source sentence.


def test_cancellation_stops_before_additional_review_call(tmp_path):
    calls = []
    class Cancelled(Exception):
        pass
    def cancel():
        calls.append(1)
        if len(calls) == 2:
            raise Cancelled()
    with pytest.raises(Cancelled):
        generate(tmp_path, [answer("模型得分为28.4。", "The score is 28.4.")], check_cancel=cancel)


def test_unicode_ligature_matches_but_changed_numeric_value_does_not():
    source = evidence().model_copy(update={"text": "An efﬁcient model scores 28.4."})
    assert IntelligenceService._validate_claims(answer("x", "An efficient model scores 28.4.").claims, [source])
    with pytest.raises(ModelOutputError):
        IntelligenceService._validate_claims(answer("x", "An efficient model scores 28.5.").claims, [source])


@pytest.mark.parametrize("quote", ["x₂ = 4", "x2 = 4", "X² = 4"])
def test_quote_normalization_never_changes_mathematical_exponents(quote):
    source = evidence().model_copy(update={"text": "x² = 4"})
    with pytest.raises(ModelOutputError):
        IntelligenceService._validate_claims(answer("x", quote).claims, [source])


def test_provider_repair_reports_field_type_without_echoing_bad_value():
    requests = []
    def transport(request):
        body = json.loads(request.content)
        requests.append(body)
        payload = ({"status": "answered", "claims": "PRIVATE_BAD_VALUE"} if len(requests) == 1 else
                   {"status": "insufficient_evidence", "claims": [], "message": "missing source"})
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps(payload)}}]})
    adapter = ChatCompletionProvider(ModelConfig("https://model.invalid", "fixture", "fake-not-secret"),
                                     transport=httpx.MockTransport(transport))
    result = asyncio.run(adapter.generate("fixture", {}, AnswerPayload))
    feedback = requests[1]["messages"][-1]["content"]
    assert "claims:list_type" in feedback and "PRIVATE_BAD_VALUE" not in feedback
    assert result.status == "insufficient_evidence"


def test_citation_span_selection_restores_exact_pdf_characters(tmp_path):
    result = AnswerPayload(status="answered", claims=[{"text": "The score is 28.4.", "citations": [
        {"evidence_id": "E_fixture", "quote_id": "Q3", "quote": ""}]}])
    (saved, _, _), provider = generate(tmp_path, [result])
    assert saved.claims[0].citations[0].quote == "The score is 28.4."
    assert provider.calls[0][0]["evidence"][0]["citation_spans"][2]["quote_id"] == "Q3"


def test_unknown_citation_span_cannot_bypass_grounding(tmp_path):
    bad = AnswerPayload(status="answered", claims=[{"text": "Unsupported", "citations": [
        {"evidence_id": "E_fixture", "quote_id": "Q99"}]}])
    with pytest.raises(ModelOutputError, match="无效的原文片段"):
        generate(tmp_path, [bad, bad])


def test_span_and_supplied_quote_must_agree():
    claim = answer("Claim", "The score is 28.5.").claims
    claim[0].citations[0].quote_id = "Q3"
    with pytest.raises(ModelOutputError, match="不一致"):
        IntelligenceService._resolve_citation_spans(claim, IntelligenceService._citation_spans([evidence()]))


def test_catalog_is_bounded_and_every_entry_is_an_exact_source_substring():
    source = evidence().model_copy(update={"text": ("Conﬁguration x² = 4; long-\nrange dependencies. " * 160)})
    spans = IntelligenceService._citation_spans([source])[source.evidence_id]
    assert spans and all(text in source.text and len(text) <= 1800 for text in spans.values())


def test_exact_claim_with_an_extra_irrelevant_citation_requires_semantic_review(tmp_path):
    result = answer("The score is 28.4.", "The score is 28.4.")
    result.claims[0].citations.append(result.claims[0].citations[0].model_copy(update={
        "quote": "Figure 1: Model architecture."}))
    corrected = answer("The score is 28.4.", "The score is 28.4.")
    (_, _, audit), provider = generate(tmp_path, [result,
        review("unsupported", "The extra caption citation is irrelevant to the numeric score."), corrected])
    assert audit["model_checked"] and len(provider.calls) == 3
    assert len(audit["attempts"]) == 2


def test_uncited_message_cannot_bypass_review_even_with_an_exact_claim(tmp_path):
    result = answer("The score is 28.4.", "The score is 28.4.")
    result.message = "The score on another task is 99.9."
    corrected = answer("The score is 28.4.", "The score is 28.4.")
    (saved, _, audit), provider = generate(tmp_path, [result,
        review("supported", unverified=["Uncited numerical result in response_message"]), corrected])
    assert not saved.message and audit["model_checked"] and len(provider.calls) == 3
    assert provider.calls[1][0]["response_message"] == result.message


def test_unacknowledged_missing_aspect_is_sent_back_for_repair(tmp_path):
    result = answer("模型得分为28.4。", "The score is 28.4.")
    corrected = answer("模型得分为28.4。", "The score is 28.4.")
    corrected.message = "Selected excerpts do not support the requested objective."
    (_, _, audit), provider = generate(tmp_path, [result,
        review("supported", missing=["The requested training objective is not addressed"]), corrected, review("supported")])
    assert "Unanswered request aspect" in provider.calls[2][0]["correction"]["problems"][0]
    assert len(audit["attempts"]) == 2
