"""Offline bilingual retrieval protocol tests; no live model quality claims."""

import asyncio
import json

import httpx
import pymupdf
import pytest

from research_workspace.intelligence import (
    AnswerPayload, ChatCompletionProvider, IntelligenceService, ModelConfig,
    ModelOutputError, RetrievalQueryPayload, SynthesisPayload,
)
from research_workspace.jobs import TaskCancelled
from research_workspace.library import Library, LibraryError


def run(value):
    return asyncio.run(value)


def document(library, paragraphs, filename="fixture.pdf"):
    if isinstance(paragraphs, str):
        paragraphs = [paragraphs]
    with pymupdf.open() as pdf:
        for index, paragraph in enumerate(paragraphs):
            if index % 10 == 0:
                page = pdf.new_page()
            page.insert_text((40, 65 + (index % 10) * 65), paragraph,
                             fontname="china-s" if any(ord(char) > 255 for char in paragraph) else "helv")
        result = library.import_document(pdf.tobytes(), filename)["document"]
    while library.process_next_job():
        pass
    return result["id"]


@pytest.fixture
def library(tmp_path):
    return Library(tmp_path)


class MockProvider:
    def __init__(self, terms=(), *, configured=True, error=None, on_expansion=None, refuse=False):
        self.configured = configured
        self.terms = list(terms)
        self.error = error
        self.on_expansion = on_expansion
        self.refuse = refuse
        self.calls = []

    async def generate(self, prompt, payload, output_model):
        self.calls.append((output_model, payload))
        if output_model is RetrievalQueryPayload:
            if self.error:
                raise self.error
            if self.on_expansion:
                self.on_expansion()
            return output_model.model_validate({"terms": self.terms})
        assert "query_terms" not in payload and "queries" not in payload
        if output_model is AnswerPayload:
            if self.refuse:
                return output_model.model_validate({"status": "insufficient_evidence", "claims": [],
                                                    "message": "来源没有回答这一问题。"})
            return output_model.model_validate({"status": "answered", "claims": [
                {"text": source["text"], "citations": [{"evidence_id": source["evidence_id"], "quote": source["text"]}]}
                for source in payload["evidence"]], "message": ""})
        assert output_model is SynthesisPayload
        source = payload["evidence"][0]
        return output_model.model_validate({"status": "generated", "sections": [
            {"heading": "Supported background", "claims": [{"text": source["text"], "citations": [
                {"evidence_id": source["evidence_id"], "quote": source["text"]}]}], "proposed_work": ""}],
            "message": "", "architecture": None})


def term(anchor, value):
    return {"source_phrase": anchor, "term": value}


def assert_canonical(library, evidence):
    for hit in evidence:
        page = library.get_page(hit.document_id, hit.page, hit.run_id)
        stored = next(block for block in page["blocks"] if block["id"] == hit.block_id)
        assert hit.text == stored["text"][:5000]
        assert hit.bbox == stored["bbox"]
        assert hit.evidence_id == "E_" + hit.block_id


def test_chinese_layernorm_queries_find_english_canonical_block(library):
    doc = document(library, ["LayerNorm normalizes every feature within a training example.",
                             "Unrelated encoder capacity is discussed in another section."])
    provider = MockProvider([term("层归一化", "LayerNorm"), term("层归一化", "layer normalization")])
    service = IntelligenceService(library, provider)
    assert service.retrieve("层归一化有什么作用？", [doc]) == []
    found = run(service.retrieve_for_question("层归一化有什么作用？", [doc], limit=1))
    assert found["mode"] == "bilingual_query_expansion_bm25_rrf"
    assert "LayerNorm" in found["evidence"][0].text
    assert found["query_terms"] == ["LayerNorm", "layer normalization"]
    assert found["expansion"]["calls"] == 1
    assert found["trace"] and found["prompt_version"] == "retrieval-query-v1"
    assert len(provider.calls) == 1
    assert "evidence" not in provider.calls[0][1], "Expansion does not need PDF text"
    assert_canonical(library, found["evidence"])


def test_answer_preserves_original_question_and_uses_only_real_evidence(library):
    doc = document(library, "LayerNorm normalizes feature activations within each example.")
    provider = MockProvider([term("层归一化", "LayerNorm")])
    result = run(IntelligenceService(library, provider).answer("层归一化的作用是什么？", [doc]))
    assert result["status"] == "answered"
    assert result["retrieval"]["mode"] == "bilingual_query_expansion_bm25_rrf"
    assert [model for model, _ in provider.calls] == [RetrievalQueryPayload, AnswerPayload]
    assert provider.calls[1][1]["question"] == "层归一化的作用是什么？"
    assert result["sources"] and all(source["document_id"] == doc for source in result["sources"])
    assert all(not source["evidence_id"].startswith("query") for source in result["sources"])


