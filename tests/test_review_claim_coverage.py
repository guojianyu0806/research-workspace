"""Resolve review coverage by independently anchored claims, never proposed indices."""
import asyncio

import pytest
from pydantic import ValidationError

from research_workspace.intelligence import (
    Claim, ClaimSupportPayload, Evidence, IntelligenceService, ModelOutputError,
    ReviewClaimCoveragePayload, ReviewCoveragePayload, ReviewMessageRepairPayload, SynthesisPayload,
)
from research_workspace.library import Library


def source(identifier, document, text, *, run_id=None):
    return Evidence(evidence_id=f"E_{identifier}", document_id=document, document_title=document.upper(),
                    filename=f"{document}.pdf", run_id=run_id or f"run-{document}", block_id=identifier,
                    page=1, bbox=[0.1, 0.1, 0.9, 0.3], text=text)


SOURCES = [
    source("orion", "doc-orion", "ORION models use attention."),
    source("generic-intro", "doc-nova", "Encoder-decoder designs transform sequences."),
    source("nova", "doc-nova", "NOVA models use a bidirectional encoder."),
    source("generic-structure", "doc-nova", "Most competitive models use an encoder and decoder."),
    source("generic-output", "doc-nova", "A decoder generates each output token autoregressively."),
]


def claims():
    texts = ["ORION 使用注意力。", "编码器和解码器结构可以转换序列。", "NOVA 使用双向编码器。",
             "大多数竞争性模型使用编码器和解码器。", "解码器自回归地产生输出词元。"]
    return [Claim(text=text, citations=[{"evidence_id": item.evidence_id, "quote_id": "Q1"}])
            for text, item in zip(texts, SOURCES)]


def synthesis(values=None, message=""):
    values = values if values is not None else claims()
    return SynthesisPayload(status="generated", sections=[{"heading": "Comparison", "claims": values[index:index + 15]}
        for index in range(0, len(values), 15)], message=message)


def repair(message=""):
    return ReviewMessageRepairPayload(status="generated", message=message)


class Provider:
    configured = True

    def __init__(self, results, *, aspects=("architecture",), gaps=None, mutate_link=None, change_aspects=False):
        self.results = iter(results)
        self.aspects = list(aspects)
        self.gaps = gaps or {}
        self.mutate_link = mutate_link
        self.change_aspects = change_aspects
        self.calls = []
        self.coverage_round = 0

    async def generate(self, prompt, payload, output_model):
        self.calls.append((payload, output_model))
        if output_model in {SynthesisPayload, ReviewMessageRepairPayload}:
            response = next(self.results)
            assert isinstance(response, output_model)
            return response
        if output_model is ClaimSupportPayload:
            return ClaimSupportPayload(verdict="supported", reason="The fixture claim matches its own source.")
        if output_model is ReviewCoveragePayload:
            self.coverage_round += 1
            aspects = ["unrequested replacement"] if self.change_aspects and self.coverage_round > 1 else self.aspects
            assessments = []
            for document in payload["selected_document_ids"]:
                for aspect in aspects:
                    gap = next((candidate for candidate in (
                        self.gaps.get((document, aspect), ""), f"《{document.upper()}》：{aspect}")
                        if candidate and candidate in payload["response_message"]), "")
                    present = bool(gap)
                    # Deliberately wrong proposals: the named NOVA assertion is
                    # index 2, while 3/4 are generic material from that same PDF.
                    indices = ([0] if document == "doc-orion" else [3, 4]) if aspect == "architecture" else []
                    assessments.append({"document_id": document, "aspect": aspect, "answer_claim_indices": indices,
                                        "gap_quote": gap if present else "", "gap_is_appropriate": present,
                                        "reason": "Fixture proposed coverage; indices are intentionally unreliable."})
            return ReviewCoveragePayload(requested_aspects=aspects, assessments=assessments, unverified_statements=[])
        assert output_model is ReviewClaimCoveragePayload
        current = payload["claim"]
        links = []
        for name, document in (("ORION", "doc-orion"), ("NOVA", "doc-nova")):
            if name not in current["text"]:
                continue
            for aspect in payload["requested_aspects"]:
                if aspect != "architecture":
                    continue
                citation = next(item for item in current["citations"] if item["document_id"] == document)
                links.append({"document_id": document, "aspect": aspect,
                              "subject_citation_id": citation["citation_id"], "answer_citation_id": citation["citation_id"],
                              "reason": "The named model and its structure are explicit in this claim and its own quote."})
        if self.mutate_link and links:
            links = self.mutate_link(links)
        return ReviewClaimCoveragePayload(answers=links)


