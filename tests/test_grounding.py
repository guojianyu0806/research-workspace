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


def review(verdict, reason=""):
    return GroundingPayload(reviews=[{"claim_index": 0, "verdict": verdict, "reason": reason}])


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
        generate(tmp_path, [answer("模型得分为28.4。", "The score is 28.4."), GroundingPayload(reviews=[])])


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


@pytest.mark.parametrize("quote", ["x₂ = 4", "x2 = 4"])
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
