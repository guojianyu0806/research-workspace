from pathlib import Path

import httpx
import pymupdf
import pytest

from research_workspace.discovery import DiscoveryService
from research_workspace.graph import GraphService, pagerank
from research_workspace.library import Library, LibraryError


def import_paper(library: Library, title: str, *, references: str = "", identifier: str = "", body: str = "Shared topic language model research.") -> dict:
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.insert_text((35, 60), title, fontsize=16)
        page.insert_text((35, 100), body, fontsize=11)
        if identifier:
            page.insert_text((35, 135), "arXiv: " + identifier, fontsize=10)
        if references:
            page = pdf.new_page()
            page.insert_text((35, 60), "References", fontsize=16)
            page.insert_textbox(pymupdf.Rect(35, 95, 550, 750), references, fontsize=10)
        pdf.set_metadata({"title": title})
        result = library.import_document(pdf.tobytes(), title + ".pdf")
    while library.process_next_job():
        pass
    return library.get_document(result["document"]["id"])


@pytest.fixture
def library(tmp_path: Path) -> Library:
    return Library(tmp_path / "library")


def test_exact_reference_title_edges_have_source_evidence(library):
    target = import_paper(library, "Foundations of Reliable Evidence Retrieval")
    source = import_paper(library, "Applications of Evidence Retrieval Systems", references="[1] A. Example. Foundations of Reliable Evidence Retrieval. 2024.")
    result = GraphService(library).build([source["id"], target["id"]])
    assert len(result["edges"]) == 1
    edge = result["edges"][0]
    assert (edge["source"], edge["target"]) == (source["id"], target["id"])
    assert edge["match_method"] == "exact_title_in_reference"
    evidence = edge["evidence"][0]
    assert evidence["document_id"] == source["id"]
    assert evidence["run_id"] == source["available_run"]["id"]
    assert evidence["page"] == 2
    page = library.get_page(source["id"], evidence["page"], evidence["run_id"])
    assert evidence["block_id"] in [block["id"] for block in page["blocks"]]
    target_node = next(node for node in result["nodes"] if node["id"] == target["id"])
    assert target_node["evidence"]
    assert target_node["in_degree"] == 1
    assert target_node["pagerank"] > next(node for node in result["nodes"] if node["id"] == source["id"])["pagerank"]
    assert result["network_used"] is False


@pytest.mark.parametrize(("verified_marker", "prominent_title", "expected_edge"), [
    (True, True, True),
    (False, True, False),
    (True, False, False),
])
def test_arxiv_filename_placeholder_uses_only_anchored_native_title(
        library, verified_marker, prominent_title, expected_edge):
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.insert_text((40, 70), "Publisher's attribution notice preceding the title.")
        for y, line in [(140, "Foundations of Reliable"), (160, "Evidence Retrieval")]:
            size = 18 if prominent_title else 11
            start = (page.rect.width - pymupdf.get_text_length(line, fontname="hebo", fontsize=size)) / 2
            page.insert_text((start, y), line, fontname="hebo", fontsize=size)
        page.insert_text((240, 230), "Example Author")
        page.insert_text((40, 275), "Text from the main paper.")
        if verified_marker:
            page.insert_text((30, 500), "arXiv:2401.12345v2", rotate=90)
        target = library.import_document(pdf.tobytes(), "2401.12345v2.pdf")["document"]
    assert target["title"] == "2401.12345v2"
    source = import_paper(library, "Applying a Reliable Evidence Framework",
                          references="[1] Example Author. Foundations of Reliable Evidence Retrieval. 2024.")
    graph = GraphService(library).build([source["id"], target["id"]])
    if expected_edge:
        edge, = graph["edges"]
        assert (edge["source"], edge["target"]) == (source["id"], target["id"])
        assert edge["match_method"] == "exact_title_in_reference"
        assert edge["evidence"][0]["page"] == 2
        node = next(node for node in graph["nodes"] if node["id"] == target["id"])
        assert node["title"] == "Foundations of Reliable Evidence Retrieval"
        assert all(evidence["page"] == 1 for evidence in node["evidence"])
        assert library.get_document(target["id"])["title"] == "2401.12345v2"
    else:
        assert graph["edges"] == []