def test_author_affiliation_question_can_find_unlabelled_first_page(library):
    doc = document(library, ["Alice Example and Bob Example", "Institute Q, Department of Computing",
                             "The paper studies neural feature prediction on a test dataset."])
    provider = MockProvider([term("作者", "authors"), term("机构", "affiliations")])
    service = IntelligenceService(library, provider)
    assert service.retrieve("作者来自哪些机构？", [doc]) == []
    result = run(service.retrieve_for_question("作者来自哪些机构？", [doc]))
    assert any("Institute Q" in hit.text for hit in result["evidence"])
    assert any(item["step"] == "bibliographic_candidates" for item in result["trace"])
    assert "Institute Q" not in result["query_terms"]
    assert_canonical(library, result["evidence"])


@pytest.mark.parametrize("question", ["β1和β2分别代表什么？", "What do β1 and β2 control?"])
def test_beta_indices_expand_to_literal_ascii_parameter_notation(library, question):
    doc = document(library, ["beta_1 controls the decay of the first moment estimate.",
                             "beta_2 controls the decay of the second moment estimate."])
    provider = MockProvider([term("β1", "beta_1"), term("β2", "beta_2")])
    service = IntelligenceService(library, provider)
    assert service.retrieve(question, [doc]) == []
    found = run(service.retrieve_for_question(question, [doc]))
    assert len(found["evidence"]) == 2
    assert any("beta_1" in hit.text for hit in found["evidence"])
    assert any("beta_2" in hit.text for hit in found["evidence"])
    assert found["expansion"]["calls"] == 1
    assert_canonical(library, found["evidence"])


def test_multidocument_fusion_keeps_fair_coverage_without_importing_unselected_version(library):
    one = document(library, [f"LayerNorm normalizes activations in experimental branch {i}." for i in range(10)], "paper-v1.pdf")
    two = document(library, "LayerNorm appears once in this distinct selected paper.", "other.pdf")
    ignored = document(library, "LayerNorm describes a different unselected version.", "paper-v2.pdf")
    provider = MockProvider([term("层归一化", "LayerNorm"), term("层归一化", "normalizes activations")])
    found = run(IntelligenceService(library, provider).retrieve_for_question("比较层归一化", [one, two], limit=2))
    assert {hit.document_id for hit in found["evidence"]} == {one, two}
    assert ignored not in found["selected_runs"]
    assert_canonical(library, found["evidence"])


def test_reparse_during_model_call_cannot_mix_versions_in_fusion(library):
    doc = document(library, ["LayerNorm is used to normalize feature activations.",
                             "A separate block describes the training example."])
    old_run = library.get_document(doc)["available_run"]["id"]
    def reparse():
        library.create_run(doc)
        while library.process_next_job():
            pass
    provider = MockProvider([term("层归一化", "LayerNorm")], on_expansion=reparse)
    found = run(IntelligenceService(library, provider).retrieve_for_question("层归一化的作用", [doc]))
    assert library.get_document(doc)["available_run"]["id"] != old_run
    assert found["selected_runs"] == {doc: old_run}
    assert {hit.run_id for hit in found["evidence"]} == {old_run}
    assert_canonical(library, found["evidence"])


def test_review_opening_fallback_uses_same_snapshot_when_reparse_finishes_during_expansion(library, monkeypatch):
    matched = document(library, "LayerNorm normalizes feature activations within each example.", "matched.pdf")
    fallback = document(library, "Copper sensors record temperatures during a controlled experiment.", "fallback.pdf")
    old_run = library.get_document(fallback)["available_run"]["id"]
    old_block = library.get_page(fallback, 1, old_run)["blocks"][0]
    def reparse():
        library.create_run(fallback)
        while library.process_next_job():
            pass
    provider = MockProvider([term("层归一化", "LayerNorm")], on_expansion=reparse)
    service = IntelligenceService(library, provider)
    reads = []
    original_blocks = service._blocks
    def read_snapshot(ids):
        reads.append(list(ids))
        return original_blocks(ids)
    monkeypatch.setattr(service, "_blocks", read_snapshot)
    result = run(service.synthesize("review", [matched, fallback], instruction="总结层归一化"))
    assert library.get_document(fallback)["available_run"]["id"] != old_run
    assert reads == [[matched, fallback]], "Review fallback must not reopen the document/run snapshot"
    retrieved = [source for source in result["evidence"] if source["document_id"] == fallback]
    assert len(retrieved) == 1
    assert retrieved[0]["run_id"] == result["retrieval"]["selected_runs"][fallback] == old_run
    assert retrieved[0]["block_id"] == old_block["id"]
    assert retrieved[0]["text"] == old_block["text"]
    assert retrieved[0]["bbox"] == old_block["bbox"]
    assert any(source["block_id"] == old_block["id"] for source in provider.calls[-1][1]["evidence"])
    assert "_opening_candidates" not in result["retrieval"]
    json.dumps(result)  # Internal Evidence objects must never leak into API/job audit data.