def run(tmp_path, provider, *, evidence=SOURCES, documents=("doc-orion", "doc-nova"), check_cancel=None):
    service = IntelligenceService(Library(tmp_path), provider)
    return asyncio.run(service._generate_grounded("fixture", {"task": "review", "instruction": "Compare model architectures and requested objectives."},
                     SynthesisPayload, evidence, required_documents=list(documents), check_cancel=check_cancel))


def classifier_calls(provider):
    return [payload for payload, model in provider.calls if model is ReviewClaimCoveragePayload]


def final_assessments(audit):
    return {(item["document_id"], item["aspect"]): item
            for item in audit["review_coverage"][-1]["assessments"]}


def test_wrong_generic_proposals_resolve_to_the_correct_named_claim(tmp_path):
    provider = Provider([synthesis()])
    _, _, audit = run(tmp_path, provider)
    assert audit["coverage_prompt_version"] == "review-coverage-v1.1"
    assert audit["coverage_link_prompt_version"] == "review-claim-coverage-v1.1"
    resolved = final_assessments(audit)
    assert resolved[("doc-nova", "architecture")]["answer_claim_indices"] == [2]
    assert resolved[("doc-orion", "architecture")]["answer_claim_indices"] == [0]
    proposals = audit["review_coverage"][-1]["proposed_assessments"]
    assert next(item for item in proposals if item["document_id"] == "doc-nova")["answer_claim_indices"] == [3, 4]
    calls = classifier_calls(provider)
    assert len(calls) == 5
    for payload in calls:
        assert set(payload) == {"task", "request", "requested_aspects", "targets", "claim"}
        assert payload["task"] == "verify_review_claim_coverage"
        assert set(payload["claim"]) == {"claim_id", "text", "citations"}
        assert all(set(target) == {"document_id", "title"} for target in payload["targets"])
        assert all(set(cite) == {"citation_id", "evidence_id", "quote_id", "quote", "document_id", "run_id", "block_id", "page", "bbox"}
                   for cite in payload["claim"]["citations"])
        assert [cite["citation_id"] for cite in payload["claim"]["citations"]] == ["R1"]
    catalog = audit["review_coverage"][-1]["claim_catalog"]
    assert [item["claim_index"] for item in catalog] == list(range(5))
    assert len({item["claim_id"] for item in catalog}) == 5
    assert resolved[("doc-nova", "architecture")]["answer_claim_ids"] == [catalog[2]["claim_id"]]


def test_deterministic_message_repair_reuses_stable_claim_coverage(tmp_path):
    provider = Provider([synthesis()], aspects=("architecture", "objective"))
    result, _, audit = run(tmp_path, provider)
    assert [item.text for item in result.sections[0].claims] == [item.text for item in claims()]
    assert len(classifier_calls(provider)) == 5
    first = {item["text"]: item["claim_id"] for item in audit["review_coverage"][0]["claim_catalog"]}
    final = {item["text"]: item["claim_id"] for item in audit["review_coverage"][-1]["claim_catalog"]}
    assert first == final
    resolved = final_assessments(audit)
    assert resolved[("doc-nova", "architecture")]["answer_claim_indices"] == [2]
    assert resolved[("doc-orion", "architecture")]["answer_claim_indices"] == [0]
    assert resolved[("doc-nova", "objective")]["gap_quote"] == "《DOC-NOVA》：objective"
    assert not resolved[("doc-nova", "objective")]["answer_claim_indices"]
    assert audit["coverage_model_calls"] == 2
    assert not any(model is ReviewMessageRepairPayload for _, model in provider.calls)


