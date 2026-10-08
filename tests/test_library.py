from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import pymupdf
import pytest

from research_workspace.library import Library, LibraryError
from research_workspace.worker import WorkerLock


def pdf_bytes(texts: list[str | None], *, encrypted: bool = False) -> bytes:
    with pymupdf.open() as pdf:
        for text in texts:
            page = pdf.new_page(width=400, height=600)
            if text is not None:
                page.insert_text((40, 80), text)
        if encrypted:
            return pdf.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256,
                               user_pw="test-password", owner_pw="owner-password")
        return pdf.tobytes()


@pytest.fixture
def library(tmp_path: Path) -> Library:
    return Library(tmp_path / "library")


def test_exact_bytes_deduplicate_but_filename_does_not(library: Library):
    original = pdf_bytes(["First original evidence"])
    first = library.import_document(original, "../../same.pdf")
    duplicate = library.import_document(original, "renamed.pdf")
    other = library.import_document(pdf_bytes(["Different evidence"]), "same.pdf")
    assert not first["duplicate"]
    assert first["document"]["filename"] == "same.pdf"
    assert duplicate["duplicate"]
    assert duplicate["document"]["id"] == first["document"]["id"]
    assert other["document"]["id"] != first["document"]["id"]
    assert len(library.list_documents()) == 2
    assert len(list(library.files_dir.glob("*.pdf"))) == 2
    assert library._source_path(first["document"]["id"]).read_bytes() == original


@pytest.mark.parametrize("content", [b"", b"not a pdf", b"%PDF-1.7\nbroken page tree"])
def test_invalid_pdf_is_not_registered(library: Library, content: bytes):
    with pytest.raises(LibraryError):
        library.import_document(content, "invalid.pdf")
    assert library.list_documents() == []
    assert list(library.files_dir.iterdir()) == []


def test_encrypted_and_zero_page_pdf_are_rejected(library: Library):
    with pytest.raises(LibraryError, match="加密"):
        library.import_document(pdf_bytes(["Protected"], encrypted=True), "secret.pdf")
    zero_pages = (
        b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
        b"2 0 obj\n<< /Type /Pages /Kids [] /Count 0 >>\nendobj\n"
        b"trailer\n<< /Root 1 0 R >>\n%%EOF"
    )
    with pytest.raises(LibraryError, match="没有可读取"):
        library.import_document(zero_pages, "empty.pdf")
    assert library.list_documents() == []


def test_pending_page_and_persistence(library: Library):
    doc = library.import_document(pdf_bytes(["Persistent evidence"]), "paper.pdf")["document"]
    page = library.get_page(doc["id"], 1)
    assert page["status"] == "pending"
    assert page["blocks"] == []
    assert page["width"] == 400
    assert library.create_run(doc["id"])["id"] == doc["current_run"]["id"]
    assert library.process_next_job()
    reopened = Library(library.data_dir)
    assert reopened.get_document(doc["id"])["current_run"]["status"] == "completed"
    assert reopened.search("persistent")[0]["page"] == 1
    assert not reopened.process_next_job()


def test_no_text_and_mixed_documents_are_honest(library: Library):
    blank = library.import_document(pdf_bytes([None]), "blank.pdf")["document"]
    mixed = library.import_document(pdf_bytes(["Visible text", None]), "mixed.pdf")["document"]
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        image = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 16, 16), 0)
        image.clear_with(100)
        page.insert_image(page.rect, stream=image.tobytes("png"))
        image_doc = library.import_document(pdf.tobytes(), "image-only.pdf")["document"]
    while library.process_next_job():
        pass
    assert library.get_document(blank["id"])["current_run"]["status"] == "needs_ocr"
    assert library.get_document(blank["id"])["available_run"] is None
    assert library.get_page(blank["id"], 1)["status"] == "no_extractable_text"
    assert library.get_document(image_doc["id"])["current_run"]["status"] == "needs_ocr"
    run = library.get_document(mixed["id"])["current_run"]
    assert (run["status"], run["pages_done"], run["text_pages"], run["no_text_pages"]) == ("partial", 2, 1, 1)
    assert library.get_document(mixed["id"])["available_run"]["id"] == run["id"]
    assert library.search("visible", document_id=mixed["id"])