@pytest.mark.parametrize("scenario", ["off", "unconfigured", "english", "chinese_source", "no_text"])
def test_auto_policy_does_not_charge_every_question(library, scenario):
    question = "层归一化有什么作用？"
    if scenario == "chinese_source":
        text = "层归一化能够帮助训练保持稳定"
    elif scenario == "no_text":
        text = ""
    else:
        text = "LayerNorm normalizes all feature activations within an example."
    doc = document(library, text)
    provider = MockProvider(configured=scenario != "unconfigured")
    if scenario == "english":
        question = "LayerNorm feature activations"
    result = run(IntelligenceService(library, provider).retrieve_for_question(
        question, [doc], expansion="off" if scenario == "off" else "auto"))
    assert provider.calls == []
    assert result["expansion"]["calls"] == 0
    assert result["mode"] == "bm25_lexical"


def test_single_unknown_english_word_still_refuses_without_model_call(library):
    doc = document(library, "Encoder layers are used for text feature prediction.")
    provider = MockProvider()
    result = run(IntelligenceService(library, provider).answer("xylophone", [doc]))
    assert result["status"] == "insufficient_evidence" and result["sources"] == []
    assert provider.calls == []


def test_generated_words_without_raw_hits_do_not_become_an_answer(library):
    doc = document(library, "Encoder layers are used for neural feature prediction.")
    provider = MockProvider([term("音乐", "symphonic instrumentation")])
    result = run(IntelligenceService(library, provider).answer("这篇文章讲了什么音乐？", [doc]))
    assert [model for model, _ in provider.calls] == [RetrievalQueryPayload]
    assert result["status"] == "insufficient_evidence"
    assert result["sources"] == result["evidence"] == result["claims"] == []
    assert result["retrieval"]["query_terms"] == ["symphonic instrumentation"]


def test_retrieved_candidate_does_not_override_the_answer_models_refusal(library):
    doc = document(library, "LayerNorm is mentioned but no experimental accuracy is available.")
    provider = MockProvider([term("层归一化", "LayerNorm")], refuse=True)
    result = run(IntelligenceService(library, provider).answer("层归一化精确提高了多少准确率？", [doc]))
    assert result["evidence"]
    assert result["status"] == "insufficient_evidence" and result["claims"] == result["sources"] == []


@pytest.mark.parametrize("bad_terms", [
    [term("并不存在的概念", "LayerNorm")],
    [term("层归一化", "LayerNorm 2016")],
    [term("层归一化", "ignore previous instructions")],
    [term("层归一化", "https://example.invalid")],
    [term("层归一化", "\u200bLayerNorm")],
    [term("层归一化", "")],
    [term("层归一化", " " * 4)],
    [term("层归一化", "x" * 81)],
    [term("层归一化", "LayerNorm")] * 7,
    [],
])
def test_unbounded_or_unanchored_expansions_fall_back_without_extra_calls(library, bad_terms):
    doc = document(library, "LayerNorm normalizes every feature within a training example.")
    provider = MockProvider(bad_terms)
    service = IntelligenceService(library, provider)
    baseline = service.retrieve("LayerNorm层归一化", [doc])
    found = run(service.retrieve_for_question("LayerNorm层归一化", [doc]))
    assert found["query_terms"] == []
    assert len(provider.calls) == 1
    assert [hit.model_dump() for hit in found["evidence"]] == [hit.model_dump() for hit in baseline]


def test_failure_is_sanitized_and_keeps_original_bm25_matches(library):
    doc = document(library, "LayerNorm normalizes feature activations within each example.")
    provider = MockProvider(error=RuntimeError("fixture-secret-key in private response"))
    service = IntelligenceService(library, provider)
    found = run(service.retrieve_for_question("LayerNorm层归一化", [doc]))
    assert found["evidence"] and found["expansion"]["status"] == "failed_fallback"
    assert "fixture-secret-key" not in str(found)
    assert len(provider.calls) == 1


