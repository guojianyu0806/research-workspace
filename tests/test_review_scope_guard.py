"""A review cannot import language/task/stage qualifiers from uncited context."""
import pytest

from research_workspace.intelligence import Claim, IntelligenceService


def card(text, *quotes):
    return Claim(text=text, citations=[{"evidence_id": f"E_{index}", "quote": quote}
                                     for index, quote in enumerate(quotes)])


@pytest.mark.parametrize("text,quote,scope", [
    ("ORION 在开放域问答训练中最小化答案损失。", "We train ORION by minimizing the answer loss.", "open-domain scope"),
    ("ORION 使用英文语料训练。", "ORION is trained on the Encyclopaedia corpus.", "English language"),
    ("ORION 使用该语料预训练。", "ORION is trained on this corpus.", "pre-training stage"),
    ("ORION 使用文档级语料。", "ORION uses this corpus.", "document-level corpus"),
    ("ORION 在测试时替换索引。", "ORION accesses the index.", "inference/test-time scope"),
    ("ORION 使用某双语数据集的平行语料训练。", "ORION is trained on a French-to-German dataset.", "parallel/aligned corpus"),
])
def test_missing_scope_is_not_supplied_by_model_familiarity(text, quote, scope):
    assert scope in IntelligenceService._missing_review_qualifiers(card(text, quote))


def test_scope_can_be_attached_to_this_same_card_as_a_separate_real_quote():
    current = card("ORION 在开放域问答中进行预训练，使用英文文档级语料，并能在测试时检索。",
                   "ORION is pre-trained for open-domain question answering.",
                   "We use an English document-level corpus for ORION.",
                   "ORION retrieves documents at test time.")
    original = [citation.quote for citation in current.citations]
    assert IntelligenceService._missing_review_qualifiers(current) == []
    assert [citation.quote for citation in current.citations] == original


def test_ordinary_training_must_not_be_relabelled_as_pretraining():
    assert IntelligenceService._missing_review_qualifiers(card(
        "ORION 用该语料训练。", "ORION is trained on the corpus.")) == []
    assert IntelligenceService._missing_review_qualifiers(card(
        "ORION 用该语料预训练。", "ORION is trained on the corpus.")) == ["pre-training stage"]


def test_explicit_aligned_or_sentence_pair_quotes_can_supply_parallel_corpus_scope():
    for wording in ["ORION trains on aligned bilingual sentences.", "ORION trains on source-target sentence\npairs."]:
        assert IntelligenceService._missing_review_qualifiers(card("ORION使用平行语料训练。", wording)) == []


def test_parallel_qualifier_cannot_transfer_between_different_named_datasets():
    current=card("ORION在ArchiveAlpha和ArchiveBeta平行句对语料上训练。",
        "ORION is trained on the ArchiveAlpha dataset containing sentence pairs.",
        "We use the ArchiveBeta dataset containing many sentences.")
    assert any("ArchiveBeta" in missing for missing in IntelligenceService._missing_review_qualifiers(current))
    current.text="ORION使用ArchiveAlpha和ArchiveBeta数据集训练。"
    assert IntelligenceService._missing_review_qualifiers(current)==[]


def test_each_dataset_can_supply_its_own_parallel_attribute_on_the_same_card():
    current=card("ORION在ArchiveAlpha和ArchiveBeta平行语料上训练。",
        "ORION is trained on the ArchiveAlpha dataset containing sentence pairs.",
        "We use the ArchiveBeta dataset containing many sentences.",
        "The ArchiveBeta dataset is parallel text.")
    assert IntelligenceService._missing_review_qualifiers(current)==[]


def test_prediction_conditioning_alone_does_not_establish_marginalization():
    current=card("ORION在文档上进行边缘化。","ORION predicts target tokens conditioned on a document.")
    assert "marginalization operation" in IntelligenceService._missing_review_qualifiers(current)
    current=card(current.text,current.citations[0].quote,"ORION marginalizes over documents for each output.")
    assert IntelligenceService._missing_review_qualifiers(current)==[]


def test_token_prediction_does_not_silently_supply_an_unquoted_vocabulary_id():
    current=card("ORION预测被遮盖词元的原始词汇id。","ORION predicts the original masked token.")
    assert "vocabulary/token identifier" in IntelligenceService._missing_review_qualifiers(current)
