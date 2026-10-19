import copy

import pytest

from research_workspace.structure import extract_structure


def block(text, number=0, box=None, page=1):
    return {"document_id": "doc", "run_id": "run", "page": page, "block_id": f"b{number}",
            "text": text, "bbox": box or [.1,.2,.8,.25]}


def extract(*blocks):
    return extract_structure(list(blocks), "doc", "run")["front_matter"]


def verify(result, blocks):
    sources = {b["block_id"]:b for b in blocks}
    assert result["audit"]["source_spans_valid"]
    for item in result["authors"]+result["affiliations"]:
        e = item["evidence"]; source = sources[e["block_id"]]
        assert e["text"] == source["text"][e["char_start"]:e["char_end"]]
        assert e["bbox"] == source["bbox"]
        assert e["run_id"] == "run" and e["document_id"] == "doc"
    authors = {a["id"]:a for a in result["authors"]}
    orgs = {o["id"]:o for o in result["affiliations"]}
    for r in result["relations"]:
        assert r["evidence"] == [authors[r["author_id"]]["evidence"],orgs[r["affiliation_id"]]["evidence"]]
        assert r["affiliation_id"] in authors[r["author_id"]]["affiliation_ids"]
        assert r["status"] == "candidate"


def test_shared_single_block_affiliation_and_exact_character_offsets():
    blocks = [block("A Study of Models",0),block("  Jacob Devlin\nMing-Wei Chang\nKenton Lee\nKristina Toutanova\nGoogle AI Language\n{a,b,c,d}@google.com",1),block("Abstract\nA short summary.",2)]
    before = copy.deepcopy(blocks)
    result = extract(*blocks)
    assert [a["name"] for a in result["authors"]] == ["Jacob Devlin","Ming-Wei Chang","Kenton Lee","Kristina Toutanova"]
    assert len(result["relations"]) == 4
    assert result["audit"]["unresolved_author_count"] == 0
    assert all(r["method"] == "single_affiliation_in_author_block" for r in result["relations"])
    verify(result,blocks); assert before == blocks


def test_multiple_columns_do_not_assign_neighboring_author_institution():
    blocks = [block("Alice Smith",0,[.1,.2,.3,.22]),block("Bob Jones",1,[.55,.2,.75,.22]),
              block("University of A\nalice@example.org",2,[.09,.23,.31,.26]),
              block("Institute of B\nbob@example.org",3,[.54,.23,.76,.26]),block("Abstract",4)]
    result = extract(*blocks)
    assert len(result["relations"]) == 2
    assert [org["text"] for org in result["affiliations"]] == ["University of A","Institute of B"]
    assert result["authors"][0]["affiliation_ids"] == ["affiliation-1"]
    assert result["authors"][1]["affiliation_ids"] == ["affiliation-2"]
    verify(result,blocks)


def test_missing_affiliation_is_not_inferred_from_email_domain_or_footnote():
    blocks = [block("Alice Smith∗†\nalice@google.com",0),block("Abstract",1),
              block("†Work performed while at Google Research.",2)]
    result = extract(*blocks)
    assert result["authors"][0]["name"] == "Alice Smith"
    assert result["authors"][0]["affiliation_status"] == "unresolved"
    assert result["affiliations"] == [] and result["relations"] == []
    verify(result,blocks)


@pytest.mark.parametrize("digit", ["1", "¹"])
def test_explicit_numeric_markers_bind_across_blocks_without_proximity_guess(digit):
    blocks = [block(f"Alice Smith{digit}\nBob Jones²",0),block(f"{digit} University of A",1),
              block("² Institute of B",2),block("Abstract",3)]
    result = extract(*blocks)
    assert [a["affiliation_ids"] for a in result["authors"]] == [["affiliation-1"],["affiliation-2"]]
    assert all(r["method"] == "explicit_affiliation_marker" for r in result["relations"])
    verify(result,blocks)


def test_ambiguous_marker_does_not_fall_back_to_same_block_or_nearest_org():
    result = extract(block("Alice Smith¹\n¹University of A\nalice@example.org"),
                     block("¹Institute of B",1),block("Abstract",2))
    assert len(result["authors"]) == 1
    assert result["authors"][0]["affiliation_status"] == "unresolved"
    assert not result["relations"]


