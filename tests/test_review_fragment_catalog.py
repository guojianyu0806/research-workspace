"""A review cannot select the unfinished tail of a cross-page paragraph."""
import asyncio

from research_workspace.intelligence import Evidence, IntelligenceService, SynthesisPayload
from research_workspace.library import Library


def source(text):
    return Evidence(evidence_id="E_page", document_id="doc", document_title="Fixture",
                    filename="fixture.pdf", run_id="frozen", block_id="page", page=1,
                    bbox=[0.1, 0.1, 0.9, 0.8], text=text)


def test_review_hides_cross_page_tail_without_changing_original_or_default_catalog():
    block = source("ORION uses a masked objective. The objective predicts the original id of the masked")
    normal = IntelligenceService._citation_spans([block])
    reviewed = IntelligenceService._citation_spans([block], omit_trailing_fragment=True)
    assert normal["E_page"]["Q2"].endswith("of the masked")
    assert reviewed == {"E_page": {"Q1": "ORION uses a masked objective."}}
    assert block.text.endswith("of the masked")


def test_standalone_structure_and_complete_footnoted_prose_remain_citable():
    for text in ("3 Model Architecture", "x = y + 1", "Model   Accuracy\nORION   92", "First sentence. Another finding.”2"):
        assert IntelligenceService._citation_spans([source(text)], omit_trailing_fragment=True) == IntelligenceService._citation_spans([source(text)])


def test_review_generation_cannot_reintroduce_omitted_span_id(tmp_path):
    block = source("ORION uses a masked objective. The objective predicts the original id of the masked")
    calls = []

    class Provider:
        configured = True

        async def generate(self, prompt, payload, output_model):
            calls.append(payload)
            assert output_model is SynthesisPayload
            return SynthesisPayload(status="generated", sections=[{"heading": "Objective", "claims": [{
                "text": "ORION uses a masked objective.", "citations": [{"evidence_id": "E_page",
                    "quote_id": "Q2" if len(calls) == 1 else "Q1"}]}]}])

    service = IntelligenceService(Library(tmp_path), Provider())
    result, _, audit = asyncio.run(service._generate_grounded("fixture", {"task": "review"},
                                        SynthesisPayload, [block], required_documents=["doc"]))
    assert [span["quote_id"] for span in calls[0]["evidence"][0]["citation_spans"]] == ["Q1"]
    assert "无效的原文片段" in audit["attempts"][0]["problems"][0]
    assert result.sections[0].claims[0].citations[0].quote == "ORION uses a masked objective."
