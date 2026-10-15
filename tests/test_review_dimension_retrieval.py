"""Review supplementation finds missing tasks/corpora in the same parse snapshot."""
from research_workspace.intelligence import Evidence, IntelligenceService
from research_workspace.library import Library
from research_workspace.review_retrieval import supplement_review_evidence


def block(identifier, document, text, *, page=1, y=0.1, run="old"):
    return Evidence(evidence_id=f"E_{identifier}", document_id=document, document_title=document,
                    filename=f"{document}.pdf", run_id=run, block_id=identifier, page=page,
                    bbox=[0.1, y, 0.9, min(y+0.05, 1)], text=text)


def test_facet_search_adds_training_and_corpus_beyond_architecture(tmp_path):
    service = IntelligenceService(Library(tmp_path))
    architecture = block("architecture", "doc", "ORION models use a bidirectional encoder architecture.")
    sources = [architecture,
               block("heading", "doc", "3 Pre-training ORION", y=0.2),
               block("intro", "doc", "We pre-train ORION with two unsupervised tasks described below.", y=0.3),
               block("objective", "doc", "The training objective predicts masked tokens and the next sentence.", y=0.4),
               block("corpus", "doc", "The pretraining corpus contains BookArchive and Encyclopaedia text passages.", y=0.5)]
    evidence, audit = supplement_review_evidence(service, sources, [architecture], [],
        request="比较模型结构、训练目标及外部知识的作用")
    assert {item.block_id for item in evidence} == {item.block_id for item in sources}
    assert all(item.run_id == "old" for item in evidence)
    assert {search["aspect"] for search in audit["searches"]} == {"模型结构", "训练目标", "外部知识的作用"}
    assert audit["selected_runs"] == {"doc": "old"}
    for selected in evidence:
        original = next(item for item in sources if item.block_id == selected.block_id)
        assert selected.text == original.text and selected.bbox == original.bbox


def test_missing_pair_search_does_not_reopen_library_or_add_other_target(tmp_path, monkeypatch):
    service = IntelligenceService(Library(tmp_path))
    sources = [block("one", "doc-one", "ORION training objective predicts the next target symbol."),
               block("two", "doc-two", "NOVA training objective predicts the next target symbol.")]
    monkeypatch.setattr(service, "_blocks", lambda _: (_ for _ in ()).throw(AssertionError("reopened snapshot")))
    evidence, audit = supplement_review_evidence(service, sources, [], [], request="training objectives",
                                                 missing_pairs=[("doc-two", "training objective")])
    assert [item.block_id for item in evidence] == ["two"]
    assert len(audit["searches"]) == 1
    assert audit["searches"][0]["document_id"] == "doc-two"


def test_source_budget_is_bounded_and_fair_between_missing_dimensions(tmp_path):
    service = IntelligenceService(Library(tmp_path))
    current = [block(f"existing-{i}", "unrelated", f"Existing paragraph number {i}.") for i in range(94)]
    sources = [block("one", "doc-one", "ORION training objective predicts the next target symbol."),
               block("two", "doc-two", "NOVA external knowledge retrieval accesses a document index.")]
    evidence, audit = supplement_review_evidence(service, sources, current, [], request="",
        missing_pairs=[("doc-one", "training objective"), ("doc-two", "external knowledge")])
    assert len(evidence) == audit["max_evidence"] == 96
    assert {item.block_id for item in evidence[-2:]} == {"one", "two"}


def test_search_vocabulary_does_not_make_nonmatching_document_evidence(tmp_path):
    service = IntelligenceService(Library(tmp_path))
    source = block("unrelated", "doc", "Copper sensors record temperatures inside a controlled chamber.")
    evidence, audit = supplement_review_evidence(service, [source], [], [], request="训练目标")
    assert evidence == [] and audit["added_block_ids"] == []


def test_short_named_training_search_preserves_explicit_corpus_attribution(tmp_path):
    service = IntelligenceService(Library(tmp_path))
    named_corpus = block("direct-corpus", "doc", "ORION is trained on BookArchive and PublicEncyclopaedia.")
    anonymous = block("anonymous", "doc", "We use a pretraining training data corpus of unlabeled passages.")
    architecture = block("architecture", "doc", "ORION models use an attention architecture with two encoders.")
    evidence, audit = supplement_review_evidence(service, [architecture, anonymous, named_corpus], [], [],
        request="", missing_pairs=[("doc", "外部知识的作用")], subject_names={"doc": ["orion"]})
    assert evidence[0].block_id == "direct-corpus"
    assert audit["searches"][0]["queries"][0] == "orion trained"
