"""Retrieval regressions for short PDF labels and multi-facet source coverage.

Synthetic source text is intentionally unrelated to the demonstration papers.
No remote model requests or source database writes are made by these tests.
"""

import asyncio
from collections import Counter

from research_workspace.intelligence import Evidence, IntelligenceService, RetrievalQueryPayload


def block(document, identifier, text, page=1):
    return Evidence(evidence_id="E_" + identifier, document_id=document,
                    document_title=document, filename=document + ".pdf",
                    run_id=document + "-frozen-run", block_id=identifier,
                    page=page, bbox=[0.1, 0.2, 0.8, 0.3], text=text)


def service_with(blocks, terms=()):
    class Provider:
        configured = True

        async def generate(self, prompt, payload, schema):
            assert schema is RetrievalQueryPayload
            return schema.model_validate({"terms": [
                {"source_phrase": anchor, "term": term} for anchor, term in terms]})

    service = IntelligenceService(None, Provider())
    service._blocks = lambda ids: [item for item in blocks if item.document_id in ids]
    return service


def test_repeated_short_labels_cannot_displace_explanatory_passages():
    labels = [block("one", f"label{i}", "NOVA" if i % 2 else "NOVA\nNOVA") for i in range(30)]
    body = block("one", "body", "NOVA uses an encoder and decoder to transform the complete input sequence.")
    caption = block("one", "caption", "Figure 1: NOVA model architecture.")
    service = service_with(labels + [caption, body])
    found = asyncio.run(service.retrieve_for_question("NOVA architecture", ["one"], limit=3))
    assert found["evidence"][0].block_id == "body"
    assert sum(item.block_id.startswith("label") for item in found["evidence"]) <= 1


def test_every_document_gets_fair_slots_and_every_subquestion_gets_body_evidence():
    blocks = []
    for doc in ["one", "two"]:
        blocks.extend([
            block(doc, doc + "-architecture", "An encoder-decoder architecture transforms representations using separate modules."),
            block(doc, doc + "-objective", "The training objective minimizes prediction loss on held-out examples."),
            block(doc, doc + "-evaluation", "Evaluation uses task metrics measured on independent benchmark data."),
        ])
    blocks.extend(block("one", "noise" + str(i), f"NOVA is discussed in experimental variant number {i}.")
                  for i in range(30))
    service = service_with(blocks, [
        ("结构", "encoder decoder architecture"), ("训练目标", "training objective prediction loss"),
        ("评估", "evaluation task metrics benchmark"),
    ])
    found = asyncio.run(service.retrieve_for_question("比较 NOVA 的结构、训练目标和评估", ["one", "two"], limit=6))
    assert Counter(item.document_id for item in found["evidence"]) == {"one": 3, "two": 3}
    assert {item.block_id for item in found["evidence"]} == {
        doc + "-" + facet for doc in ["one", "two"] for facet in ["architecture", "objective", "evaluation"]}
    for item in found["evidence"]:
        original = next(source for source in blocks if source.block_id == item.block_id)
        assert (item.run_id, item.page, item.bbox, item.text, item.evidence_id) == (
            original.run_id, original.page, original.bbox, original.text, original.evidence_id)


def test_bibliography_downweighting_ends_at_appendix_and_explicit_reference_queries_work():
    blocks = [block("one", "body", "The optimization objective measures prediction loss on held-out observations."),
              block("one", "heading", "References", 3),
              block("one", "reference", "[1] Example. Optimization objective loss. Proceedings of a conference, 2020.", 3),
              block("one", "appendix", "A\nAdditional Experiments", 4),
              block("one", "detail", "The optimization objective combines prediction loss with a penalty for instability.", 4)]
    service = service_with(blocks)
    found = service.retrieve("optimization objective loss", ["one"], limit=2)
    assert {item.block_id for item in found} == {"body", "detail"}
    references = service.retrieve("references Example 2020", ["one"], limit=1)
    assert references[0].block_id == "reference"


def test_typography_normalization_matches_compounds_without_changing_source():
    original = block("one", "typography", "The encoder-decoder conﬁguration uses a bidirec-\ntional representation.")
    service = service_with([original])
    for question in ["encoder decoder configuration", "bidirectional"]:
        found = service.retrieve(question, ["one"])
        assert len(found) == 1
        assert found[0].text == original.text