def test_reparse_retains_old_evidence_and_original(library: Library):
    content = pdf_bytes(["Versioned source evidence"])
    doc = library.import_document(content, "evidence.pdf")["document"]
    first_run = doc["current_run"]["id"]
    library.process_next_job()
    first_page = library.get_page(doc["id"], 1, first_run)
    second_run = library.create_run(doc["id"])
    assert second_run["id"] != first_run
    assert second_run["version"] == 2
    assert library.create_run(doc["id"])["id"] == second_run["id"]
    library.process_next_job()
    assert library.get_page(doc["id"], 1, first_run) == first_page
    assert library.search("source", run_id=first_run)[0]["block_id"] == first_page["blocks"][0]["id"]
    assert library.search("source", document_id=doc["id"])[0]["run_id"] == second_run["id"]
    assert library._source_path(doc["id"]).read_bytes() == content
    assert len(library.list_runs(doc["id"])) == 2


@pytest.mark.parametrize("replacement_status", ["failed", "needs_ocr"])
def test_reparse_keeps_default_evidence_until_readable_replacement(
    library: Library, monkeypatch, replacement_status: str,
):
    doc = library.import_document(pdf_bytes(["Stable evidence"]), "stable.pdf")["document"]
    library.process_next_job()
    old_run_id = doc["current_run"]["id"]
    old_page = library.get_page(doc["id"], 1)
    replacement = library.create_run(doc["id"])

    def assert_old_defaults():
        current_doc = library.get_document(doc["id"])
        assert current_doc["current_run"]["id"] == replacement["id"]
        assert current_doc["available_run"]["id"] == old_run_id
        assert library.get_page(doc["id"], 1) == old_page
        assert [hit["run_id"] for hit in library.search("Stable")] == [old_run_id]
        assert library.search("Stable", document_id=doc["id"])[0]["run_id"] == old_run_id

    assert library.get_run(replacement["id"])["status"] == "queued"
    assert_old_defaults()
    assert library._claim_run()["id"] == replacement["id"]
    assert library.get_run(replacement["id"])["status"] == "running"
    assert_old_defaults()
    assert library.get_page(doc["id"], 1, replacement["id"])["status"] == "pending"

    original_extract = library._page_blocks

    def unusable_result(source_page):
        if replacement_status == "failed":
            raise ValueError("simulated replacement failure")
        return []

    monkeypatch.setattr(library, "_page_blocks", unusable_result)
    library.recover_interrupted_runs()
    library.process_next_job()
    assert library.get_run(replacement["id"])["status"] == replacement_status
    assert_old_defaults()

    monkeypatch.setattr(library, "_page_blocks", original_extract)
    successful = library.create_run(doc["id"])
    library.process_next_job()
    latest_doc = library.get_document(doc["id"])
    assert latest_doc["current_run"]["id"] == successful["id"]
    assert latest_doc["available_run"]["id"] == successful["id"]
    assert library.get_page(doc["id"], 1)["run_id"] == successful["id"]
    assert [hit["run_id"] for hit in library.search("Stable")] == [successful["id"]]
    assert library.get_page(doc["id"], 1, old_run_id) == old_page


