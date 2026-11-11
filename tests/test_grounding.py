"""Offline regression for semantically unsupported but text-matching citations."""
import asyncio
import json

import httpx
import pytest

from research_workspace.intelligence import (
    AnswerPayload, ChatCompletionProvider, ClaimRepairPayload, ClaimSupportPayload, CoveragePayload, Evidence,
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
    return ClaimSupportPayload(verdict=verdict, reason=reason)


def coverage(*, unverified=(), missing=()):
    return CoveragePayload(unverified_statements=list(unverified), missing_aspects=list(missing))


def test_caption_matching_quote_is_not_enough_and_is_repaired(tmp_path):
    unsupported = answer("模型采用编码器和解码器。", "Figure 1: Model architecture.")
    corrected = answer("模型采用编码器和解码器。", "The model uses an encoder and a decoder.")
    (result, used, audit), provider = generate(tmp_path, [unsupported,
        review("unsupported", "Caption does not specify encoder/decoder."), corrected, review("supported"), coverage()])
    assert result == corrected and used == {"E_fixture"}
    assert audit["status"] == "passed" and len(audit["attempts"]) == 2
    assert provider.calls[2][0]["correction"]["problems"]
    assert all(call[0].get("evidence", [evidence().model_dump()])[0]["run_id"] == "run"
               for call in provider.calls)


def test_repeated_semantic_failure_is_rejected_after_one_repair(tmp_path):
    bad = answer("模型得分为 99。", "The score is 28.4.")
    with pytest.raises(ModelOutputError, match="复核未通过"):
        generate(tmp_path, [bad, review("unsupported", "Wrong number"), bad])


def test_coverage_schema_cannot_replace_the_single_claim_verdict():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        ClaimSupportPayload.model_validate(coverage().model_dump())
    with pytest.raises(ValidationError):
        CoveragePayload.model_validate({**coverage().model_dump(), "verdict": "supported"})


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
        coverage(unverified=["Uncited numerical result in response_message"]), corrected])
    assert not saved.message and audit["model_checked"] and len(provider.calls) == 3
    assert provider.calls[1][0]["response_message"] == result.message


def test_unacknowledged_missing_aspect_is_sent_back_for_repair(tmp_path):
    result = answer("模型得分为28.4。", "The score is 28.4.")
    corrected = answer("模型得分为28.4。", "The score is 28.4.")
    corrected.message = "Selected excerpts do not support the requested objective."
    (_, _, audit), provider = generate(tmp_path, [result,
        review("supported"), coverage(missing=["The requested training objective is not addressed"]), corrected, coverage()])
    assert "Unanswered request aspect" in provider.calls[3][0]["correction"]["problems"][0]
    assert len(audit["attempts"]) == 2
    assert len([call for call in provider.calls if call[1] is ClaimSupportPayload]) == 1