def test_body_similarity_or_title_mention_is_not_a_citation(library):
    target = import_paper(library, "Foundations of Reliable Evidence Retrieval")
    source = import_paper(library, "Related Research on Evidence Retrieval", body="Foundations of Reliable Evidence Retrieval is a phrase mentioned in prose.")
    result = GraphService(library).build()
    assert result["edges"] == []
    assert all(node["role"] == "isolated" for node in result["nodes"])
    assert result["unresolved_count"] == 2


def test_reference_continued_in_separate_pdf_block_keeps_both_anchors(library):
    target = import_paper(library, 'Foundations of Reliable Evidence Retrieval')
    with pymupdf.open() as pdf:
        first = pdf.new_page()
        first.insert_text((35,60),'Research with a split reference entry')
        page = pdf.new_page()
        page.insert_text((35,60),'References')
        page.insert_text((35,100),'[1] Example Author. Foundations of Reliable')
        page.insert_text((35,150),'Evidence Retrieval. 2024.')
        imported = library.import_document(pdf.tobytes(),'split-reference.pdf')['document']
    library.process_next_job()
    result = GraphService(library).build([imported['id'],target['id']])
    assert len(result['edges']) == 1
    edge = result['edges'][0]
    assert edge['target'] == target['id']
    assert len({entry['block_id'] for entry in edge['evidence']}) == 2


def test_ambiguous_same_title_does_not_create_edges(library):
    title = "Foundations of Reliable Evidence Retrieval"
    first = import_paper(library, title, body="Version one.")
    second = import_paper(library, title, body="Version two.")
    source = import_paper(library, "Applying a Reliable Evidence Framework", references="[1] " + title + ".")
    result = GraphService(library).build([first["id"], second["id"], source["id"]])
    assert result["edges"] == []
    assert any("多个同名" in item["reason"] for item in result["unresolved"])


def test_explicit_arxiv_version_disambiguates_same_title(library):
    title = "Foundations of Reliable Evidence Retrieval"
    first = import_paper(library, title, identifier="2401.12345v1")
    second = import_paper(library, title, identifier="2401.12345v2")
    source = import_paper(library, "Applying a Reliable Evidence Framework", references="[1] " + title + ". arXiv:2401.12345v1.")
    result = GraphService(library).build([first["id"], second["id"], source["id"]])
    assert [(edge["source"], edge["target"]) for edge in result["edges"]] == [(source["id"], first["id"])]
    assert result["edges"][0]["match_method"] == "arxiv_identifier"


def test_unversioned_arxiv_reference_stays_ambiguous(library):
    first = import_paper(library, "First Edition of Research Methods", identifier="2401.12345v1")
    second = import_paper(library, "Second Edition of Research Methods", identifier="2401.12345v2")
    source = import_paper(library, "Applying a Reliable Evidence Framework", references="[1] Research methods. arXiv:2401.12345.")
    result = GraphService(library).build([first["id"], second["id"], source["id"]])
    assert result["edges"] == []
    assert any("多个同名" in item["reason"] for item in result["unresolved"])


def test_matching_title_does_not_override_conflicting_identifier(library):
    title = "Foundations of Reliable Evidence Retrieval"
    target = import_paper(library, title, identifier="2401.12345v1")
    source = import_paper(library, "Applying a Reliable Evidence Framework", references="[1] " + title + ". arXiv:2401.99999v1.")
    result = GraphService(library).build([target["id"], source["id"]])
    assert result["edges"] == []
    assert result["unresolved_count"] > 0


def test_metadata_alone_cannot_fake_local_title_identity(library):
    target = import_paper(library, "Actual Original Article Title")
    with library._connection() as db:
        db.execute("UPDATE documents SET title=? WHERE id=?", ("A Fabricated Unrelated Article Title", target["id"]))
    source = import_paper(library, "Applying a Reliable Evidence Framework", references="[1] A Fabricated Unrelated Article Title.")
    result = GraphService(library).build([source["id"], target["id"]])
    assert result["edges"] == []


