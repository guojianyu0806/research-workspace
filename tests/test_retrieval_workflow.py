"""Real local job cancellation with an offline query-generation provider."""

import asyncio

import pymupdf
import pytest

from research_workspace.intelligence import IntelligenceService, RetrievalQueryPayload
from research_workspace.jobs import JobStore
from research_workspace.library import Library


@pytest.mark.parametrize("kind", ["qa", "review", "future_work"])
def test_cancel_after_expansion_stops_paid_answer_or_synthesis_in_real_jobstore(tmp_path, kind):
    library = Library(tmp_path)
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((40, 80), "LayerNorm normalizes feature activations within each example.")
        doc = library.import_document(pdf.tobytes(), "offline-fixture.pdf")["document"]["id"]
    while library.process_next_job():
        pass
    store = JobStore(library)
    task = store.create(kind, {"document_ids": [doc], "question": "层归一化有什么作用？",
                               "instruction": "分析层归一化的作用"})
    calls = []

    class Provider:
        configured = True

        async def generate(self, prompt, payload, output_model):
            calls.append(output_model)
            assert output_model is RetrievalQueryPayload, "Cancelled tasks must not start answer generation"
            store.cancel(task["id"])
            return output_model.model_validate({"terms": [{"source_phrase": "层归一化", "term": "LayerNorm"}]})

    assert asyncio.run(store.process_next(IntelligenceService(library, Provider())))
    done = store.get(task["id"])
    assert done["status"] == "cancelled"
    assert done["status"] != "failed"
    assert done["result"] is None
    assert done["cancel_requested"]
    assert calls == [RetrievalQueryPayload]
    assert not asyncio.run(store.process_next(IntelligenceService(library, Provider())))
    assert calls == [RetrievalQueryPayload]
