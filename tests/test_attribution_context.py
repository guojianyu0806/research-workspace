"""Bounded attribution recall uses canonical blocks, never synthesized evidence."""

import asyncio
from collections import Counter

from research_workspace.intelligence import Evidence, IntelligenceService, MAX_EVIDENCE, RetrievalQueryPayload


def block(document, identifier, text, *, page=1, y=0.2, x=0.1, run=None):
    return Evidence(evidence_id="E_" + identifier, document_id=document,
                    document_title="Unrelated title", filename=document + ".pdf",
                    run_id=run or document + "-frozen", block_id=identifier,
                    page=page, bbox=[x, y, x + 0.35, y + 0.05], text=text)


def corpus(documents=("one", "two", "three")):
    sources, seeds = [], []
    for document in documents:
        seed = block(document, document + "-detail", "The training objective predicts missing signals from surrounding observations.", page=2)
        seeds.append(seed)
        sources.append(seed)
        for i in range(5):
            sources.append(block(document, f"{document}-intro-{i}",
                                 f"We introduce System{i}, a new architecture using a memory for prediction of missing signals.",
                                 y=0.1 + i / 10))
    return sources, seeds


def additions(sources, seeds, documents):
    service = IntelligenceService(None, None)
    return service._attribution_context(
        [{"text": "architecture training objective memory prediction"}], sources, seeds,
        documents, service._lexical_index(sources))


def test_attribution_additions_are_fair_bounded_independent_canonical_blocks():
    sources, seeds = corpus()
    found = additions(sources, seeds, ["one", "two", "three"])
    assert len(found) == 6
    assert Counter(item.document_id for item in found) == {"one": 2, "two": 2, "three": 2}
    original = {item.block_id: item for item in sources}
    assert not {item.block_id for item in seeds} & {item.block_id for item in found}
    for item in found:
        assert item.model_dump() == original[item.block_id].model_dump()


def test_each_document_has_its_own_cap_and_snapshot_run_boundary():
    sources, seeds = corpus(("one",))
    sources.append(block("one", "new-version", "We introduce Quill, an architecture using memory for prediction.", run="one-new"))
    sources.append(block("unselected", "outside", "We introduce Heron, an architecture using memory for prediction."))
    found = additions(sources, seeds, ["one"])
    assert len(found) == 3
    assert all(item.document_id == "one" and item.run_id == "one-frozen" for item in found)


def test_total_evidence_cap_and_same_document_text_deduplication():
    sources, seeds = corpus(("one",))
    sources.append(sources[1].model_copy(update={"block_id": "repeat", "evidence_id": "E_repeat"}))
    found = additions(sources, seeds, ["one"])
    assert len({item.text for item in found}) == len(found)
    fillers = [block("one", f"existing-{i}", f"Already selected canonical observation {i}.", page=5)
               for i in range(MAX_EVIDENCE - 2)]
    sources.extend(fillers)
    found = additions(sources, seeds + fillers, ["one"])
    assert len(found) == 1
    assert additions(sources, seeds + fillers + found, ["one"]) == []


def test_method_introduction_beats_code_footnotes_and_unrelated_named_material():
    seed = block("one", "detail", "The prediction objective reconstructs missing signals.", page=2)
    anchor = block("one", "anchor", "We pre-train Lark using two prediction objectives described in this section.", page=2)
    code = block("one", "code", "We introduce Lark code for prediction objectives available at https://example.invalid/code.")
    unrelated = block("one", "other", "We introduce Orchard, a dataset containing weather records from remote islands.")
    sources = [seed, code, unrelated, anchor]
    assert [item.block_id for item in additions(sources, [seed], ["one"])] == ["anchor"]


def test_two_column_section_context_uses_reading_order_without_joining_text():
    heading = block("one", "heading", "2\nTraining Objectives", y=0.1)
    explanation = block("one", "explanation", "The procedure uses two complementary prediction objectives over masked observations. "
                        "The first recovers missing input signals while the second matches observations from related sources.", y=0.3)
    seed = block("one", "detail", "The prediction objective minimizes error on held-out observations and uses their complete representation. "
                 "Each missing item is predicted from its available context without adding information from outside this input.", x=0.55, y=0.1)
    # A PDF extraction can interleave the top of the right column before the
    # bottom of the left column, even though that is not the reading order.
    sources = [heading, seed, explanation]
    assert [item.block_id for item in IntelligenceService._reading_order(sources)] == ["heading", "explanation", "detail"]
    found = additions(sources, [seed], ["one"])
    assert [item.block_id for item in found] == ["explanation"]
    assert found[0].text == explanation.text


def test_context_is_opt_in_and_uses_only_the_snapshot_taken_before_model_await():
    old_seed = block("one", "old-detail", "The prediction objective reconstructs missing signals.")
    old_anchor = block("one", "old-intro", "We pre-train Lark using two prediction objectives in this section.")
    new_anchor = block("one", "new-intro", "We introduce Lark, an architecture with a new training objective.", run="one-new")
    current, reads = [old_seed, old_anchor], []

    class Provider:
        configured = True

        async def generate(self, prompt, payload, schema):
            assert schema is RetrievalQueryPayload
            current[:] = [new_anchor]
            return schema(terms=[{"source_phrase": "训练目标", "term": "training objective prediction"}])

    service = IntelligenceService(None, Provider())

    def snapshot(document_ids):
        reads.append(tuple(document_ids))
        return list(current)

    service._blocks = snapshot
    # Pin the lexical hit so this test exercises the asynchronous version
    # boundary and optional context expansion, not BM25 scoring.
    service._rank_lexical = lambda question, blocks, index: [old_seed]
    default = asyncio.run(service.retrieve_for_question("训练目标", ["one"], limit=1, expansion="off"))
    assert [item.block_id for item in default["evidence"]] == ["old-detail"]
    assert default["attribution_context"]["enabled"] is False
    reads.clear()
    found = asyncio.run(service.retrieve_for_question("训练目标", ["one"], limit=1,
                                                     include_attribution_context=True, expansion="always"))
    assert reads == [("one",)]
    assert found["selected_runs"] == {"one": "one-frozen"}
    assert found["attribution_context"]["added_block_ids"] == ["old-intro"]
    assert [item.block_id for item in found["evidence"]] == ["old-detail", "old-intro"]
    assert all(item.run_id == "one-frozen" for item in found["evidence"])