def test_reordered_claims_reuse_cached_links_and_recompute_current_indices(tmp_path):
    values = claims()
    for item, cited_source in zip(values, SOURCES):
        item.citations[0].quote = cited_source.text
    proposal = ReviewCoveragePayload(requested_aspects=["architecture"], assessments=[{
        "document_id": document, "aspect": "architecture", "answer_claim_indices": [3, 4],
        "gap_is_appropriate": False, "reason": "Intentionally unreliable original positions.",
    } for document in ("doc-orion", "doc-nova")], unverified_statements=[])
    provider = Provider([])
    service = IntelligenceService(Library(tmp_path), provider)
    cache, audit = {}, {"coverage_link_model_calls": 0, "coverage_link_cache_hits": 0}

    async def exercise():
        first = await service._resolve_review_coverage(proposal, values, SOURCES, ["doc-orion", "doc-nova"],
            "Architecture?", list(range(5)), cache, audit)
        order = [2, 0, 1, 3, 4]
        final = await service._resolve_review_coverage(proposal, [values[index] for index in order], SOURCES,
            ["doc-orion", "doc-nova"], "Architecture?", order, cache, audit)
        return first, final

    (_, first_record, first_problems), (resolved, final_record, final_problems) = asyncio.run(exercise())
    assert not first_problems and not final_problems
    assert len(classifier_calls(provider)) == 5
    assert audit == {"coverage_link_model_calls": 5, "coverage_link_cache_hits": 5}
    assert {item["text"]: item["claim_id"] for item in first_record["claim_catalog"]} == {
        item["text"]: item["claim_id"] for item in final_record["claim_catalog"]}
    assert {item.document_id: item.answer_claim_indices for item in resolved.assessments} == {
        "doc-orion": [1], "doc-nova": [0]}
    assert [item["original_claim_index"] for item in final_record["claim_catalog"]] == [2, 0, 1, 3, 4]


@pytest.mark.parametrize("field,value", [
    ("subject_citation_id", "R99"), ("answer_citation_id", "R99"),
    ("document_id", "doc-nova"), ("document_id", "unknown-document"), ("aspect", "unknown-dimension"),
])
def test_tampered_or_cross_document_coverage_links_are_rejected(tmp_path, field, value):
    def mutate(links):
        return [{**link, field: value} for link in links]
    provider = Provider([synthesis(), repair()], mutate_link=mutate)
    with pytest.raises(ModelOutputError):
        run(tmp_path, provider)


@pytest.mark.parametrize("field", ["subject_citation_id", "answer_citation_id"])
@pytest.mark.parametrize("invalid_id", ["", "R0", "Q1", "R01"])
def test_coverage_link_schema_rejects_malformed_citation_ids(field, invalid_id):
    link = {"document_id": "doc-orion", "aspect": "architecture", "subject_citation_id": "R1",
            "answer_citation_id": "R1", "reason": "Use an existing citation, without copying its words."}
    with pytest.raises(ValidationError):
        ReviewClaimCoveragePayload(answers=[{**link, field: invalid_id}])