def test_adjacent_claim_and_uncited_source_text_are_isolated_until_cited(tmp_path):
    """The old batch reviewer could see the missing attribution in another card."""
    generic = evidence().model_copy(update={
        "text": "Most competitive models use an encoder and decoder. Hidden full-block detail is not a citation."})
    attribution = evidence().model_copy(update={
        "evidence_id": "E_attribution", "block_id": "attribution",
        "document_title": "Forbidden title context",
        "text": "Model Delta follows this encoder-decoder architecture."})
    initial = AnswerPayload(status="answered", claims=[
        {"text": "Model Delta uses an encoder and decoder.", "citations": [
            {"evidence_id": "E_fixture", "quote_id": "Q1"}]},
        {"text": attribution.text, "citations": [
            {"evidence_id": "E_attribution", "quote_id": "Q1"}]},
    ])
    corrected = initial.model_copy(deep=True)
    corrected.claims[0].citations.append(corrected.claims[1].citations[0].model_copy(deep=True))
    provider = SequenceProvider([initial, review("unsupported", "The claim's quotations do not identify Model Delta."),
                                 corrected, review("supported"), coverage()])
    service = IntelligenceService(Library(tmp_path), provider)
    result, _, audit = asyncio.run(service._generate_grounded(
        "fixture", {"task": "review", "instruction": "Untrusted request contains extra facts."},
        AnswerPayload, [generic, attribution]))

    support_calls = [payload for payload, model in provider.calls if model is ClaimSupportPayload]
    assert len(support_calls) == 2
    assert all(set(payload) == {"task", "claim_index", "claim", "response_kind","citation_provenance"} for payload in support_calls)
    assert all(payload["response_kind"] == "review" for payload in support_calls)
    first = support_calls[0]
    assert first["task"] == "verify_single_claim" and first["claim_index"] == 0
    assert first["claim"]["citations"][0]["quote"] == "Most competitive models use an encoder and decoder."
    provenance=first['citation_provenance']
    assert len(provenance)==1 and provenance[0]['block_id']==generic.block_id
    assert provenance[0]['page']==generic.page and provenance[0]['bbox']==generic.bbox
    assert set(provenance[0])=={'citation_id','document_id','run_id','block_id','page','bbox'}
    first_json = json.dumps(first, ensure_ascii=False)
    assert attribution.text not in first_json
    assert "Hidden full-block" not in first_json
    assert attribution.document_title not in first_json
    assert "Untrusted request" not in first_json
    assert len(support_calls[1]["claim"]["citations"]) == 2
    assert support_calls[1]["claim"]["citations"][1]["quote"] == attribution.text
    assert len(result.claims[0].citations) == 2
    assert all(citation.quote for citation in result.claims[0].citations)
    assert audit["attempts"][0]["reviews"][0]["verdict"] == "unsupported"
    assert audit["attempts"][1]["reviews"][0]["verdict"] == "supported"
    # Whole-response coverage is reached only after all individual claims pass.
    assert [model for _, model in provider.calls].count(CoveragePayload) == 1
    assert provider.calls[-1][1] is CoveragePayload


_TRANSFORMER_LAYER_USAGE = (
    "The Transformer follows this overall architecture using stacked self-attention and point-wise, fully "
    "connected layers for both the encoder and decoder, shown in the left and right halves of Figure 1, respectively."
)
_TRANSFORMER_CLASSIFICATION = "We presented the Transformer, the first sequence transduction model based entirely on attention."
_TRANSFORMER_LAYER_COMPOSITION = (
    "The Transformer encoder and decoder each consist of stacked self-attention and point-wise, fully connected layers."
)
_GENERAL_TRANSDUCTION = "Most competitive neural sequence transduction models have an encoder-decoder structure."
_LAYER_COUNTS_ONLY = "The encoder consists of six layers. The decoder consists of six layers."
_NATIVE_LAYER_STACKS = ("The encoder is composed of a stack of N = 6 identical layers. "
                        "The decoder is also composed of a stack of N = 6 identical layers.")
_USAGE_CLAIM = "Transformer 的编码器和解码器均使用堆叠自注意力层和逐位置全连接层。"
_COMPOSITION_CLAIM = "Transformer 的编码器和解码器均由堆叠自注意力层和逐位置全连接层构成。"
_CLASSIFICATION_CLAIM = "Transformer 是序列转导模型。"
_CLASSIFICATION_ALIAS_CLAIM = "Transformer 是序列转换模型。"


def comparison_answer(transformer_text, transformer_quote_ids):
    return AnswerPayload(status="answered", claims=[
        {"text": transformer_text, "citations": [
            {"evidence_id": "E_transformer", "quote_id": quote_id} for quote_id in transformer_quote_ids]},
        {"text": "BERT 使用双向 Transformer 编码器。", "citations": [
            {"evidence_id": "E_bert", "quote_id": "Q1"}]},
    ])


def comparison_evidence():
    transformer = evidence().model_copy(update={
        "evidence_id": "E_transformer", "document_id": "transformer", "document_title": "Transformer",
        "run_id": "transformer-original-run", "block_id": "transformer-architecture", "page": 3,
        "text": " ".join((_TRANSFORMER_LAYER_USAGE, _TRANSFORMER_CLASSIFICATION,
                          _TRANSFORMER_LAYER_COMPOSITION, _GENERAL_TRANSDUCTION, _LAYER_COUNTS_ONLY,
                          _NATIVE_LAYER_STACKS)),
    })
    bert = evidence().model_copy(update={
        "evidence_id": "E_bert", "document_id": "bert", "document_title": "BERT", "run_id": "bert-original-run",
        "block_id": "bert-architecture", "page": 4,
        "text": "BERT uses a bidirectional Transformer encoder, adapted from the Transformer, a sequence transduction model.",
    })
    return [transformer, bert]


