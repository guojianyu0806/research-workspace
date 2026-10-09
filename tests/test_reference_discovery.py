"""Offline, synthetic fixtures: tests do not establish academic importance."""
from copy import deepcopy

import httpx
import pymupdf
import pytest

from research_workspace.discovery import ARXIV_API, DiscoveryService
from research_workspace.library import Library
from research_workspace.reference_discovery import ReferenceDiscovery, ReferenceDiscoveryError
from research_workspace.structure import extract_structure


def block(text, identifier, page):
    return {"document_id": "document", "run_id": "run-old", "page": page,
            "block_id": identifier, "bbox": [.1, .1, .9, .4], "text": text}


class FixtureLibrary:
    def __init__(self, refs=None):
        self.blocks = [block("1 Methods\nWe evaluate retrieval [1] and repeat [1] in an experiment.", "method", 1),
                       block("Background astronomy mentions [2].", "background", 1),
                       block("PRIVATE_PARENT_TEXT_DO_NOT_TRANSMIT with no citation.", "private", 2),
                       block("References\n" + (refs or '[1] Alice. Evidence Retrieval Methods. 2024. arXiv:2401.12345v1.\n[2] Bob. Stellar Astronomy Catalogs. 2022.\n[3] Carol. Uncited Retrieval Analysis. 2023.'), "bibliography", 3)]
        self.structure = extract_structure(self.blocks, "document", "run-old")
        self.structure["processing_status"] = "completed"
        self.page_calls = []
        self.missing_context = False

    def get_structure(self, document_id, run_id=None):
        assert document_id == "document"
        if run_id and run_id != "run-old":
            raise KeyError(run_id)
        return deepcopy(self.structure)

    def get_page(self, document_id, page, run_id=None):
        self.page_calls.append((document_id, page, run_id))
        return {"blocks": [] if self.missing_context else [{"id": b["block_id"], **b} for b in self.blocks if b["page"] == page]}


def paper(identifier="2401.12345v1", **changes):
    return {"id": f"arxiv:{identifier}", "arxiv_id": identifier, "canonical_id": identifier.split("v")[0],
            "title": "Evidence Retrieval Methods", "doi": None, "source": "arxiv",
            "source_url": f"https://arxiv.org/abs/{identifier}", "retrieved_at": "2026-10-08T10:00:00+00:00",
            "fulltext_status": "metadata_only", "fulltext_searchable": False, **changes}


def found(items=None, **changes):
    return {"source": "arxiv", "source_url": ARXIV_API, "retrieved_at": "2026-10-08T10:00:00+00:00",
            "cached": True, "items": [paper()] if items is None else items, "total_results": 1, "error": None, **changes}


class FixtureDiscovery:
    def __init__(self, responses=None):
        self.responses = list(responses or [found()])
        self.calls = []

    def search_references(self, references, *, limit_per_reference):
        self.calls.append((references, limit_per_reference))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return {"results": [{"reference": references[0], **deepcopy(response)}], "metadata_only": True}


def test_ranking_has_reversible_filter_and_original_version_bound_evidence():
    library = FixtureLibrary()
    before = deepcopy(library.structure)
    service = ReferenceDiscovery(library)
    result = service.rank_references("document", "retrieval", run_id="run-old")
    assert service.discovery is None  # Ranking has no discovery initialization/network side effect.
    assert [r["reference_id"] for r in result["included"]] == ["reference-1", "reference-3"]
    assert [r["reference_id"] for r in result["filtered"]] == ["reference-2"]
    first = result["included"][0]
    assert first["score"] == 3 + 2 + 2 + 4
    assert first["signals"]["citation_occurrences"]["count"] == 2
    assert first["contexts"][0]["method_cues"] == ["experiment", "methods"]
    for context in first["contexts"]:
        evidence = context["context_evidence"]
        original = next(b["text"] for b in library.blocks if b["block_id"] == evidence["block_id"])
        assert original[evidence["char_start"]:evidence["char_end"]] == evidence["text"]
        assert evidence["run_id"] == "run-old"
        assert context["citation_evidence"]["text"] == "[1]"
    assert all(call[2] == "run-old" for call in library.page_calls)
    assert len(library.page_calls) == 1  # Multiple references reuse the same original page.
    restored = service.rank_references("document", "retrieval", include_filtered=True)
    assert len(restored["items"]) == 3 and len(restored["filtered"]) == 1
    assert restored["filtering"] == {"persisted": False, "reversible": True, "academic_value_assessed": False}
    assert library.structure == before


