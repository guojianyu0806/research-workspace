"""Translation regressions from the live acceptance run; no external API calls."""

import asyncio
import re

import pytest

from research_workspace.intelligence import (
    IntelligenceService, ModelOutputError, TranslationPayload, protect_text,
    restore_text, validate_translation_language,
)
from research_workspace.library import Library
from research_workspace.jobs import JobStore


SOURCE_WITH_FORMULA = (
    "本文拟比较 RAG 与 BERT，计划在 2 个数据集上评估；尚未开展实验。"
    "请保留引用 [1] 和公式 E = mc^2。"
)


class FixtureProvider:
    configured = True

    def __init__(self, translate):
        self.translate = translate
        self.requests = []

    async def generate(self, prompt, request, schema):
        self.requests.append(dict(request))
        assert schema is TranslationPayload
        return TranslationPayload(translated_text=self.translate(request, len(self.requests)))


def test_live_formula_regression_exposes_prose_and_preserves_only_math():
    protected, replacements = protect_text(SOURCE_WITH_FORMULA)
    assert "本文拟比较 RAG 与 BERT" in protected
    assert "尚未开展实验" in protected
    assert list(replacements.values()) == ["2", "[1]", "E = mc^2"]
    assert restore_text(protected, replacements) == SOURCE_WITH_FORMULA


@pytest.mark.parametrize(("source", "formula", "prose"), [
    ("公式E=mc^2仍待验证。", "E=mc^2", "仍待验证"),
    ("The equation E = mc^2 is an example.", "E = mc^2", "is an example"),
    ("We use x + y = z in this proof.", "x + y = z", "in this proof"),
    ("当 x ≤ y 时继续。", "x ≤ y", "时继续"),
    ("设 x=-3.5e-2 后继续计算。", "x=-3.5e-2", "后继续计算"),
    ("结果使用 √(x + y)，仍需检查。", "√(x + y)", "仍需检查"),
    (r"The expression x = \frac{a}{b} is retained.", r"x = \frac{a}{b}", "is retained"),
])
def test_raw_math_does_not_swallow_adjacent_prose(source, formula, prose):
    protected, replacements = protect_text(source)
    assert formula in replacements.values()
    assert prose in protected
    assert restore_text(protected, replacements) == source


@pytest.mark.parametrize("source", [
    "E = mc^2", "x + y = z", "∑ x_i", r"\[x = \frac{a}{b}\]", "$x^2+y$",
    "```python\nresult = 12\n```", "`key = 42`", "[1, 2]", "28.4%",
])
def test_formula_and_code_only_allow_unchanged_content(source):
    protected, replacements = protect_text(source)
    for language in ("zh", "en"):
        validate_translation_language(protected, protected, language)
    assert restore_text(protected, replacements) == source


def test_literal_code_latex_citations_and_numbers_are_unchanged():
    source = (
        "Translate this explanation [1, 2]. `value = 7`\n"
        "```python\ncount = 20\n```\n"
        r"Use $x^2+y$ and \(\frac{a}{b}\) and \[x = y\]. "
        "Accuracy is 28.4%, time is -3.5e-2; https://example.invalid/a1."
    )
    protected, replacements = protect_text(source)
    assert "Translate this explanation" in protected and "Accuracy is" in protected
    for span in ("[1, 2]", "`value = 7`", "```python\ncount = 20\n```", "$x^2+y$",
                 r"\(\frac{a}{b}\)", r"\[x = y\]", "28.4%", "-3.5e-2"):
        assert span in replacements.values()
    assert restore_text(protected, replacements) == source


def test_english_hyphenated_prose_is_not_math():
    source = "The state-of-the-art approach needs a human-in-the-loop evaluation."
    assert protect_text(source) == (source, {})


def test_hyphenated_model_name_still_preserves_its_number():
    source = "The model-12 variant is tested."
    protected, replacements = protect_text(source)
    assert "The model" in protected and "variant is tested" in protected
    assert list(replacements.values()) == ["-12"]
    assert restore_text(protected, replacements) == source


def test_long_addition_is_one_span_without_line_scanning_backtracking():
    formula = "a+" * 5000 + "a"
    protected, replacements = protect_text("计算 " + formula + " 后继续。")
    assert list(replacements.values()) == [formula]
    assert "计算 " in protected and " 后继续。" in protected


@pytest.mark.parametrize("source", ["BERT MLM NSP", "Ashish Vaswani Noam Shazeer", "Google Research"])
def test_names_and_acronyms_do_not_require_invented_chinese_prose(source):
    validate_translation_language(source, source, "zh")