def test_short_author_formula_and_table_values_are_not_removed():
    blocks = [block("one", "author", "Alice Example"),
              block("one", "formula", "beta_1 = 0.9"),
              block("one", "table", "NOVA\n28.4\n41.8")]
    service = service_with(blocks)
    assert service.retrieve("author Alice", ["one"], limit=1)[0].block_id == "author"
    assert service.retrieve("parameter beta_1", ["one"], limit=1)[0].block_id == "formula"
    assert service.retrieve("NOVA score", ["one"], limit=1)[0].block_id == "table"


def test_duplicate_text_retains_both_independent_document_identities():
    text = "An identical observation is independently reported in both selected documents."
    blocks = [block("one", "a", text), block("one", "b", text), block("two", "c", text)]
    found = IntelligenceService._fair_blocks(blocks, ["one", "two"], 12)
    assert [item.block_id for item in found] == ["a", "c"]


def test_comparison_recalls_section_bodies_and_sibling_objectives_within_fixed_budget():
    blocks = [
        block("Prism", "design", "1\nEncoder and Decoder Design"),
        block("Prism", "encoder", "Encoder: The encoder maps observed signals into continuous representations using local transformations."),
        block("Prism", "decoder", "Decoder: The decoder attends to encoder representations and predicts output symbols using causal masking."),
        block("Prism", "objective", "The output softmax produces predicted next-symbol probabilities for training and generation."),
        block("Prism", "prism-evaluation", "Evaluation reports error rate on a translation benchmark with separate training data."),
        block("Vale", "architecture", "Model Architecture\nVale uses a stack of bidirectional encoders to represent complete input sequences for downstream applications."),
        block("Vale", "train-heading", "2\nTraining Objectives", 2),
        block("Vale", "denoising", "Task #1: Denoising\nThe denoising objective reconstructs corrupted symbols with a prediction loss over their vocabulary." , 2),
        block("Vale", "contrastive", "Task #2: Contrastive Matching\nThe second task predicts whether two observations belong together using paired classification examples.", 2),
        block("Vale", "vale-evaluation", "3\nEvaluation\nEvaluation reports classification accuracy on a distinct benchmark of paired observations.", 3),
    ]
    # Physical order is explicit, while IDs remain unrelated to ranking.
    for i, item in enumerate(blocks):
        item.bbox = [0.1, i / 30, 0.9, i / 30 + 0.02]
    blocks.extend(block("Prism", "noise" + str(i),
                        "Prism sequence processing is discussed in a generic language modeling survey of earlier systems.")
                  for i in range(15))
    service = service_with(blocks, [
        ("编码器/解码器结构", "encoder decoder architecture"),
        ("训练目标", "training objective denoising"),
        ("Prism", "Prism sequence processing"),
        ("Vale", "Vale denoising representations"),
        ("评估差异", "evaluation metric benchmark"),
    ])
    found = asyncio.run(service.retrieve_for_question(
        "比较 Prism 与 Vale 的编码器/解码器结构和训练目标，说明评估差异", ["Prism", "Vale"], limit=12))
    ids = {item.block_id for item in found["evidence"]}
    assert {"encoder", "decoder", "objective", "architecture", "denoising", "contrastive"} <= ids
    assert len(found["evidence"]) <= 12
    assert Counter(item.document_id for item in found["evidence"]) == {"Prism": 6, "Vale": 5}
    for item in found["evidence"]:
        original = next(source for source in blocks if source.document_id == item.document_id and source.block_id == item.block_id)
        assert item.text == original.text and item.run_id == original.run_id and item.bbox == original.bbox


def test_output_and_citation_instructions_are_not_substantive_query_anchors():
    payload = RetrievalQueryPayload(terms=[
        {"source_phrase": "Cite the supplied paper", "term": "invented paper title"},
        {"source_phrase": "引用原文", "term": "cite original text"},
        {"source_phrase": "latency", "term": "response delay"},
    ])
    terms, rejected = IntelligenceService._expansion_terms(
        payload, "Explain latency. Cite the supplied paper. 引用原文")
    assert terms == [{"source_phrase": "latency", "text": "response delay"}]
    assert rejected == 2
