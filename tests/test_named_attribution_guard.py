"""Conservative, source-derived model-name rejection before semantic review."""
import asyncio

import pytest

from research_workspace.intelligence import (
    AnswerPayload, Claim, ClaimSupportPayload, CoveragePayload, Evidence,
    IntelligenceService, ModelOutputError,
)
from research_workspace.library import Library


def source(block_id, text):
    return Evidence(evidence_id=f"E_{block_id}", document_id="doc", document_title="Fixture",
                    filename="fixture.pdf", run_id="frozen-run", block_id=block_id, page=1,
                    bbox=[0.1, 0.1, 0.9, 0.3], text=text)


def claim(text, *quotes):
    return Claim(text=text, citations=[{"evidence_id": f"E_{block}", "quote": quote}
                                       for block, quote in quotes])


def names(*texts):
    return IntelligenceService._named_attribution_identifiers(
        IntelligenceService._citation_spans([source(str(index), text) for index, text in enumerate(texts)]))


def missing(value, identifiers):
    return IntelligenceService._missing_named_attribution(value, identifiers)


def test_missing_named_subject_is_rejected_without_using_full_source_as_support():
    identifiers = names("We introduce ORION models. We use retrieved documents to generate text.")
    value = claim("ORION 使用检索文档生成文本。", ("0", "We use retrieved documents to generate text."))
    assert identifiers == {"orion"}
    assert missing(value, identifiers) == ["orion"]


def test_variant_does_not_support_family_but_exact_variant_remains_valid():
    identifiers = names("We build ORION models.", "ORION-Sequence models use one document for a sequence.")
    quote = ("1", "ORION-Sequence models use one document for a sequence.")
    assert missing(claim("ORION 对整条序列使用同一文档。", quote), identifiers) == ["orion"]
    assert missing(claim("ORION-Sequence 对整条序列使用同一文档。", quote), identifiers) == []
    assert missing(claim("ORION 对整条序列使用同一文档。", ("1", "ORION2 models do so.")), identifiers) == ["orion"]


def test_separate_attached_identity_quote_can_supply_name_but_is_not_auto_support():
    identifiers = names("We introduce ORION models.")
    value = claim("ORION 使用检索文档。", ("0", "We introduce ORION models."),
                  ("1", "We use retrieved documents."))
    assert missing(value, identifiers) == []


@pytest.mark.parametrize("text,quote", [
    ("奥瑞恩模型使用检索文档。", "Our model uses retrieved documents."),
    ("ORION 使用检索文档。", "奥瑞恩模型使用检索文档。"),
    ("ORION 使用检索文档。", "The model named 奥瑞恩 uses retrieved documents to generate the requested output."),
    ("ORION utilise des documents.", "Le modèle Orión utilise des documents."),
])
def test_uncertain_multilingual_aliases_are_left_to_semantic_review(text, quote):
    assert missing(claim(text, ("0", quote)), names("We introduce ORION models.")) == []


def test_common_english_words_and_task_abbreviations_are_not_model_identifiers():
    text = ("Most models share components. Many models are neural. Some models use attention. "
            "Several models exist. Different models vary. Such models are common. Specific models are compared. "
            "Single models are trained. Multiple models are combined. NLP models perform QA. "
            "MLM models predict masks. We introduce a new architecture. Our model works.")
    assert names(text) == set()
    assert missing(claim("Most NLP models perform QA.", ("0", "A system answers questions.")), names(text)) == []


def test_unrelated_known_name_does_not_make_every_claim_require_it():
    identifiers = names("We introduce ORION models.")
    assert missing(claim("神经网络可以使用注意力。", ("0", "Networks can use attention.")), identifiers) == []
    assert missing(claim("An unknown ordinary term is used.", ("0", "A term is used.")), identifiers) == []