def test_structure_reference_reused_only_with_matching_retained_block(library, monkeypatch):
    target = import_paper(library, "Foundations of Reliable Evidence Retrieval")
    source = import_paper(library, "Applying a Reliable Evidence Framework", references="[1] Foundations of Reliable Evidence Retrieval.")
    block = library.get_page(source["id"], 2)["blocks"][-1]
    reference = {"id": "ref-real", "text": block["text"], "evidence": {
        "document_id": source["id"], "run_id": source["available_run"]["id"], "page": 2,
        "block_id": block["id"], "bbox": block["bbox"], "text": block["text"]}, "citation_ids": ["cite-real"]}
    monkeypatch.setattr(library, "get_structure", lambda doc_id, run_id=None: {"method": "heuristic", "references": [reference] if doc_id == source["id"] else []}, raising=False)
    result = GraphService(library).build([source["id"], target["id"]])
    assert result["edges"][0]["citation_ids"] == ["cite-real"]
    reference["evidence"]["block_id"] = "not-a-real-block"
    assert GraphService(library).build([source["id"], target["id"]])["edges"] == []


def test_verified_external_metadata_is_distinct_from_fulltext(library):
    content = b'''<feed xmlns="http://www.w3.org/2005/Atom"><entry>
    <id>https://arxiv.org/abs/2401.12345v1</id><title>External Verified Metadata Example</title>
    <summary>Test fixture metadata.</summary><link title="pdf" href="https://arxiv.org/pdf/2401.12345v1"/>
    </entry></feed>'''
    discovery = DiscoveryService(library, httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=content))), request_interval=0)
    discovery.search("external example")
    source = import_paper(library, "Applying a Reliable Evidence Framework", references="[1] External Verified Metadata Example. arXiv:2401.12345v1.")
    result = GraphService(library).build([source["id"]])
    assert len(result["edges"]) == 1
    external = next(node for node in result["nodes"] if node["kind"] == "external_metadata")
    assert external["document_id"] is None
    assert not external["fulltext_available"]
    assert external["source_url"] == "https://arxiv.org/abs/2401.12345v1"


def test_twenty_one_document_chain_and_pagerank_mass(library):
    documents = []
    for index in range(21):
        title = f"Research Evidence Chain Article Number {index:02d}"
        refs = f"[1] Research Evidence Chain Article Number {index - 1:02d}." if index else ""
        documents.append(import_paper(library, title, references=refs))
    result = GraphService(library).build([doc["id"] for doc in documents])
    assert len(result["nodes"]) == 21
    assert len(result["edges"]) == 20
    assert abs(sum(node["pagerank"] for node in result["nodes"]) - 1) < 1e-6
    assert result["nodes"][0]["id"] == documents[0]["id"]
    assert all(edge["evidence"] for edge in result["edges"])


def test_unknown_workspace_document_is_rejected(library):
    with pytest.raises(KeyError):
        GraphService(library).build(["another-workspace-document"])
    with pytest.raises(LibraryError):
        GraphService(library).build("not a list")
    assert GraphService(library).build()["nodes"] == []


def test_pagerank_isolated_and_dangling_mass():
    assert pagerank([], []) == {}
    assert pagerank(["a", "b"], []) == {"a": .5, "b": .5}
    result = pagerank(["a", "b"], [{"source": "a", "target": "b"}])
    assert result["b"] > result["a"]
    assert sum(result.values()) == pytest.approx(1)


def test_real_title_split_into_adjacent_pdf_blocks_has_all_identity_anchors(library):
    title='Reliable Evidence Retrieval for Scientific Research'
    with pymupdf.open() as pdf:
        page=pdf.new_page()
        page.insert_text((40,60),'Reliable Evidence Retrieval for')
        page.insert_text((100,80),'Scientific Research')
        page.insert_text((40,140),'Actual paper body.')
        target=library.import_document(pdf.tobytes(),'split-title.pdf')['document']
    library.process_next_job();library.update_metadata(target['id'],title=title)
    source=import_paper(library,'Applications of Scientific Evidence Retrieval',references='[1] Example Author. '+title+'. 2024.')
    graph=GraphService(library).build([source['id'],target['id']])
    assert len(graph['edges'])==1 and graph['edges'][0]['target']==target['id']
    node=next(n for n in graph['nodes'] if n['id']==target['id'])
    assert len(node['evidence'])==2
    assert all(e['document_id']==target['id'] for e in node['evidence'])
