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
