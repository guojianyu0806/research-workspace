"""Offline protocol/guardrail tests. No test contacts a real model provider."""

import asyncio
import json
from pathlib import Path

import httpx
import pymupdf
import pytest

from research_workspace.intelligence import (
    AnswerPayload, ChatCompletionProvider, IntelligenceService, ModelConfig,
    ModelNotConfigured, ModelOutputError, ModelProviderError, tokenize,
)
from research_workspace.library import Library


def run(coroutine):
    return asyncio.run(coroutine)


def envelope(payload):
    return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
        "content": json.dumps(payload, ensure_ascii=False)}}]})


def provider(handler, *, attempts=3):
    async def no_wait(seconds):
        pass
    return ChatCompletionProvider(
        ModelConfig("https://model.invalid/v1", "offline-fixture", "fixture-not-a-real-key", max_attempts=attempts),
        transport=httpx.MockTransport(handler), sleep=no_wait,
    )


def import_pdf(library, text, filename="paper.pdf"):
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.insert_text((40, 80), text, fontname="china-s" if any(ord(char) > 255 for char in text) else "helv")
        doc = library.import_document(pdf.tobytes(), filename)["document"]
    library.process_next_job()
    return doc["id"]


@pytest.fixture
def library(tmp_path: Path):
    return Library(tmp_path)


def cited_answer(request):
    data = json.loads(json.loads(request.content)["messages"][1]["content"])
    return envelope({"status": "answered", "claims": [
        {"text": item["text"], "citations": [{"evidence_id": item["evidence_id"], "quote": item["text"]}]}
        for item in data["evidence"]
    ], "message": ""})


def test_unconfigured_service_never_invokes_transport(library):
    calls = []
    adapter = ChatCompletionProvider(ModelConfig(), transport=httpx.MockTransport(lambda req: calls.append(req)))
    service = IntelligenceService(library, adapter)
    assert service.capabilities()["configured"] is False
    assert set(service.capabilities()) == {"configured", "prompt_version", "retrieval", "max_documents", "external_freshness_verification"}
    with pytest.raises(ModelNotConfigured):
        run(service.answer("Any question?", ["unknown"]))
    assert calls == []
    assert "fixture-not-a-real-key" not in repr(ModelConfig("https://model.invalid", "model", "fixture-not-a-real-key"))


def test_adapter_exact_endpoint_and_schema():
    seen = []
    def handler(request):
        seen.append(request)
        assert str(request.url) == "https://model.invalid/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer fixture-not-a-real-key"
        return envelope({"status": "insufficient_evidence", "claims": [], "message": "Need sources"})
    result = run(provider(handler).generate("test schema", {"question": "test"}, AnswerPayload))
    assert result.status == "insufficient_evidence" and len(seen) == 1


@pytest.mark.parametrize("status", [429, 503])
def test_rate_or_server_retry_is_bounded(status):
    calls = []
    def handler(request):
        calls.append(1)
        return httpx.Response(status, headers={"Retry-After": "99999"}, text="secret provider details")
    with pytest.raises(ModelProviderError) as caught:
        run(provider(handler).generate("test", {}, AnswerPayload))
    assert len(calls) == 3 and caught.value.retryable
    assert "secret provider details" not in str(caught.value)


def test_authentication_error_does_not_retry_or_echo_body():
    calls = []
    def handler(request):
        calls.append(1)
        return httpx.Response(401, text="fixture-not-a-real-key")
    with pytest.raises(ModelProviderError) as caught:
        run(provider(handler).generate("test", {}, AnswerPayload))
    assert len(calls) == 1 and "fixture-not-a-real-key" not in str(caught.value)


def test_timeout_retries_are_bounded():
    calls = []
    def handler(request):
        calls.append(1)
        raise httpx.ReadTimeout("private request information", request=request)
    with pytest.raises(ModelProviderError) as caught:
        run(provider(handler).generate("test", {}, AnswerPayload))
    assert len(calls) == 3 and "private request" not in str(caught.value)


def test_invalid_model_schema_gets_only_finite_repair():
    calls = []
    def handler(request):
        calls.append(1)
        return envelope({"status": "answered", "claims": [], "extra": "not allowed"})
    with pytest.raises(ModelOutputError):
        run(provider(handler).generate("test", {}, AnswerPayload))
    assert len(calls) == 3


def test_lexical_retrieval_is_ranked_fair_and_version_bound(library):
    first = import_pdf(library, "Transformer parameter count is 100. Encoder layers are stacked.", "one.pdf")
    second = import_pdf(library, "Transformer parameter count is 200.", "two.pdf")
    service = IntelligenceService(library, provider(cited_answer))
    selected = service.retrieve("Transformer parameter count", [first, second], limit=2)
    assert {block.document_id for block in selected} == {first, second}
    assert all(block.score > 0 and len(block.bbox) == 4 for block in selected)
    old = selected[0]
    library.create_run(old.document_id)
    assert service.retrieve("Transformer", [old.document_id])[0].run_id == old.run_id
    result = run(service.answer("Transformer parameter count", [first, second], mode="compare"))
    assert result["status"] == "answered" and len(result["sources"]) == 2
    for source in result["sources"]:
        page = library.get_page(source["document_id"], source["page"], source["run_id"])
        assert source["block_id"] in {block["id"] for block in page["blocks"]}
    assert result["prompt_version"] and result["trace"]


def test_chinese_tokenization_and_retrieval(library):
    document_id = import_pdf(library, "训练参数与原文证据")
    service = IntelligenceService(library, provider(cited_answer))
    assert "参数" in tokenize("请问训练参数是什么")
    assert service.retrieve("训练参数", [document_id])[0].document_id == document_id


def test_missing_evidence_refuses_without_model_request(library):
    document_id = import_pdf(library, "Source about encoder layers")
    calls = []
    service = IntelligenceService(library, provider(lambda req: calls.append(req)))
    result = run(service.answer("xylophone", [document_id]))
    assert result["status"] == "insufficient_evidence" and result["claims"] == [] and calls == []


@pytest.mark.parametrize("kind", ["unknown_id", "invented_quote"])
def test_fabricated_citations_are_never_returned(library, kind):
    document_id = import_pdf(library, "Encoder has six layers")
    def handler(request):
        payload = json.loads(json.loads(request.content)["messages"][1]["content"])
        source = payload["evidence"][0]
        return envelope({"status": "answered", "claims": [{"text": "Claim", "citations": [{
            "evidence_id": "invented" if kind == "unknown_id" else source["evidence_id"],
            "quote": source["text"] if kind == "unknown_id" else "A nonexistent quotation"}]}], "message": ""})
    with pytest.raises(ModelOutputError):
        run(IntelligenceService(library, provider(handler)).answer("encoder layers", [document_id]))


def test_comparison_requires_evidence_from_each_selected_document(library):
    first = import_pdf(library, "Encoder parameter count")
    second = import_pdf(library, "Unrelated violin music")
    calls = []
    service = IntelligenceService(library, provider(lambda req: calls.append(req)))
    result = run(service.answer("encoder parameters", [first, second], mode="compare"))
    assert result["status"] == "insufficient_evidence" and result["missing_document_ids"] == [second]
    assert calls == []