def test_multiple_unmarked_orgs_in_one_block_remain_unresolved():
    result = extract(block("Alice Smith\nUniversity of A\nInstitute of B\nalice@example.org"),block("Abstract",1))
    assert len(result["affiliations"]) == 2
    assert not result["relations"]


def test_marked_org_cannot_be_assigned_to_unmarked_author():
    result = extract(block("Alice Smith\n¹University of A\nalice@example.org"),block("Abstract",1))
    assert result["authors"][0]["affiliation_status"] == "unresolved"


def test_wide_shared_org_below_multiple_columns_is_ambiguous():
    result = extract(block("Alice Smith",0,[.1,.2,.3,.22]),block("Bob Jones",1,[.5,.2,.7,.22]),
                     block("University of A",2,[.1,.23,.8,.25]),block("Abstract",3))
    assert len(result["authors"]) == 2
    assert not result["relations"]


@pytest.mark.parametrize("tail", ["Abstract", "1 Introduction", "摘要"])
def test_body_names_and_organizations_after_boundary_are_not_front_matter(tail):
    result = extract(block(tail,0),block("Alice Smith\nUniversity of A\nalice@example.org",1))
    assert not result["authors"] and not result["affiliations"]


def test_unbounded_header_and_later_page_do_not_create_candidates():
    result = extract(block("Alice Smith\nUniversity of A\nalice@example.org",0),block("Abstract",1,page=2))
    assert result["reason"] == "no_bounded_front_matter"
    assert not result["authors"]


def test_rotated_stamp_and_title_not_treated_as_author():
    blocks = [block("Language Understanding",0),block("Attention Is All You Need",1),
              block("Alice Smith\nUniversity of A\na@example.org",2),
              block("Arxiv Research",3,[.01,.1,.04,.7]),block("Abstract",4)]
    result = extract(*blocks)
    assert [a["name"] for a in result["authors"]] == ["Alice Smith"]
    assert [o["text"] for o in result["affiliations"]] == ["University of A"]
    verify(result,blocks)


def test_comma_separated_authors_keep_per_name_exact_offsets():
    blocks = [block("Alice Smith, Bob Jones\nUniversity of A\na@example.org"),block("Abstract",1)]
    result = extract(*blocks)
    assert [a["name"] for a in result["authors"]] == ["Alice Smith","Bob Jones"]
    verify(result,blocks)


def test_partial_structure_reports_unresolved_instead_of_unconditionally_missing():
    result = extract_structure([block("Alice Smith\nalice@example.org"),block("Abstract",1)],"doc","run")
    assert "author_affiliations_not_fully_resolved" in result["unavailable_fields"]
    assert result["front_matter"]["audit"]["unresolved_author_count"] == 1


def test_symbol_marked_multi_org_line_does_not_assign_combined_org_to_everyone():
    blocks = [block("Patrick Lewis†‡, Ethan Perez⋆",0),block("Mike Lewis†",1),
              block("†Facebook AI Research; ‡University College London; ⋆New York University;",2),block("Abstract",3)]
    result = extract(*blocks)
    assert [a["affiliation_ids"] for a in result["authors"]] == [["affiliation-1","affiliation-2"],["affiliation-3"],["affiliation-1"]]
    verify(result,blocks)


def test_missing_symbol_in_existing_affiliation_marker_system_stays_unresolved():
    result = extract(block("Patrick Lewis†‡\n†Facebook AI Research\na@example.org"),block("Abstract",1))
    assert len(result["authors"]) == 1
    assert result["authors"][0]["affiliation_status"] == "unresolved"
    assert not result["relations"]


