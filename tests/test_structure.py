from research_workspace.structure import extract_structure


def block(text, page=1, name="block-one"):
    return {"document_id": "doc-one", "run_id": "run-one", "page": page,
            "block_id": name, "text": text, "bbox": [0.1, 0.1, 0.8, 0.8]}


def test_heading_hierarchy_and_citation_round_trip_have_original_evidence():
    blocks = [block("Abstract\nA measured result.\n1 Introduction\nPrior work [1, 2].\n1.1 Method\nMore support [1]."),
              block("References\n[1] Alice. A study. 2020. doi:10.1234/example.\n[2] Bob. Another study. 2021. arXiv:1706.03762", 2, "refs")]
    result = extract_structure(blocks, "doc-one", "run-one")
    assert result["method"] == "heuristic"
    headings = {item["title"]: item for item in result["headings"]}
    assert headings["1.1 Method"]["parent_id"] == headings["1 Introduction"]["id"]
    assert result["abstract"]["text"] == "A measured result."
    assert len(result["references"]) == 2
    assert result["references"][0]["doi"] == "10.1234/example"
    assert result["references"][1]["arxiv_id"] == "1706.03762"
    assert len(result["citations"]) == 2
    assert len(result["references"][0]["citation_ids"]) == 2
    for citation in result["citations"]:
        evidence = citation["evidence"]
        source = next(item for item in blocks if item["block_id"] == evidence["block_id"])
        assert source["text"][evidence["char_start"]:evidence["char_end"]] == citation["marker"]
        for target in citation["reference_ids"]:
            reference = next(item for item in result["references"] if item["id"] == target)
            assert citation["id"] in reference["citation_ids"]
            assert reference["evidence"]["page"] == 2


def test_reference_continuations_unresolved_citations_and_appendix():
    result = extract_structure([
        block("1 Intro\nKnown [1] and missing [9]."),
        block("References\n[1] Alice and Bob.\nContinuation with publication details.\nAppendix A\nAdditional citation [1].", 2),
    ], "doc-one", "run-one")
    assert "Continuation" in result["references"][0]["text"]
    assert "Appendix" not in result["references"][0]["text"]
    missing = next(item for item in result["citations"] if item["marker"] == "[9]")
    assert missing["reference_ids"] == []
    assert missing["unresolved_labels"] == ["9"]
    assert len(result["references"][0]["citation_ids"]) == 2


def test_missing_structure_does_not_fabricate_references_or_formulas():
    result = extract_structure([block("Unstructured text about a model and equation.")], "doc-one", "run-one")
    assert result["references"] == []
    assert result["headings"] == []
    assert result["abstract"] is None
    assert "latex_formulas" in result["unavailable_fields"]


def test_split_number_headings_exclude_year_and_numeric_body_sentences():
    result = extract_structure([block(
        "Abstract\nA compact summary.\n1\nIntroduction\n2014 English-French dataset description\n"
        "3.5 days.\n23.75\nDeep-Att + PosUnk [35]\n2\nModel Architecture\n2.1\nAttention Mechanism\nMeasured results follow."
    )], "doc-one", "run-one")
    titles = [heading["title"] for heading in result["headings"]]
    assert titles == ["Abstract", "1 Introduction", "2 Model Architecture", "2.1 Attention Mechanism"]
    assert result["abstract"]["text"] == "A compact summary."
    assert result["headings"][-1]["parent_id"] == result["headings"][-2]["id"]