@pytest.mark.parametrize(("overstatement", "quote_ids", "reason"), [
    (_COMPOSITION_CLAIM, ["Q1"], "composition"),
    (_COMPOSITION_CLAIM, ["Q1", "Q5", "Q6"], "composition"),
    (_CLASSIFICATION_CLAIM, ["Q1", "Q4"], "sequence transduction"),
    (_CLASSIFICATION_ALIAS_CLAIM, ["Q1", "Q4"], "sequence transduction"),
    ("该架构是序列转换模型。", ["Q1", "Q4"], "sequence transduction"),
])
def test_comparison_scope_guard_checks_the_current_claims_own_quotes(tmp_path, overstatement, quote_ids, reason):
    source = comparison_evidence()
    initial = comparison_answer(overstatement, quote_ids)
    corrected = comparison_answer(_USAGE_CLAIM, ["Q1"])
    # The unsupported Transformer claim must be rejected before consulting the
    # provider. The adjacent BERT claim is independently verified and cached.
    provider = SequenceProvider([initial, review("supported"), ClaimRepairPayload(claim=corrected.claims[0]),
                                 review("supported"), coverage()])
    service = IntelligenceService(Library(tmp_path), provider)
    saved, used, audit = asyncio.run(service._generate_grounded(
        "fixture", {"task": "compare", "question": "Compare the models' architectures.",
                    "allow_isolated_claim_repair": True},
        AnswerPayload, source, required_documents=["transformer", "bert"]))

    first, final = audit["attempts"]
    rejected = first["reviews"][0]
    assert rejected["claim_index"] == 0 and rejected["claim_id"] == first["claims"][0]["claim_id"]
    assert rejected["verdict"] == "unsupported" and rejected["method"] == "literal_scope_guard"
    assert reason in rejected["reason"].lower()
    assert [payload["claim_index"] for payload, model in provider.calls if model is ClaimSupportPayload] == [1, 0]
    repair_payload, repair_model = provider.calls[2]
    assert repair_model is ClaimRepairPayload and repair_payload["rejection_reason"] == rejected["reason"]
    assert {block["document_id"] for block in repair_payload["evidence"]} == {"transformer"}
    assert used == {"E_transformer", "E_bert"} and saved.claims[0].text == _USAGE_CLAIM
    assert [citation.quote for citation in saved.claims[0].citations] == [_TRANSFORMER_LAYER_USAGE]
    assert [item["verdict"] for item in final["reviews"]] == ["supported", "supported"]
    assert final["claims"][0]["claim_id"] != rejected["claim_id"]
    assert final["claims"][1]["claim_id"] == first["claims"][1]["claim_id"]
    repair, = audit["claim_repairs"]
    assert repair["original_claim_id"] == rejected["claim_id"]
    assert repair["repaired_claim_id"] == final["claims"][0]["claim_id"]
    assert repair["claim_index"] == repair["result_claim_index"] == 0
    for index, (entry, review_entry) in enumerate(zip(final["claims"], final["reviews"], strict=True)):
        assert entry["claim_index"] == review_entry["claim_index"] == index
        assert entry["claim_id"] == review_entry["claim_id"]
        assert entry["citations"][0]["run_id"] == source[index].run_id
    assert audit["response_coverage"][0]["claim_ids"] == [claim["claim_id"] for claim in final["claims"]]


@pytest.mark.parametrize(("claim", "quote_id", "quote"), [
    (_CLASSIFICATION_CLAIM, "Q2", _TRANSFORMER_CLASSIFICATION),
    (_CLASSIFICATION_ALIAS_CLAIM, "Q2", _TRANSFORMER_CLASSIFICATION),
    (_COMPOSITION_CLAIM, "Q3", _TRANSFORMER_LAYER_COMPOSITION),
])
def test_comparison_scope_guard_accepts_explicit_support_on_the_same_card(tmp_path, claim, quote_id, quote):
    result = comparison_answer(claim, [quote_id])
    provider = SequenceProvider([result, review("supported"), review("supported"), coverage()])
    service = IntelligenceService(Library(tmp_path), provider)
    saved, _, audit = asyncio.run(service._generate_grounded(
        "fixture", {"task": "compare", "question": "Compare the models' architectures."},
        AnswerPayload, comparison_evidence(), required_documents=["transformer", "bert"]))

    assert [citation.quote for citation in saved.claims[0].citations] == [quote]
    assert [item["method"] for item in audit["attempts"][0]["reviews"]] == ["isolated_review", "isolated_review"]
    assert len(audit["attempts"]) == 1
    assert audit["status"] == "passed"