def test_address_and_preprint_are_not_names_and_inline_authors_keep_own_org():
    blocks = [block("A PREPRINT",0),block("LIANGYU CHEN, University of A, China\nYULONG YE, University of B, United Kingdom",1),
              block("Alice Smith\nUniversity of C\n10 Crichton Street, Room 4.19\nEdinburgh, EH8 9AB, United Kingdom",2),
              block("New York, USA\na@example.org",3),block("Abstract",4)]
    result = extract(*blocks)
    assert [a["name"] for a in result["authors"]] == ["LIANGYU CHEN","YULONG YE","Alice Smith"]
    assert [a["affiliation_ids"] for a in result["authors"]] == [["affiliation-1"],["affiliation-2"],["affiliation-3"]]
    verify(result,blocks)


def test_number_fused_to_institution_word_still_has_exact_text_evidence():
    blocks = [block("Nicu Sebe6",0),block("6University of Trento",1),block("Abstract",2)]
    result = extract(*blocks)
    assert result["affiliations"][0]["text"] == "University of Trento"
    assert result["authors"][0]["affiliation_ids"] == ["affiliation-1"]
    verify(result,blocks)


def test_titles_near_author_contact_blocks_are_not_contact_context():
    result = extract(block("Differential Privacy",0,[.3,.12,.6,.15]),
                     block("Alice Smith\nalice@example.org\nUniversity of A",1,[.1,.16,.3,.21]),
                     block("Abstract",2))
    assert [a["name"] for a in result["authors"]] == ["Alice Smith"]


def test_independent_role_is_not_person_and_email_only_does_not_fill_affiliation():
    blocks = [block("Xinyi Ai\nIndependent Researcher\nMountain View, CA, USA",0,[.4,.1,.6,.15]),
              block("a@example.org",1,[.4,.15,.6,.17]),block("Abstract",2)]
    result = extract(*blocks)
    assert [a["name"] for a in result["authors"]] == ["Xinyi Ai"]
    assert not result["relations"]
    verify(result,blocks)


def test_prior_author_row_does_not_block_unique_next_row_institution():
    blocks = [block("Alice Smith\nUniversity of A\na@example.org",0,[.5,.29,.7,.33]),
              block("Bob Jones",1,[.55,.35,.7,.37]),block("Institute of B",2,[.5,.375,.75,.40]),block("Abstract",3)]
    result = extract(*blocks)
    assert result["authors"][1]["affiliation_ids"] == ["affiliation-2"]


def test_api_fixed_versions_preserve_catalog_and_reject_other_account_source(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCH_DATA_DIR",str(tmp_path/"bootstrap"))
    from fastapi.testclient import TestClient
    import pymupdf
    from research_workspace.api import create_app
    from research_workspace.library import Library
    app = create_app(tmp_path/"service",start_worker=False)
    token,alice = app.state.accounts.register("Alice","fixture-only-password")
    bob_token,_ = app.state.accounts.register("Bob","fixture-only-password")
    library = Library(app.state.accounts.directory(alice))
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        for y,text in [(70,"Local Research Evidence"),(120,"Alice Smith"),(145,"University of A"),(180,"Abstract"),(210,"A real source sentence.")]:
            page.insert_text((50,y),text)
        doc = library.import_document(pdf.tobytes(),"fixture.pdf")["document"]
    library.process_next_job()
    doc = library.update_metadata(doc["id"],authors=["Manually Confirmed Author"])
    old = doc["current_run"]["id"]
    library.create_run(doc["id"]); library.process_next_job()
    new = library.get_document(doc["id"])["current_run"]["id"]
    # Real structure endpoint and account dependency; never contacts a model.
    with TestClient(app,base_url="http://127.0.0.1:8765") as client:
        from research_workspace.accounts import Accounts
        client.cookies.set(Accounts.COOKIE,token)
        for run in [old,new]:
            response = client.get(f"/api/documents/{doc['id']}/structure",params={"run_id":run})
            assert response.status_code == 200
            front = response.json()["front_matter"]
            assert front["authors"][0]["name"] == "Alice Smith"
            assert front["authors"][0]["evidence"]["run_id"] == run
            assert front["relations"][0]["evidence"][1]["run_id"] == run
        assert library.get_document(doc["id"])["authors"] == ["Manually Confirmed Author"]
        client.cookies.set(Accounts.COOKIE,bob_token)
        assert client.get(f"/api/documents/{doc['id']}/structure",params={"run_id":old}).status_code == 404
