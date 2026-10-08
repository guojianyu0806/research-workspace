"""API tests exercise data boundaries, upload validation and real evidence."""

import pymupdf
import pytest
from fastapi.testclient import TestClient

from research_workspace.api import create_app


def pdf_bytes(text="An evidence sentence about transformers."):
    with pymupdf.open() as doc:
        doc.new_page().insert_text((50, 80), text)
        return doc.tobytes()


@pytest.fixture
def service(tmp_path):
    app = create_app(tmp_path, start_worker=False, max_upload_mb=1)
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        yield client, app.state.library


def test_real_upload_parse_search_and_page(service):
    client, library = service
    uploaded = client.post("/api/documents", files={"file": ("paper.pdf", pdf_bytes(), "application/pdf")})
    assert uploaded.status_code == 202, uploaded.text
    doc = uploaded.json()["document"]
    assert library.process_next_job()
    ready = client.get(f"/api/documents/{doc['id']}").json()["document"]
    assert ready["current_run"]["status"] == "completed"
    hits = client.get("/api/search", params={"q": "EVIDENCE", "document_id": doc["id"]}).json()["hits"]
    assert len(hits) == 1
    assert "evidence sentence" in hits[0]["text"]
    assert hits[0]["page"] == 1
    page = client.get(f"/api/documents/{doc['id']}/pages/1").json()
    assert page["run_id"] == hits[0]["run_id"]
    assert page["blocks"][0]["bbox"] == hits[0]["bbox"]
    image = client.get(f"/api/documents/{doc['id']}/pages/1/image")
    assert image.status_code == 200 and image.content.startswith(b"\x89PNG")


def test_bad_and_empty_uploads_do_not_enter_library(service):
    client, _ = service
    for content in (b"", b"not really a PDF", b"%PDF-1.7 broken"):
        response = client.post("/api/documents", files={"file": ("wrong.pdf", content, "application/pdf")})
        assert response.status_code == 400, response.text
    assert client.get("/api/documents").json()["documents"] == []


def test_upload_limit(service):
    client, _ = service
    response = client.post("/api/documents", files={"file": ("huge.pdf", b"x" * (1024 * 1024 + 1))})
    assert response.status_code == 413
    assert client.get("/api/documents").json()["documents"] == []


@pytest.mark.parametrize("declared_length", [None, "1"])
def test_stream_upload_limit_is_reported_correctly(service, declared_length):
    client, _ = service
    boundary = "fixture-boundary"
    opening = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
               'filename="large.pdf"\r\nContent-Type: application/pdf\r\n\r\n').encode()
    chunks = [opening, b"x" * (2400 * 1024), f"\r\n--{boundary}--\r\n".encode()]
    headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
    if declared_length is not None:
        headers["Content-Length"] = declared_length
    response = client.post("/api/documents", content=iter(chunks), headers=headers)
    assert response.status_code == 413, response.text
    assert "超过" in response.json()["detail"]
    assert client.get("/api/documents").json()["documents"] == []


def test_no_text_document_is_not_claimed_parsed(service):
    client, library = service
    with pymupdf.open() as doc:
        doc.new_page()
        data = doc.tobytes()
    uploaded = client.post("/api/documents", files={"file": ("image.pdf", data)}).json()["document"]
    library.process_next_job()
    doc = client.get(f"/api/documents/{uploaded['id']}").json()["document"]
    assert doc["current_run"]["status"] == "needs_ocr"
    assert doc["current_run"]["text_pages"] == 0
    assert client.get("/api/search", params={"q": "unavailable"}).json()["hits"] == []


def test_local_origin_and_host_boundaries(service):
    client, _ = service
    assert client.get("/api/documents", headers={"host": "evil.example"}).status_code == 400
    for method, path in (("GET", "/api/documents"), ("POST", "/api/documents")):
        assert client.request(method, path, headers={"origin": "https://evil.example"}).status_code == 403
    assert client.get("/api/health", headers={"origin": "http://127.0.0.1:8765"}).status_code == 200
    assert client.get("/api/health", headers={"sec-fetch-site": "cross-site"}).status_code == 403
    assert client.get("/api/health", headers={"origin": "http://["}).status_code == 403
    assert client.get("/api/capabilities").json()["llm"] is False


def test_queries_and_missing_resource(service):
    client, _ = service
    assert client.get("/api/search", params={"q": "   "}).status_code == 400
    assert client.get("/api/documents/missing").status_code == 404
    assert client.get("/api/search", params={"q": "a" * 201}).status_code == 422


def test_cross_document_run_rejected_and_old_run_retained(service):
    client, library = service
    first = client.post("/api/documents", files={"file": ("one.pdf", pdf_bytes("alpha evidence"))}).json()["document"]
    library.process_next_job()
    old = client.get(f"/api/documents/{first['id']}").json()["document"]["current_run"]["id"]
    second = client.post("/api/documents", files={"file": ("two.pdf", pdf_bytes("beta evidence"))}).json()["document"]
    library.process_next_job()
    assert client.get(f"/api/documents/{second['id']}/pages/1", params={"run_id": old}).status_code in {400, 404}
    new = client.post(f"/api/documents/{first['id']}/parse-runs").json()["run"]["id"]
    assert new != old
    library.process_next_job()
    page = client.get(f"/api/documents/{first['id']}/pages/1", params={"run_id": old}).json()
    assert page["run_id"] == old and "alpha evidence" in page["text"]


def test_duplicate_and_untrusted_filename(service):
    client, library = service
    data = pdf_bytes()
    first = client.post("/api/documents", files={"file": ("../../outside.pdf", data)}).json()
    duplicate = client.post("/api/documents", files={"file": ("renamed.pdf", data)})
    assert duplicate.status_code == 200
    assert duplicate.json()["duplicate"] is True
    assert duplicate.json()["document"]["id"] == first["document"]["id"]
    assert len(library.list_documents()) == 1