def test_pdf_typography_normalization_preserves_actual_variant_boundaries():
    identifiers = names("We presented the Trans-\nformer.", "ＯＲＩＯＮ’s model uses attention.",
                        "ORION-\nSequence models use a document.")
    assert {"transformer", "orion", "orion-sequence"}.issubset(identifiers)
    assert missing(claim("Transformer 使用注意力。", ("0", "The Trans-\nformer uses attention.")), identifiers) == []
    assert missing(claim("Trans-\nformer 使用注意力。", ("0", "Transformer uses attention.")), identifiers) == []
    assert missing(claim("ORION 使用注意力。", ("0", "ＯＲＩＯＮ’s model uses attention.")), identifiers) == []
    assert missing(claim("ORION 使用单篇文档。", ("0", "ORION-\nSequence models use a document.")), identifiers) == ["orion"]


class SequenceProvider:
    configured = True

    def __init__(self, replies):
        self.replies = iter(replies)
        self.calls = []

    async def generate(self, prompt, payload, output_model):
        self.calls.append((payload, output_model))
        response = next(self.replies)
        assert isinstance(response, output_model)
        return response


def run(tmp_path, replies, evidence):
    provider = SequenceProvider(replies)
    service = IntelligenceService(Library(tmp_path), provider)
    result = asyncio.run(service._generate_grounded("fixture", {"task": "qa", "question": "Mechanism?"},
                                                  AnswerPayload, evidence))
    return result, provider


def test_guard_records_denial_repairs_then_keeps_judge_isolated(tmp_path):
    introduction = source("intro", "We introduce ORION models.")
    mechanism = source("mechanism", "We use retrieved documents to generate text.")
    bad = AnswerPayload(status="answered", claims=[claim("ORION 使用检索文档生成文本。", ("mechanism", mechanism.text))])
    good = AnswerPayload(status="answered", claims=[claim("ORION 使用检索文档生成文本。",
                        ("intro", introduction.text), ("mechanism", mechanism.text))])
    (result, used, audit), provider = run(tmp_path, [bad, good,
        ClaimSupportPayload(verdict="supported", reason="Both identity and mechanism are quoted."),
        CoveragePayload(unverified_statements=[], missing_aspects=[])], [introduction, mechanism])
    assert result == good and used == {"E_intro", "E_mechanism"}
    assert audit["attempts"][0]["reviews"][0]["method"] == "named_attribution_guard"
    assert audit["attempts"][0]["reviews"][0]["verdict"] == "unsupported"
    assert "orion" in provider.calls[1][0]["correction"]["problems"][0]
    assert audit["claim_model_calls"] == 1 and audit["coverage_model_calls"] == 1
    judge = next(payload for payload, model in provider.calls if model is ClaimSupportPayload)
    assert set(judge) == {"task", "claim_index", "claim", "response_kind"}
    assert judge["response_kind"] == "qa"
    assert judge["claim"] == good.claims[0].model_dump()


def test_repeated_missing_identity_is_bounded_and_never_sent_for_semantic_approval(tmp_path):
    introduction = source("intro", "We introduce ORION models.")
    mechanism = source("mechanism", "We use retrieved documents.")
    bad = AnswerPayload(status="answered", claims=[claim("ORION 使用检索文档。", ("mechanism", mechanism.text))])
    provider = SequenceProvider([bad, bad.model_copy(deep=True)])
    service = IntelligenceService(Library(tmp_path), provider)
    with pytest.raises(ModelOutputError, match="复核未通过"):
        asyncio.run(service._generate_grounded("fixture", {"task": "qa", "question": "Mechanism?"},
                                              AnswerPayload, [introduction, mechanism]))
    assert len(provider.calls) == 2
    assert all(model is AnswerPayload for _, model in provider.calls)


def test_name_presence_does_not_override_semantic_denial(tmp_path):
    cited = source("intro", "We introduce ORION models for text generation.")
    bad = AnswerPayload(status="answered", claims=[claim("ORION 的准确率为 99%。", ("intro", cited.text))])
    provider = SequenceProvider([bad, ClaimSupportPayload(verdict="unsupported", reason="No score in the quote."),
                                 bad.model_copy(deep=True)])
    service = IntelligenceService(Library(tmp_path), provider)
    with pytest.raises(ModelOutputError, match="No score"):
        asyncio.run(service._generate_grounded("fixture", {"task": "qa", "question": "Score?"},
                                              AnswerPayload, [cited]))
    assert sum(model is ClaimSupportPayload for _, model in provider.calls) == 1  # Existing cache retained.