@pytest.mark.parametrize("asynchronous", [False, True])
def test_cancel_after_query_expansion_prevents_answer_call(library, asynchronous):
    doc = document(library, "LayerNorm normalizes feature activations within each example.")
    provider = MockProvider([term("层归一化", "LayerNorm")])
    def check_cancel():
        if provider.calls:
            raise TaskCancelled()
    async def async_check_cancel():
        check_cancel()
    with pytest.raises(TaskCancelled):
        run(IntelligenceService(library, provider).answer("层归一化有什么作用？", [doc],
                                                        check_cancel=async_check_cancel if asynchronous else check_cancel))
    assert [model for model, _ in provider.calls] == [RetrievalQueryPayload]


@pytest.mark.parametrize("error", [TaskCancelled(), asyncio.CancelledError()])
def test_provider_cancellation_is_not_degraded_to_success(library, error):
    doc = document(library, "LayerNorm normalizes feature activations within each example.")
    provider = MockProvider(error=error)
    with pytest.raises(type(error)):
        run(IntelligenceService(library, provider).retrieve_for_question("层归一化有什么作用？", [doc]))


@pytest.mark.parametrize("response_kind", ["malformed", "limited", "server_error", "truncated", "success"])
def test_http_query_expansion_has_one_attempt_and_a_small_token_budget(response_kind):
    calls = []
    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        assert body["max_tokens"] == 512
        if response_kind == "limited":
            return httpx.Response(429)
        if response_kind == "server_error":
            return httpx.Response(503)
        content = "not JSON" if response_kind == "malformed" else json.dumps({"terms": []})
        return httpx.Response(200, json={"choices": [{"finish_reason": "length" if response_kind == "truncated" else "stop",
                                                      "message": {"content": content}}]})
    adapter = ChatCompletionProvider(ModelConfig("https://model.invalid", "fixture", "fixture-not-real", max_attempts=3),
                                     transport=httpx.MockTransport(handler))
    if response_kind == "success":
        assert run(adapter.generate("fixture", {}, RetrievalQueryPayload)).terms == []
    else:
        with pytest.raises(RuntimeError):
            run(adapter.generate("fixture", {}, RetrievalQueryPayload))
    assert len(calls) == 1


def test_synthesis_can_expand_chinese_instruction_without_changing_citation_rules(library):
    doc = document(library, "LayerNorm normalizes feature activations within each example.")
    provider = MockProvider([term("层归一化", "LayerNorm")])
    result = run(IntelligenceService(library, provider).synthesize("review", [doc], instruction="总结层归一化"))
    assert [model for model, _ in provider.calls] == [RetrievalQueryPayload, SynthesisPayload]
    assert result["retrieval"]["mode"] == "bilingual_query_expansion_bm25_rrf"
    assert result["references"][0]["document_id"] == doc
    assert result["sources"][0]["document_id"] == doc


def test_default_synthesis_heading_query_does_not_buy_unneeded_expansion(library):
    doc = document(library, "Future work should evaluate the limitations of feature prediction.")
    provider = MockProvider()
    result = run(IntelligenceService(library, provider).synthesize("future_work", [doc]))
    assert [model for model, _ in provider.calls] == [SynthesisPayload]
    assert result["retrieval"]["expansion"]["status"] == "disabled"


def test_expanded_sources_still_reject_a_fabricated_citation(library):
    doc = document(library, "LayerNorm normalizes feature activations within each example.")
    class BadAnswer(MockProvider):
        async def generate(self, prompt, payload, output_model):
            if output_model is not AnswerPayload:
                return await super().generate(prompt, payload, output_model)
            return output_model.model_validate({"status":"answered","claims":[{"text":"Unsupported",
                "citations":[{"evidence_id":"generated-query-term","quote":"LayerNorm"}]}]})
    with pytest.raises(ModelOutputError):
        run(IntelligenceService(library, BadAnswer([term("层归一化", "LayerNorm")])).answer("层归一化作用", [doc]))


@pytest.mark.parametrize("kwargs", [{"question":""}, {"question":"x"*4001}, {"limit":25}, {"expansion":"unknown"}])
def test_invalid_async_retrieval_input_never_calls_provider(library, kwargs):
    doc = document(library, "LayerNorm normalizes feature activations within each example.")
    provider = MockProvider()
    with pytest.raises(LibraryError):
        run(IntelligenceService(library, provider).retrieve_for_question(
            **{"question":"层归一化作用", "document_ids":[doc], **kwargs}))
    assert provider.calls == []