def test_comparison_accepts_original_encoder_and_also_decoder_stack_definitions(tmp_path):
    text = "Transformer 的编码器和解码器均由六个相同的层堆叠组成。"
    result = comparison_answer(text, ["Q1", "Q7", "Q8"])
    provider = SequenceProvider([result, review("supported"), review("supported"), coverage()])
    service = IntelligenceService(Library(tmp_path), provider)
    saved, _, audit = asyncio.run(service._generate_grounded(
        "fixture", {"task": "compare", "question": "比较模型的层数和堆叠组成。"},
        AnswerPayload, comparison_evidence(), required_documents=["transformer", "bert"]))

    assert saved.claims[0].text == text
    assert " ".join(citation.quote for citation in saved.claims[0].citations[1:]) == _NATIVE_LAYER_STACKS
    assert audit["status"] == "passed" and len(audit["attempts"]) == 1
    assert [entry["method"] for entry in audit["claim_reviews"]] == ["isolated_review", "isolated_review"]
    assert audit["claim_model_calls"] == 2


def test_unchanged_unsupported_claim_is_cached_but_never_accepted(tmp_path):
    bad = answer("模型得分为99。", "The score is 28.4.")
    provider = SequenceProvider([bad, review("unsupported", "Wrong number"), bad.model_copy(deep=True)])
    service = IntelligenceService(Library(tmp_path), provider)
    with pytest.raises(ModelOutputError, match="复核未通过"):
        asyncio.run(service._generate_grounded("fixture", {"task": "qa"}, AnswerPayload, [evidence()]))
    assert len(provider.calls) == 3
    assert sum(model is ClaimSupportPayload for _, model in provider.calls) == 1
    assert all(model is not CoveragePayload for _, model in provider.calls)


def test_changed_claim_text_cannot_reuse_a_supported_verdict(tmp_path):
    initial = answer("模型得分为28.4。", "The score is 28.4.")
    changed = answer("模型得分为99。", "The score is 28.4.")
    provider = SequenceProvider([initial, review("supported"), coverage(missing=["Explain the score"]),
                                 changed, review("unsupported", "The numeric value changed")])
    service = IntelligenceService(Library(tmp_path), provider)
    with pytest.raises(ModelOutputError, match="复核未通过"):
        asyncio.run(service._generate_grounded("fixture", {"task": "qa"}, AnswerPayload, [evidence()]))
    assert sum(model is ClaimSupportPayload for _, model in provider.calls) == 2
    assert sum(model is CoveragePayload for _, model in provider.calls) == 1


def test_cancellation_between_claims_stops_remaining_review_and_coverage(tmp_path):
    result = answer("模型采用编码器和解码器。", "The model uses an encoder and a decoder.")
    result.claims.extend(answer("模型得分为28.4。", "The score is 28.4.").claims)
    provider = SequenceProvider([result, review("supported")])
    service = IntelligenceService(Library(tmp_path), provider)
    class Cancelled(Exception):
        pass
    def cancel():
        if len(provider.calls) >= 2:
            raise Cancelled()
    with pytest.raises(Cancelled):
        asyncio.run(service._generate_grounded("fixture", {"task": "qa"}, AnswerPayload, [evidence()],
                                             check_cancel=cancel))
    assert len(provider.calls) == 2
    assert provider.calls[-1][0]["claim_index"] == 0


def test_excessive_semantic_review_count_is_rejected_without_partial_approval(tmp_path):
    result = AnswerPayload(status="answered", claims=[
        {"text": f"Claim {index}: 模型得分为28.4。", "citations": [
            {"evidence_id": "E_fixture", "quote": "The score is 28.4."}]} for index in range(25)])
    provider = SequenceProvider([result])
    service = IntelligenceService(Library(tmp_path), provider)
    with pytest.raises(ModelOutputError):
        asyncio.run(service._generate_grounded("fixture", {"task": "qa"}, AnswerPayload, [evidence()]))
    assert len(provider.calls) == 1