def test_frequency_and_method_signals_cannot_override_query_relevance():
    library = FixtureLibrary()
    result = ReferenceDiscovery(library).rank_references("document", "stellar")
    assert [r["reference_id"] for r in result["included"]] == ["reference-2"]
    assert next(r for r in result["filtered"] if r["reference_id"] == "reference-1")["score"] > 0
    assert len(ReferenceDiscovery(library).rank_references("document", "")["included"]) == 3


def test_only_nearby_verified_context_contributes_and_missing_block_is_honest():
    library = FixtureLibrary()
    library.missing_context = True
    result = ReferenceDiscovery(library).rank_references("document", "experiment")
    assert result["included"] == []
    context = result["filtered"][0]["contexts"][0]
    assert context["status"] == "unavailable" and context["context_evidence"] is None
    assert result["filtered"][0]["signals"]["method_experiment_context"]["points"] == 0


def test_context_radius_does_not_match_distant_text_or_invent_citations():
    library = FixtureLibrary()
    library.blocks[0]["text"] = "distant_target " + "z " * 400 + "Known source [1]."
    library.structure = extract_structure(library.blocks, "document", "run-old")
    result = ReferenceDiscovery(library).rank_references("document", "distant_target")
    assert result["included"] == []
    assert result["total_references"] == 3
    library.structure["references"] = []
    assert ReferenceDiscovery(library).rank_references("document", "retrieval")["items"] == []


@pytest.mark.parametrize("query,include_filtered", [(None, False), ("x" * 501, False), ("***", False), ("retrieval", "false")])
def test_invalid_ranking_query_is_rejected(query, include_filtered):
    with pytest.raises(ReferenceDiscoveryError):
        ReferenceDiscovery(FixtureLibrary()).rank_references("document", query, include_filtered=include_filtered)


def test_selected_identifier_lookup_preserves_cache_time_and_never_sends_parent_text():
    library, discovery = FixtureLibrary(), FixtureDiscovery()
    result = ReferenceDiscovery(library, discovery).search_selected("document", ["reference-1"], run_id="run-old")
    assert discovery.calls == [(["2401.12345v1"], 3)]
    row = result["results"][0]
    assert row["status"] == "completed" and row["cached"] is True
    assert row["retrieved_at"] == "2026-10-08T10:00:00+00:00" and row["attempted_at"] != row["retrieved_at"]
    assert row["candidates"][0]["match_status"] == "identifier_match"
    assert row["candidates"][0]["arxiv_version_match"] == "same"
    assert result["automatic_download"] is False and result["requires_user_confirmation"] is True
    assert library.page_calls == []
    assert "PRIVATE_PARENT" not in str(discovery.calls)


def test_budget_and_selection_validation_happen_before_unwanted_requests():
    discovery = FixtureDiscovery()
    service = ReferenceDiscovery(FixtureLibrary(), discovery)
    with pytest.raises(ReferenceDiscoveryError):
        service.search_selected("document", ["reference-1", "reference-99"])
    assert discovery.calls == []
    result = service.search_selected("document", ["reference-1", "reference-1", "reference-2"], max_searches=1)
    assert result["requests_used"] == 1 and result["duplicate_selections_ignored"] == 1
    assert result["results"][1]["status"] == "skipped_budget"


@pytest.mark.parametrize("budget", [0, 11, True, 1.5])
def test_invalid_budget_is_rejected(budget):
    with pytest.raises(ReferenceDiscoveryError):
        ReferenceDiscovery(FixtureLibrary()).search_selected("document", ["reference-1"], max_searches=budget)


def test_title_similarity_is_only_a_candidate_and_doi_is_a_strong_identifier():
    refs = '[1] John Smith and Jane Doe. Evidence Retrieval Methods. 2024.\n[2] B. "Reliable Numeric Evaluation". doi:10.1234/TEST.'
    discovery = FixtureDiscovery([found(), found([paper("2402.00001v1", doi="10.1234/test", title="Reliable Numeric Evaluation")])])
    result = ReferenceDiscovery(FixtureLibrary(refs), discovery).search_selected("document", ["reference-1", "reference-2"])
    assert discovery.calls == [(["Evidence Retrieval Methods"], 3), (["10.1234/test"], 3)]
    first, second = [row["candidates"][0] for row in result["results"]]
    assert first["match_status"] == "title_candidate"
    assert first["requires_user_confirmation"] is True
    assert second["match_status"] == "identifier_match" and second["matched_identifiers"] == ["doi"]


