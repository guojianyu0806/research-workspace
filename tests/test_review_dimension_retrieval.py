"""Review supplementation finds missing tasks/corpora in the same parse snapshot."""
import pytest

from research_workspace.intelligence import Evidence, IntelligenceService
from research_workspace.library import Library
from research_workspace.review_retrieval import (supplement_review_evidence, directly_named_training,
    subject_hints, focus_review_evidence, request_aspects)


@pytest.mark.parametrize(("question", "expected"), [
    ("说明模型架构。不要讨论层数、训练目标或实验结果。", ["模型结构"]),
    ("无需讨论训练目标，外部知识来源，比较模型架构。", ["模型结构"]),
    ("比较模型架构但不要讨论训练目标；请描述知识来源。", ["模型结构", "外部知识的作用"]),
    ("比较模型架构，训练目标不用讨论。", ["模型结构"]),
    ("Compare architectures, but do not discuss training objectives or knowledge sources.", ["模型结构"]),
    ("Without discussing training objectives, describe external knowledge sources.", ["外部知识的作用"]),
    ("说明模型是否不需要特定训练任务。", ["训练目标"]),
])
def test_explicit_opt_outs_do_not_become_requested_dimensions(question, expected):
    assert request_aspects(question, []) == expected


def test_query_expansion_cannot_reintroduce_excluded_dimension():
    question = "请说明运行延迟。不要讨论训练目标。"
    expanded = [{"source_phrase": "运行延迟", "text": "inference latency"},
                {"source_phrase": "训练目标", "text": "training objective"}]
    assert request_aspects(question, expanded) == ["运行延迟"]


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


def test_direct_training_candidates_distinguish_task_relation_from_speed_statement(tmp_path):
    service = IntelligenceService(Library(tmp_path))
    assert directly_named_training("We train ORION by minimizing answer loss.", ["orion"], service)
    assert directly_named_training("The ORION model trained for translation uses attention.", ["orion"], service)
    assert not directly_named_training("ORION can be trained significantly faster.", ["orion"], service)
    assert not directly_named_training("ORION was trained for as little as ten hours.", ["orion"], service)


def test_subject_hints_prefer_the_named_contribution_over_background(tmp_path):
    service = IntelligenceService(Library(tmp_path))
    sources = [block("one", "Research", "We introduce ORION models. ORION models use attention."),
               block("two", "Research", "ORION models pre-train on text."),
               block("three", "Research", "NOVA models are discussed as prior work.")]
    assert subject_hints(service, sources) == {"Research": ["orion"]}


def test_focused_objective_catalog_prioritizes_actual_training_task_over_decoder_and_speed(tmp_path):
    service = IntelligenceService(Library(tmp_path))
    decoder = block("decoder", "doc", "ORION's decoder predicts next-token probabilities with softmax.")
    speed = block("speed", "doc", "ORION can be trained significantly faster.")
    task = block("task", "doc", "The ORION (large) model trained for French-to-German translation used dropout.")
    focused = focus_review_evidence(service, [decoder, speed, task], "训练目标", ["orion"], max_blocks=1)
    assert focused == [task]
    assert focused[0].text == task.text and focused[0].bbox == task.bbox


def test_focused_source_catalog_retains_named_scope_and_anonymous_corpus_without_architecture_noise(tmp_path):
    service = IntelligenceService(Library(tmp_path))
    architecture = block("arch", "doc", "ORION models use attention.")
    corpus = block("data", "doc", "We trained on the public training data corpus of translation sentence pairs.")
    scope = block("scope", "doc", "The ORION model trained for translation uses dropout.")
    focused = focus_review_evidence(service, [architecture, scope, corpus], "外部知识的作用", ["orion"])
    assert focused == [corpus, scope]


def test_focused_catalog_has_bounded_source_count_and_never_invents_an_objective(tmp_path):
    service = IntelligenceService(Library(tmp_path))
    paragraphs = [block(str(i), "doc", f"ORION predicts target symbol {i}.") for i in range(24)]
    focused = focus_review_evidence(service, paragraphs, "training objective", ["orion"])
    assert len(focused) == 12 and all(item in paragraphs for item in focused)
    unrelated = block("unrelated", "doc", "Copper sensors record temperature.")
    assert focus_review_evidence(service, [unrelated], "training objective", ["orion"]) == [unrelated]


def test_named_task_scope_and_actual_corpus_reserve_search_slots_before_generic_context(tmp_path):
    service = IntelligenceService(Library(tmp_path))
    architecture = block("arch", "doc", "ORION uses an attention encoder architecture.")
    scope = block("scope", "doc", "We pre-train ORION using two unsupervised tasks described in this section.")
    anonymous_corpus = block("corpus", "doc", "For the pre-training corpus we use BookArchive and PublicEncyclopaedia.")
    prior = block("prior", "doc", "We pre-train NOVA using two tasks described in this section.")
    snapshot = [architecture, prior, scope, anonymous_corpus]
    evidence, audit = supplement_review_evidence(service, snapshot, [], [], request="training objectives",
        missing_pairs=[("doc", "training objective")], max_evidence=1, subject_names={"doc":["orion"]})
    assert evidence == [scope] and audit["searches"][0]["role_anchor_block_ids"] == ["scope"]
    evidence, audit = supplement_review_evidence(service, snapshot, [], [], request="external knowledge",
        missing_pairs=[("doc", "external knowledge")], max_evidence=1, subject_names={"doc":["orion"]})
    assert evidence == [anonymous_corpus]
    assert audit["searches"][0]["role_anchor_block_ids"] == ["corpus"]