def translate_formula_fixture(request, attempt):
    numbers, citation, formula = re.findall(r"⟪K[a-f0-9]{12}_\d+⟫", request["text"])
    return (f"This study plans to compare RAG and BERT on {numbers} datasets; "
            f"no experiments have been conducted. Retain citation {citation} and equation {formula}.")


def test_formula_regression_translates_prose_and_preserves_experimental_limit(tmp_path):
    provider = FixtureProvider(translate_formula_fixture)
    result = asyncio.run(IntelligenceService(Library(tmp_path), provider).translate_text(SOURCE_WITH_FORMULA))
    assert "no experiments have been conducted" in result["translated_text"]
    assert "2 datasets" in result["translated_text"]
    assert "[1]" in result["translated_text"] and "E = mc^2" in result["translated_text"]
    assert len(provider.requests) == 1


def test_untranslated_chinese_gets_one_corrective_retry(tmp_path):
    provider = FixtureProvider(lambda req, count: req["text"] if count == 1 else translate_formula_fixture(req, count))
    result = asyncio.run(IntelligenceService(Library(tmp_path), provider).translate_text(SOURCE_WITH_FORMULA))
    assert len(provider.requests) == 2
    assert "translation_feedback" in provider.requests[1]
    assert "尚未开展实验" not in result["translated_text"]


@pytest.mark.parametrize(("source", "language"), [
    (SOURCE_WITH_FORMULA, "en"),
    ("The model obtains 28.4 BLEU on the benchmark [1].", "zh"),
])
def test_repeated_wrong_language_is_rejected_not_completed(tmp_path, source, language):
    provider = FixtureProvider(lambda req, count: req["text"])
    with pytest.raises(ModelOutputError, match="目标语言"):
        asyncio.run(IntelligenceService(Library(tmp_path), provider).translate_text(source, target_language=language))
    assert len(provider.requests) == 2


def test_preservation_corruption_is_not_bypassed_by_language_retry(tmp_path):
    provider = FixtureProvider(lambda req, count: "The equation is preserved.")
    with pytest.raises(ModelOutputError, match="受保护"):
        asyncio.run(IntelligenceService(Library(tmp_path), provider).translate_text(SOURCE_WITH_FORMULA))
    assert len(provider.requests) == 1


def test_queued_text_translation_uses_same_language_repair(tmp_path):
    library = Library(tmp_path)
    store = JobStore(library)
    job = store.create("translate_text", {"text": SOURCE_WITH_FORMULA, "target_language": "en"})
    provider = FixtureProvider(lambda req, count: req["text"] if count == 1 else translate_formula_fixture(req, count))
    asyncio.run(store.process_next(IntelligenceService(library, provider)))
    saved = store.get(job["id"])
    assert saved["status"] == "completed"
    assert len(provider.requests) == 2
    assert "no experiments have been conducted" in saved["result"]["translated_text"]
    assert "E = mc^2" in saved["result"]["translated_text"]
    assert "target_language_sanity" in saved["result"]["verification"]


@pytest.mark.parametrize("wrong_result", ["wrong_language", "corrupt_marker"])
def test_queued_translation_rejects_invalid_results(tmp_path, wrong_result):
    library = Library(tmp_path)
    store = JobStore(library)
    job = store.create("translate_text", {"text": SOURCE_WITH_FORMULA, "target_language": "en"})
    provider = FixtureProvider(lambda req, count: req["text"] if wrong_result == "wrong_language" else "A translation.")
    asyncio.run(store.process_next(IntelligenceService(library, provider)))
    saved = store.get(job["id"])
    assert saved["status"] == "failed"
    assert saved["result"] is None and saved["completed_items"] == []
    assert len(provider.requests) == (2 if wrong_result == "wrong_language" else 1)


def test_queued_cancellation_prevents_buying_language_repair(tmp_path):
    library = Library(tmp_path)
    store = JobStore(library)
    job = store.create("translate_text", {"text": SOURCE_WITH_FORMULA, "target_language": "en"})

    def cancelled_response(request, count):
        store.cancel(job["id"])
        return request["text"]  # The in-flight response needs repair, but cancellation wins.

    provider = FixtureProvider(cancelled_response)
    asyncio.run(store.process_next(IntelligenceService(library, provider)))
    saved = store.get(job["id"])
    assert saved["status"] == "cancelled"
    assert len(provider.requests) == 1
    assert saved["result"] is None and saved["completed_items"] == []