@pytest.mark.parametrize("subject_id,answer_id,valid", [
    ("R1", "R3", True), ("R3", "R1", True),
    ("R2", "R3", False), ("R1", "R2", False), ("R1", "R99", False),
])
def test_both_selected_citations_must_belong_to_the_target_document(subject_id, answer_id, valid):
    items = [SOURCES[0], SOURCES[2], source("orion-detail", "doc-orion", "ORION uses stacked attention layers.")]
    value = Claim(text="ORION 使用注意力层，NOVA 使用双向编码器。", citations=[{
        "evidence_id": item.evidence_id, "quote": item.text} for item in items])
    identity = IntelligenceService._review_claim_identity(value, items)
    assert [(cite["citation_id"], cite["document_id"]) for cite in identity["citations"]] == [
        ("R1", "doc-orion"), ("R2", "doc-nova"), ("R3", "doc-orion")]
    links = ReviewClaimCoveragePayload(answers=[{
        "document_id": "doc-orion", "aspect": "architecture", "subject_citation_id": subject_id,
        "answer_citation_id": answer_id, "reason": "Subject and answer are selected from this claim's own references.",
    }])
    problems = IntelligenceService._review_link_problems(links, identity, ["doc-orion", "doc-nova"], ["architecture"])
    assert bool(problems) is not valid


def test_coverage_link_schema_does_not_accept_copied_quote_anchors():
    with pytest.raises(ValidationError):
        ReviewClaimCoveragePayload(answers=[{
            "document_id": "doc-orion", "aspect": "architecture", "subject_citation_id": "R1",
            "answer_citation_id": "R1", "subject_quote": "ORION", "answer_quote": "An altered quotation.",
            "reason": "Copied text is outside the citation-selection protocol.",
        }])


def test_duplicate_coverage_links_are_rejected(tmp_path):
    provider = Provider([synthesis(), repair()], mutate_link=lambda links: links + links)
    with pytest.raises(ModelOutputError):
        run(tmp_path, provider)


def test_generic_claim_alone_requires_an_explicit_gap_for_the_named_model_dimension(tmp_path):
    generic = claims()[3]
    provider = Provider([synthesis([generic])])
    result, _, audit = run(tmp_path, provider, evidence=[SOURCES[3]], documents=("doc-nova",))
    assert "《DOC-NOVA》：architecture" in result.message
    assert final_assessments(audit)[("doc-nova", "architecture")]["answer_claim_indices"] == []
    assert audit["coverage_model_calls"] == 2
    assert len(classifier_calls(provider)) == 1


def test_valid_gap_is_preserved_when_generic_claims_do_not_cover_the_named_model(tmp_path):
    generic = claims()[3]
    gap = "所给摘录未独立支持 NOVA 的模型结构。"
    provider = Provider([synthesis([generic], gap)], gaps={("doc-nova", "architecture"): gap})
    _, _, audit = run(tmp_path, provider, evidence=[SOURCES[3]], documents=("doc-nova",))
    resolved = final_assessments(audit)[("doc-nova", "architecture")]
    assert resolved["answer_claim_indices"] == [] and resolved["gap_quote"] == gap


def test_cancellation_stops_before_another_claim_coverage_call(tmp_path):
    provider = Provider([synthesis()])

    class Cancelled(Exception):
        pass

    def cancel():
        if classifier_calls(provider):
            raise Cancelled()

    with pytest.raises(Cancelled):
        run(tmp_path, provider, check_cancel=cancel)
    assert len(classifier_calls(provider)) == 1


def test_unique_claim_coverage_requests_never_exceed_the_24_call_budget(tmp_path):
    items = [source(f"item-{index}", "doc-orion", f"ORION models use attention in component {index}.") for index in range(25)]
    direct = [Claim(text=item.text, citations=[{"evidence_id": item.evidence_id, "quote": item.text}]) for item in items]
    provider = Provider([synthesis(direct)])
    with pytest.raises(ModelOutputError, match="24"):
        run(tmp_path, provider, evidence=items, documents=("doc-orion",))
    assert len(classifier_calls(provider)) <= 24


def test_second_round_cannot_replace_the_original_requested_dimensions(tmp_path):
    provider = Provider([synthesis()], aspects=("architecture", "objective"), change_aspects=True)
    with pytest.raises(ModelOutputError, match="dimensions changed"):
        run(tmp_path, provider)
    assert len(classifier_calls(provider)) == 5


