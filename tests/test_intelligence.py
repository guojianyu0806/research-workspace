"""Offline protocol/guardrail tests. No test contacts a real model provider."""

import asyncio
import json
from pathlib import Path

import httpx
import pymupdf
import pytest

from research_workspace.intelligence import (
    AnswerPayload, ChatCompletionProvider, IntelligenceService, ModelConfig,
    ModelNotConfigured, ModelOutputError, ModelProviderError, protect_text,
    restore_text, split_protected_text, tokenize,
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
    if data.get("task") == "verify_response_coverage":
        return envelope({"unverified_statements":[],"missing_aspects":[]})
    return envelope({"status": "answered", "claims": [
        {"text": item["text"], "citations": [{"evidence_id": item["evidence_id"], "quote": item["text"]}]}
        for item in data["evidence"]
    ], "message": ""})


def test_unconfigured_service_never_invokes_transport(library):
    calls = []
    adapter = ChatCompletionProvider(ModelConfig(), transport=httpx.MockTransport(lambda req: calls.append(req)))
    service = IntelligenceService(library, adapter)
    assert service.capabilities()["configured"] is False
    assert set(service.capabilities()) == {"configured", "prompt_version", "retrieval", "semantic", "max_documents", "external_freshness_verification", "bounded_future_fulltext_verification"}
    assert service.capabilities()['bounded_future_fulltext_verification'] is True
    assert service.capabilities()['external_freshness_verification'] is False
    assert service.capabilities()['semantic']['status']=='disabled'
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


def test_deepseek_uses_documented_non_thinking_json_mode():
    def handler(request):
        body = json.loads(request.content)
        assert str(request.url) == 'https://api.deepseek.com/chat/completions'
        assert body['thinking'] == {'type':'disabled'}
        assert body['response_format'] == {'type':'json_object'}
        return envelope({'status':'insufficient_evidence','claims':[],'message':'fixture'})
    adapter = ChatCompletionProvider(ModelConfig('https://api.deepseek.com','deepseek-flash','fixture-not-real'),
                                     transport=httpx.MockTransport(handler))
    assert run(adapter.generate('test',{},AnswerPayload)).status == 'insufficient_evidence'


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


def test_read_timeout_is_not_automatically_replayed():
    calls = []
    def handler(request):
        calls.append(1)
        raise httpx.ReadTimeout("private request information", request=request)
    with pytest.raises(ModelProviderError) as caught:
        run(provider(handler).generate("test", {}, AnswerPayload))
    assert len(calls) == 1 and "private request" not in str(caught.value)
    assert "结果未知" in str(caught.value)


def test_connection_failure_retries_are_bounded():
    calls = []
    def handler(request):
        calls.append(1)
        raise httpx.ConnectError("private network information",request=request)
    with pytest.raises(ModelProviderError):
        run(provider(handler).generate("test",{},AnswerPayload))
    assert len(calls)==3


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


def translation_echo(request):
    payload = json.loads(json.loads(request.content)["messages"][1]["content"])
    text = payload["text"]
    if payload["target_language"] == "zh":
        text = (text.replace("Real source has ", "原文包含 ").replace(" layers ", " 层 ")
                .replace("First source text", "第一段原文").replace("Second source text", "第二段原文"))
    return envelope({"translated_text": text})


def test_translation_preserves_code_math_references_and_numbers(library):
    source = "Result 12.5% on 2026 data [11].\n`value = 7` and $x^2 + y$.\n```python\ncount = 20\n```\nhttps://example.invalid/a1"
    service = IntelligenceService(library, provider(translation_echo))
    result = run(service.translate_text(source, target_language="en"))
    assert result["source_text"] == source and result["translated_text"] == source
    assert result["prompt_version"] and result["trace"]


@pytest.mark.parametrize("mutation", ["delete", "duplicate", "new_number"])
def test_translation_rejects_marker_or_number_corruption(library, mutation):
    def handler(request):
        payload = json.loads(json.loads(request.content)["messages"][1]["content"])
        text = payload["text"]
        if mutation == "delete":
            text = "Deleted all preserved values"
        elif mutation == "duplicate":
            text = text + text
        else:
            text = text + " 999"
        return envelope({"translated_text": text})
    with pytest.raises(ModelOutputError):
        run(IntelligenceService(library, provider(handler)).translate_text("The parameter is 100.", target_language="zh"))


def test_long_translation_reports_segments_without_truncating(library):
    source = ("A sentence about research. " * 410) + "Last value is 0.998."
    progress = []
    result = run(IntelligenceService(library, provider(translation_echo)).translate_text(source, progress=progress.append))
    assert len(result["segments"]) >= 3
    assert "".join(segment["source_text"] for segment in result["segments"]) == source
    assert result["translated_text"].endswith("Last value is 0.998.")
    assert len(progress) == len(result["segments"])


def test_translation_scope_reports_missing_pages_and_binds_real_block(library):
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((40, 80), "Real source has 12 layers [1].")
        pdf.new_page()
        doc = library.import_document(pdf.tobytes(), "mixed.pdf")["document"]
    library.process_next_job()
    service = IntelligenceService(library, provider(translation_echo))
    selection = service.list_translation_blocks(doc["id"])
    assert selection["total_pages"] == 2 and selection["parsed_pages"] == 1
    assert selection["missing_pages"] == [{"page": 2, "status": "no_extractable_text"}]
    block = selection["blocks"][0]
    block["text"] = "Injected content"
    translated = run(service.translate_block(block))
    assert translated["block_id"] == block["block_id"]
    assert translated["source_text"] == "Real source has 12 layers [1]."
    progress = []
    result = run(service.translate(doc["id"], progress=progress.append))
    assert result["status"] == "partial" and result["completed_blocks"] == 1
    assert progress[0]["block_id"] == block["block_id"]


def test_translation_callback_can_cancel_without_losing_completed_result(library):
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((40, 80), "First source text")
        pdf.new_page().insert_text((40, 80), "Second source text")
        doc = library.import_document(pdf.tobytes(), "two-pages.pdf")["document"]
    library.process_next_job()
    calls, completed = [], []
    def handler(request):
        calls.append(1)
        return translation_echo(request)
    def on_progress(block):
        completed.append(block)
        raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        run(IntelligenceService(library, provider(handler)).translate(doc["id"], progress=on_progress))
    assert len(calls) == 1 and len(completed) == 1
    assert completed[0]["page"] == 1


def test_split_does_not_cut_protected_marker():
    source = "中" * 3990 + "123.45" + "文" * 20
    protected, replacements = protect_text(source)
    chunks = split_protected_text(protected)
    assert "".join(chunks) == protected
    assert "".join(restore_text(chunk, replacements) for chunk in chunks) == source


@pytest.mark.parametrize("schema_name,budget,reply", [
    ("ClaimSupportPayload", 1024, {"verdict": "supported", "reason": ""}),
    ("CoveragePayload", 2048, {"unverified_statements": [], "missing_aspects": []}),
    ("ReviewMessageRepairPayload", 2048, {"status": "generated", "message": ""}),
    ("ReviewClaimCoveragePayload", 4096, {"answers": []}),
    ("ReviewCoveragePayload", 8192, {"requested_aspects": ["structure"], "assessments": [{
        "document_id": "doc", "aspect": "structure", "answer_claim_indices": [0],
        "gap_is_appropriate": False, "reason": "Fixture coverage."}], "unverified_statements": []}),
])
def test_short_review_uses_bounded_output_budget(schema_name, budget, reply):
    from research_workspace import intelligence
    requests = []
    def handler(request):
        requests.append(json.loads(request.content))
        return envelope(reply)
    run(provider(handler).generate("fixture", {}, getattr(intelligence, schema_name)))
    assert requests[0]["max_tokens"] == budget


def synthesis_reply(request):
    payload = json.loads(json.loads(request.content)["messages"][1]["content"])
    if payload["task"] == "verify_review_claim_coverage":
        current = payload["claim"]
        return envelope({"answers": [{
            "document_id": cite["document_id"], "aspect": aspect,
            "subject_citation_id": cite["citation_id"], "answer_citation_id": cite["citation_id"],
            "reason": "Fixture source coverage.",
        } for cite in current["citations"] for aspect in payload["requested_aspects"]]})
    if payload["task"] == "verify_single_claim":
        return envelope({"verdict": "supported", "reason": ""})
    if payload["task"] == "verify_response_coverage" and payload.get("response_kind") == "review":
        source_documents = {source["evidence_id"]: source["document_id"] for source in payload["evidence"]}
        return envelope({"requested_aspects": ["structure"], "assessments": [{
            "document_id": document_id, "aspect": "structure", "answer_claim_indices": [
                next(item["claim_index"] for item in payload["claims"] if any(
                    source_documents[cite["evidence_id"]] == document_id for cite in item["citations"]))],
            "gap_is_appropriate": False, "reason": "Fixture coverage.",
        } for document_id in payload["selected_document_ids"]], "unverified_statements": []})
    if payload["task"] == "verify_response_coverage":
        return envelope({"unverified_statements": [], "missing_aspects": []})
    source = payload["evidence"][0]
    claim = {"text": source["text"], "citations": [{"evidence_id": source["evidence_id"], "quote": source["text"]}]}
    headings = ["Abstract", "Introduction", "Related Work"] if payload["task"] == "draft" else ["Supported background"]
    architecture = {"nodes": [{"id": "Input", "label": "Input text"}, {"id": "Model", "label": "Proposed model"}],
                    "edges": [{"source": "Input", "target": "Model"}]} if payload["task"] == "draft" else None
    return envelope({"status": "generated", "sections": [{"heading": heading, "claims": [claim],
        "proposed_work": "An untested proposal" if payload["task"] == "draft" else ""} for heading in headings],
        "message": "", "architecture": architecture})


@pytest.mark.parametrize("kind", ["review", "future_work", "draft"])
def test_synthesis_sources_are_constructed_from_actual_documents(library, kind):
    document_id = import_pdf(library, "Future work needs better encoder evaluation. Abstract results are limited.", "actual.pdf")
    result = run(IntelligenceService(library, provider(synthesis_reply)).synthesize(kind, [document_id], instruction="encoder evaluation"))
    assert result["status"] == "generated" and result["references"][0]["filename"] == "actual.pdf"
    assert result["references"][0]["document_id"] == document_id
    assert result["prompt_version"] and result["trace"]
    if kind == "future_work":
        assert result["future_work_verification"] == "evidence_insufficient"
        assert result["search_time_range"] is None
    if kind == "draft":
        assert "## Abstract" in result["content"] and "## References" in result["content"]
        assert result["architecture"]["edges"][0] == {"source": "Input", "target": "Model"}
        assert result["architecture_status"] == "proposed_design_not_experimental_evidence"


def test_unknown_citation_in_synthesis_is_rejected(library):
    document_id = import_pdf(library, "Abstract encoder result")
    def handler(request):
        return envelope({"status": "generated", "sections": [{"heading": "Summary", "claims": [
            {"text": "Unsupported", "citations": [{"evidence_id": "unknown", "quote": "Fabricated"}]}],
            "proposed_work": ""}], "message": "", "architecture": None})
    with pytest.raises(ModelOutputError):
        run(IntelligenceService(library, provider(handler)).synthesize("review", [document_id]))


def test_all_model_operations_report_unconfigured(library):
    service = IntelligenceService(library, ChatCompletionProvider(ModelConfig()))
    operations = [service.translate_text("source"), service.translate_block({}), service.translate("unknown"),
                  service.synthesize("review", ["unknown"])]
    for operation in operations:
        with pytest.raises(ModelNotConfigured):
            run(operation)


def test_provider_rejects_truncated_completion():
    calls = []
    def handler(request):
        calls.append(1)
        request_payload = json.loads(request.content)
        assert request_payload["max_tokens"] == 8192
        return httpx.Response(200, json={"choices": [{"finish_reason": "length", "message": {
            "content": json.dumps({"status": "insufficient_evidence", "claims": [], "message": "truncated"})}}]})
    with pytest.raises(ModelOutputError, match="截断"):
        run(provider(handler).generate("test", {}, AnswerPayload))
    assert len(calls) == 3