def test_different_arxiv_version_and_conflicting_identifiers_are_not_silently_merged():
    library = FixtureLibrary('[1] Alice. Evidence Retrieval Methods. arXiv:2401.12345v1. doi:10.1234/one.')
    discovery = FixtureDiscovery([found([paper("2401.12345v7"), paper("2401.12345v1", doi="10.1234/different")])])
    row = ReferenceDiscovery(library, discovery).search_selected("document", ["reference-1"])["results"][0]
    assert row["candidates"][0]["arxiv_version_match"] == "different"
    assert row["candidates"][0]["match_status"] == "identifier_match"
    assert row["candidates"][1]["match_status"] == "identifier_conflict"


def test_same_query_is_reused_within_budget_and_filtered_references_remain_searchable():
    library = FixtureLibrary('[1] Alice. Evidence Retrieval Methods. arXiv:2401.12345v1.\n[2] Bob. Evidence Retrieval Methods. arXiv:2401.12345v1.')
    discovery = FixtureDiscovery()
    service = ReferenceDiscovery(library, discovery)
    assert service.rank_references("document", "spectroscopy")["included"] == []
    result = service.search_selected("document", ["reference-1", "reference-2"], max_searches=1)
    assert result["status"] == "completed" and result["requests_used"] == 1
    assert result["results"][1]["reused_query"] is True
    assert len(discovery.calls) == 1


def test_failure_is_bounded_redacted_and_later_selected_reference_can_succeed():
    discovery = FixtureDiscovery([RuntimeError("PRIVATE-TOKEN transport payload"), found([])])
    result = ReferenceDiscovery(FixtureLibrary(), discovery).search_selected("document", ["reference-1", "reference-2"])
    assert result["status"] == "partial" and result["requests_used"] == 2
    assert result["results"][0]["status"] == "failed" and result["results"][0]["retrieved_at"] is None
    assert "PRIVATE-TOKEN" not in str(result)
    assert result["results"][1]["status"] == "not_found"


def test_unparseable_reference_does_not_send_full_bibliography_or_consume_budget():
    discovery = FixtureDiscovery()
    result = ReferenceDiscovery(FixtureLibrary('[1] X. 2020.'), discovery).search_selected("document", ["reference-1"])
    assert result["results"][0]["status"] == "skipped_no_query" and result["requests_used"] == 0
    assert discovery.calls == []


def test_missing_provenance_or_unsafe_metadata_does_not_become_confirmed_identity():
    invalid = FixtureDiscovery([found(retrieved_at=None)])
    assert ReferenceDiscovery(FixtureLibrary(), invalid).search_selected("document", ["reference-1"])["results"][0]["status"] == "failed"
    unsafe = FixtureDiscovery([found([paper(source_url="https://arxiv.org.evil.example/abs/2401.12345v1"), paper(arxiv_id={})])])
    row = ReferenceDiscovery(FixtureLibrary(), unsafe).search_selected("document", ["reference-1"])["results"][0]
    assert row["candidates"] == [] and row["ignored_invalid_metadata"] == 2


def test_real_library_and_discovery_http_contract_work_offline_with_old_run_and_cache(tmp_path):
    library = Library(tmp_path / "workspace")
    with pymupdf.open() as pdf:
        p = pdf.new_page()
        p.insert_text((35, 60), "1 Methods\nWe evaluate retrieval [1].")
        p = pdf.new_page()
        p.insert_text((35, 60), "References\n[1] Alice. Evidence Retrieval Methods. arXiv:2401.12345v1.")
        document = library.import_document(pdf.tobytes(), "SYNTHETIC-REFERENCE-TEST.pdf")["document"]
    while library.process_next_job():
        pass
    old_run = library.get_document(document["id"])["available_run"]["id"]
    calls = []
    def respond(request):
        calls.append(request)
        assert request.url.host == "export.arxiv.org" and request.url.params["id_list"] == "2401.12345v1"
        return httpx.Response(200, content=b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/abs/2401.12345v1</id><title>Evidence Retrieval Methods</title><summary>Synthetic test record.</summary><published>2024-01-01T00:00:00Z</published></entry></feed>')
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        discovery = DiscoveryService(library, client, request_interval=0)
        service = ReferenceDiscovery(library, discovery)
        library.create_run(document["id"])  # Old source evidence remains explicitly usable.
        ranked = service.rank_references(document["id"], "retrieval", run_id=old_run)
        ref_id = ranked["included"][0]["reference_id"]
        first = service.search_selected(document["id"], [ref_id], run_id=old_run)
        second = service.search_selected(document["id"], [ref_id], run_id=old_run)
    assert first["results"][0]["cached"] is False and second["results"][0]["cached"] is True
    assert first["results"][0]["retrieved_at"] == second["results"][0]["retrieved_at"]
    assert len(calls) == 1 and len(library.list_documents()) == 1