def test_interrupted_job_resumes_without_replacing_saved_pages(library: Library, monkeypatch):
    doc = library.import_document(pdf_bytes(["First saved page", "Second page", "Last page"]), "resume.pdf")["document"]
    run_id = doc["current_run"]["id"]
    original_save = library._save_page

    def interrupt_on_second(run_id, page, *args, **kwargs):
        if page == 2:
            raise KeyboardInterrupt()
        return original_save(run_id, page, *args, **kwargs)

    monkeypatch.setattr(library, "_save_page", interrupt_on_second)
    with pytest.raises(KeyboardInterrupt):
        library.process_next_job()
    first_page = library.get_page(doc["id"], 1)
    assert library.get_run(run_id)["pages_done"] == 1
    reopened = Library(library.data_dir)
    assert reopened.get_run(run_id)["status"] == "running"
    assert reopened.get_document(doc["id"])["available_run"] is None
    assert reopened.search("First saved page")[0]["run_id"] == run_id
    assert reopened.get_page(doc["id"], 1) == first_page
    assert reopened.recover_interrupted_runs() == 1
    assert reopened.process_next_job()
    assert reopened.get_run(run_id)["status"] == "completed"
    assert reopened.get_run(run_id)["pages_done"] == 3
    assert reopened.get_page(doc["id"], 1) == first_page
    assert reopened.search("last page")[0]["page"] == 3


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_bbox_covers_actual_ink_after_crop_and_rotation(library: Library, rotation: int):
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=500, height=700)
        page.insert_text((100, 150), "Rotated evidence", fontsize=18)
        page.set_cropbox(pymupdf.Rect(50, 50, 450, 650))
        page.set_rotation(rotation)
        content = pdf.tobytes()
    doc = library.import_document(content, "rotated.pdf")["document"]
    library.process_next_job()
    parsed = library.get_page(doc["id"], 1)
    assert parsed["coordinate_system"] == "normalized_rendered_page"
    bbox = parsed["blocks"][0]["bbox"]
    image = pymupdf.Pixmap(library.render_page(doc["id"], 1, scale=1))
    samples = image.samples
    dark = [(pixel % image.width, pixel // image.width)
            for pixel in range(image.width * image.height)
            if min(samples[pixel * image.n:pixel * image.n + 3]) < 150]
    assert dark
    xs, ys = zip(*dark)
    assert bbox[0] * image.width - 2 <= min(xs)
    assert bbox[1] * image.height - 2 <= min(ys)
    assert bbox[2] * image.width + 2 >= max(xs)
    assert bbox[3] * image.height + 2 >= max(ys)
    assert all(0 <= coordinate <= 1 for coordinate in bbox)
    assert (parsed["width"], parsed["height"]) == (image.width, image.height)


def test_541_pages_include_tail_evidence(library: Library):
    texts = [f"Book page {number}" for number in range(1, 542)]
    texts[-1] = "TAIL_EVIDENCE_541 has exact parameter 0.998"
    doc = library.import_document(pdf_bytes(texts), "long-fixture.pdf")["document"]
    library.process_next_job()
    run = library.get_document(doc["id"])["current_run"]
    assert (run["status"], run["pages_done"], run["total_pages"]) == ("completed", 541, 541)
    assert library.search("tail_evidence_541")[0]["page"] == 541
    assert library.search("Book page 270")[0]["page"] == 270
    assert "0.998" in library.get_page(doc["id"], 541)["text"]


def test_literal_case_and_line_break_search(library: Library):
    doc = library.import_document(pdf_bytes(["Alpha Beta\nCaseFold 100% A_B %_ SQL ' quoted"]), "literal.pdf")["document"]
    library.process_next_job()
    for query in ["alpha   BETA", "BETA\n CASEFOLD", "100%", "A_B", "%_", "' quoted"]:
        assert library.search(query), query
    assert library.search("missing%_") == []
    assert library.search("%' OR 1=1 --") == []
    assert library.search("   ") == []
    with pytest.raises(LibraryError):
        library.search("x", limit=101)
    with pytest.raises(LibraryError):
        library.get_page(doc["id"], 0)
    with pytest.raises(LibraryError):
        library.render_page(doc["id"], 1, scale=float("nan"))


def test_chinese_literal_query(library: Library):
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.insert_text((40, 80), "原文检索参数和证据", fontname="china-s")
        content = pdf.tobytes()
    library.import_document(content, "中文.pdf")
    library.process_next_job()
    assert "原文检索参数" in library.search("检索参数")[0]["text"]


def test_single_page_failure_does_not_discard_other_pages(library: Library, monkeypatch):
    doc = library.import_document(pdf_bytes(["Broken page", "Surviving evidence"]), "partial.pdf")["document"]
    original_extract = library._page_blocks

    def fail_first(source_page):
        if source_page.number == 0:
            raise ValueError("simulated extraction failure")
        return original_extract(source_page)

    monkeypatch.setattr(library, "_page_blocks", fail_first)
    library.process_next_job()
    run = library.get_document(doc["id"])["current_run"]
    assert (run["status"], run["pages_done"], run["failed_pages"]) == ("partial", 2, 1)
    assert library.get_page(doc["id"], 1)["status"] == "failed"
    assert "simulated extraction failure" in run["error"]
    assert library.search("surviving")[0]["page"] == 2


def test_document_run_mismatch_is_rejected(library: Library):
    first = library.import_document(pdf_bytes(["First"]), "a.pdf")["document"]
    second = library.import_document(pdf_bytes(["Second"]), "b.pdf")["document"]
    with pytest.raises(LibraryError, match="不属于"):
        library.get_page(first["id"], 1, second["current_run"]["id"])
    with pytest.raises(LibraryError, match="不属于"):
        library.search("text", document_id=first["id"], run_id=second["current_run"]["id"])
    with pytest.raises(KeyError):
        library.get_document("unknown")


def test_worker_os_lock_and_cli(library: Library):
    doc = library.import_document(pdf_bytes(["Worker process evidence"]), "worker.pdf")["document"]
    command = [sys.executable, "-m", "research_workspace.worker", "--data-dir", str(library.data_dir), "--once"]
    with WorkerLock(library.data_dir):
        blocked = subprocess.run(command, capture_output=True, timeout=20)
        assert blocked.returncode == 2
        assert library.get_document(doc["id"])["current_run"]["status"] == "queued"
    completed = subprocess.run(command, capture_output=True, timeout=20)
    assert completed.returncode == 0, completed.stderr.decode(errors="replace")
    assert library.get_document(doc["id"])["current_run"]["status"] == "completed"


def test_metadata_and_reversible_archive_preserve_evidence(library: Library):
    content = pdf_bytes(["Preserved archive evidence"])
    doc = library.import_document(content, "archive.pdf")["document"]
    library.process_next_job()
    old_page = library.get_page(doc["id"], 1)
    updated = library.update_metadata(doc["id"], title="A verified title", authors=["Alice", "张三"])
    assert updated["title"] == "A verified title"
    assert updated["authors"] == ["Alice", "张三"]
    assert library.archive_document(doc["id"])["archived_at"]
    assert library.list_documents() == []
    assert len(library.list_documents(include_archived=True)) == 1
    assert library.search("Preserved") == []
    assert library.search("Preserved", document_id=doc["id"])
    assert library.get_page(doc["id"], 1) == old_page
    assert library._source_path(doc["id"]).read_bytes() == content
    assert library.restore_document(doc["id"])["archived_at"] is None
    assert len(library.search("Preserved")) == 1
    with pytest.raises(LibraryError):
        library.update_metadata(doc["id"], title=" ")


def test_arxiv_candidates_require_confirmation_and_current_choice_is_reversible(library: Library):
    first = library.import_document(pdf_bytes(["Shared experiment result 1"]), "1706.03762v1.pdf")["document"]
    newer = library.import_document(pdf_bytes(["Shared experiment result 7"]), "1706.03762v7.pdf")["document"]
    other = library.import_document(pdf_bytes(["Shared subject but an unrelated paper"]), "9999.12345v1.pdf")["document"]
    while library.process_next_job():
        pass
    candidates = library.find_version_candidates(first["id"])
    assert [candidate["document"]["id"] for candidate in candidates] == [newer["id"]]
    assert candidates[0]["requires_confirmation"]
    assert candidates[0]["arxiv"]["version"] == 7
    assert library.get_document(first["id"])["version_family_id"] is None
    assert len(library.search("Shared")) == 3
    relation = library.confirm_version_relation(first["id"], newer["id"], action="keep_both")
    assert relation["action"] == "keep_both"
    assert library.get_document(first["id"])["is_current_version"]
    assert library.get_document(newer["id"])["is_current_version"]
    assert len(library.search("Shared")) == 3
    library.confirm_version_relation(first["id"], newer["id"], action="use_other")
    assert {hit["document_id"] for hit in library.search("Shared")} == {newer["id"], other["id"]}
    assert library.get_version_family(first["id"])["current_document_id"] == newer["id"]
    assert library.search("result 1", document_id=first["id"])
    library.confirm_version_relation(first["id"], newer["id"], action="use_document")
    assert library.get_document(first["id"])["is_current_version"]
    assert len(library.get_version_family(first["id"])["relations"]) == 3
    assert len(list(library.files_dir.glob("*.pdf"))) == 3


def test_content_fingerprint_can_find_renamed_versions_without_merging(library: Library):
    lines = [f"Detailed method {i} measures gradient evidence under fixed conditions." for i in range(12)]
    first = library.import_document(pdf_bytes(["\n".join(lines)]), "renamed-one.pdf")["document"]
    second = library.import_document(pdf_bytes(["\n".join(lines) + "\nAdditional measured result."]), "renamed-two.pdf")["document"]
    unrelated = library.import_document(pdf_bytes(["Another gradient method uses a different training setup."]), "gradient-topic.pdf")["document"]
    while library.process_next_job():
        pass
    found = library.find_version_candidates(first["id"])
    assert [item["document"]["id"] for item in found] == [second["id"]]
    assert found[0]["reason"] == "high_body_overlap"
    assert found[0]["body_similarity"] > 0.8
    assert library.get_document(unrelated["id"])["version_family_id"] is None


def test_version_diff_has_both_original_locations(library: Library):
    first = library.import_document(pdf_bytes(["Result value is 10."]), "a.pdf")["document"]
    second = library.import_document(pdf_bytes(["Result value is 20."]), "b.pdf")["document"]
    while library.process_next_job():
        pass
    diff = library.compare_versions(first["id"], second["id"])
    change = diff["changes"][0]
    assert "10" in change["before"]["text"]
    assert "20" in change["after"]["text"]
    for side in ("before", "after"):
        evidence = change[side]
        page = library.get_page(evidence["document_id"], evidence["page"], evidence["run_id"])
        assert page["blocks"][0]["bbox"] == evidence["bbox"]


def test_cancel_queued_and_running_jobs_retains_saved_pages(library: Library, monkeypatch):
    doc = library.import_document(pdf_bytes(["First page", "Second page"]), "cancel.pdf")["document"]
    queued = library.cancel_run(doc["current_run"]["id"])
    assert queued["status"] == "cancelled"
    assert not library.process_next_job()
    run = library.create_run(doc["id"])
    original_save = library._save_page

    def save_then_cancel(*args, **kwargs):
        original_save(*args, **kwargs)
        library.cancel_run(run["id"])

    monkeypatch.setattr(library, "_save_page", save_then_cancel)
    library.process_next_job()
    cancelled = library.get_run(run["id"])
    assert cancelled["status"] == "cancelled"
    assert cancelled["pages_done"] == 1
    assert library.get_page(doc["id"], 1)["text"] == "First page"
    assert library.get_page(doc["id"], 2)["status"] == "pending"
    assert library.search("First page")
    assert library.recover_interrupted_runs() == 0


def test_single_page_retry_only_extracts_target_and_preserves_old_run(library: Library, monkeypatch):
    doc = library.import_document(pdf_bytes(["Page one", "Page two", "Page three"]), "retry.pdf")["document"]
    original_extract = library._page_blocks

    def fail_second(source_page):
        if source_page.number == 1:
            raise ValueError("temporary second-page failure")
        return original_extract(source_page)

    monkeypatch.setattr(library, "_page_blocks", fail_second)
    library.process_next_job()
    source_run = library.get_document(doc["id"])["current_run"]
    old_pages = [library.get_page(doc["id"], page, source_run["id"]) for page in (1, 2, 3)]
    seen = []

    def record_target(source_page):
        seen.append(source_page.number + 1)
        return original_extract(source_page)

    monkeypatch.setattr(library, "_page_blocks", record_target)
    retry = library.retry_page(doc["id"], 2, source_run["id"])
    assert retry["parent_run_id"] == source_run["id"]
    assert retry["target_pages"] == [2]
    assert library.get_page(doc["id"], 1, retry["id"])["text"] == "Page one"
    library.process_next_job()
    assert seen == [2]
    assert library.get_run(retry["id"])["status"] == "completed"
    assert library.search("Page two")[0]["run_id"] == retry["id"]
    assert [library.get_page(doc["id"], page, source_run["id"]) for page in (1, 2, 3)] == old_pages


def test_structure_result_uses_actual_pdf_blocks(library: Library):
    content = pdf_bytes(["Abstract\nAn evidence based method.\n1 Introduction\nWe build on [1].",
                         "References\n[1] Alice. Prior method. 2020."])
    doc = library.import_document(content, "structure.pdf")["document"]
    library.process_next_job()
    result = library.get_structure(doc["id"])
    assert result["references"][0]["citation_ids"]
    assert result["headings"][1]["title"] == "1 Introduction"
    evidence = result["citations"][0]["evidence"]
    source = library.get_page(doc["id"], evidence["page"], evidence["run_id"])
    assert any(block["id"] == evidence["block_id"] for block in source["blocks"])
    assert result["processing_status"] == "completed"


def test_missing_ocr_configuration_is_explicit_and_does_not_expose_paths(library: Library, monkeypatch, tmp_path):
    private_path = str(tmp_path / "private-ocr-models")
    monkeypatch.setenv("RESEARCH_OCR_ENABLED", "1")
    monkeypatch.setenv("RESEARCH_OCR_TESSDATA", private_path)
    monkeypatch.setenv("RESEARCH_OCR_LANG", "eng")
    status = library.ocr_status()
    assert status["configured"]
    assert not status["available"]
    assert private_path not in str(status)
    assert "path" not in status
    doc = library.import_document(pdf_bytes([None]), "scan.pdf")["document"]
    library.process_next_job()
    page = library.get_page(doc["id"], 1)
    assert page["status"] == "no_extractable_text"
    assert "OCR" in page["notice"]
    assert page["extraction_method"] == "native"
    assert not page["blocks"]