@pytest.mark.parametrize("field,value", [
    ("document_id", "another-doc"), ("run_id", "another-run"), ("block_id", "another-block"),
    ("page", 2), ("bbox", [0.2, 0.2, 0.8, 0.4]),
])
def test_stable_claim_identity_includes_resolved_source_identity(field, value):
    item = SOURCES[0]
    value_claim = Claim(text="ORION 使用注意力。", citations=[{"evidence_id": item.evidence_id, "quote": item.text}])
    baseline = IntelligenceService._review_claim_identity(value_claim, [item])
    assert baseline == IntelligenceService._review_claim_identity(value_claim.model_copy(deep=True), [item.model_copy(deep=True)])
    changed = IntelligenceService._review_claim_identity(value_claim, [item.model_copy(update={field: value})])
    assert baseline["claim_id"] != changed["claim_id"]


def test_stable_claim_identity_includes_claim_text_and_resolved_quotes():
    item = SOURCES[0]
    value = Claim(text="ORION 使用注意力。", citations=[{"evidence_id": item.evidence_id, "quote": item.text}])
    baseline = IntelligenceService._review_claim_identity(value, [item])["claim_id"]
    changed_text = value.model_copy(deep=True)
    changed_text.text = "ORION 模型使用注意力。"
    changed_quote = value.model_copy(deep=True)
    changed_quote.citations[0].quote = "ORION models use attention"
    assert IntelligenceService._review_claim_identity(changed_text, [item])["claim_id"] != baseline
    assert IntelligenceService._review_claim_identity(changed_quote, [item])["claim_id"] != baseline


def test_cross_round_link_budget_stops_before_the_25th_unique_request(tmp_path):
    items = [source(f"item-{index}", "doc-orion", f"ORION models use attention in component {index}.") for index in range(25)]
    direct = [Claim(text=item.text, citations=[{"evidence_id": item.evidence_id, "quote": item.text}]) for item in items]
    proposal = ReviewCoveragePayload(requested_aspects=["architecture"], assessments=[{
        "document_id": "doc-orion", "aspect": "architecture", "answer_claim_indices": [],
        "gap_is_appropriate": False, "reason": "Fixture proposal.",
    }], unverified_statements=[])
    provider = Provider([])
    service = IntelligenceService(Library(tmp_path), provider)
    cache, audit = {}, {"coverage_link_model_calls": 0, "coverage_link_cache_hits": 0}

    async def exercise():
        await service._resolve_review_coverage(proposal, direct[:13], items, ["doc-orion"], "Architecture?",
                                              list(range(13)), cache, audit)
        await service._resolve_review_coverage(proposal, direct[13:], items, ["doc-orion"], "Architecture?",
                                              list(range(13, 25)), cache, audit)

    with pytest.raises(ModelOutputError, match="24"):
        asyncio.run(exercise())
    assert len(classifier_calls(provider)) == 24 and audit["coverage_link_model_calls"] == 24


def test_invalid_gap_flag_cannot_hide_a_gap_statement_conflicting_with_a_verified_answer(tmp_path):
    value = claims()[0]
    value.citations[0].quote = SOURCES[0].text
    proposal = ReviewCoveragePayload(requested_aspects=["architecture"], assessments=[{
        "document_id": "doc-orion", "aspect": "architecture", "answer_claim_indices": [0],
        "gap_quote": "所给摘录未独立支持 ORION 的模型结构。", "gap_is_appropriate": False,
        "reason": "The proposed gap is not appropriate because a named structural claim exists.",
    }], unverified_statements=[])
    provider = Provider([])
    service = IntelligenceService(Library(tmp_path), provider)
    _, _, problems = asyncio.run(service._resolve_review_coverage(proposal, [value], [SOURCES[0]],
        ["doc-orion"], "Architecture?", [0], {}, {"coverage_link_model_calls": 0, "coverage_link_cache_hits": 0}))
    assert any("conflicts" in problem for problem in problems)
