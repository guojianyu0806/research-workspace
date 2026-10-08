"""Offline HTTP contract and persistence tests; fixtures are not production data."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pymupdf
import pytest

from research_workspace.discovery import DiscoveryError, DiscoveryService, arxiv_identifier
from research_workspace.library import Library


def feed(entries: str) -> bytes:
    return ('''<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">'''
            + entries + '</feed>').encode()


def entry(identifier="2401.12345v1", title="Evidence Retrieval Methods", summary="Research on evidence.", pdf_host="arxiv.org"):
    return f'''<entry><id>http://arxiv.org/abs/{identifier}</id><title>{title}</title>
    <summary>{summary}</summary><author><name>Fixture Author</name></author>
    <published>2024-01-01T00:00:00Z</published><updated>2024-01-02T00:00:00Z</updated>
    <link title="pdf" href="https://{pdf_host}/pdf/{identifier}"/>
    <category term="cs.IR"/></entry>'''


def pdf_bytes(title="Evidence Retrieval Methods"):
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.insert_text((30, 70), title)
        pdf.set_metadata({"title": title})
        return pdf.tobytes()


def service(tmp_path: Path, handler):
    library = Library(tmp_path / "workspace")
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return DiscoveryService(library, client, request_interval=0)


def test_constructor_and_local_recommendations_never_access_network(tmp_path):
    calls = []
    discovery = service(tmp_path, lambda request: calls.append(request))
    assert discovery.list_subscriptions() == []
    assert discovery.list_papers() == []
    assert discovery.recommendations()["network_used"] is False
    assert discovery.create_subscription("public quantum research")["status"] == "idle"
    assert calls == []


def test_real_atom_contract_metadata_cache_and_unverified_code(tmp_path):
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(200, content=feed(entry(summary="Code: https://github.com/example/research and github references.")))
    discovery = service(tmp_path, respond)
    result = discovery.search("Evidence Retrieval", field="title")
    paper = result["items"][0]
    assert calls[0].url.host == "export.arxiv.org"
    assert calls[0].url.params["search_query"] == 'ti:"Evidence" AND ti:"Retrieval"'
    assert paper["arxiv_id"] == "2401.12345v1"
    assert paper["version"] == 1
    assert paper["source_url"] == "https://arxiv.org/abs/2401.12345v1"
    assert paper["fulltext_status"] == "metadata_only"
    assert paper["fulltext_searchable"] is False
    assert paper["verified_code"] is False
    assert paper["code_links"][0]["verification"] == "unverified_metadata_link"
    assert discovery.search("Evidence Retrieval", field="title")["cached"]
    assert len(calls) == 1
    reopened = DiscoveryService(discovery.library, discovery.client, request_interval=0)
    assert reopened.search("Evidence Retrieval", field="title")["cached"]
    assert len(calls) == 1


def test_only_verified_official_pdf_is_downloaded_and_imported(tmp_path):
    content = pdf_bytes()
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(200, content=content if "/pdf/" in request.url.path else feed(entry()))
    discovery = service(tmp_path, respond)
    paper = discovery.search("2401.12345v1")["items"][0]
    assert calls[0].url.params["id_list"] == "2401.12345v1"
    imported = discovery.import_paper(paper["id"])
    assert not imported["duplicate"]
    assert imported["document"]["current_run"]["status"] == "queued"
    assert imported["source"]["metadata_url"] == paper["source_url"]
    assert discovery.get_paper(paper["id"])["fulltext_status"] == "downloaded"
    assert not discovery.get_paper(paper["id"])["fulltext_searchable"]
    discovery.library.process_next_job()
    assert discovery.get_paper(paper["id"])["fulltext_searchable"]
    assert discovery.import_paper(paper["id"])["duplicate"]
    assert len(calls) == 2
    with pytest.raises(KeyError):
        discovery.import_paper("https://127.0.0.1/private.pdf")


@pytest.mark.parametrize("redirect", ["https://127.0.0.1/private.pdf", "http://arxiv.org/pdf/2401.12345v1", "https://arxiv.org/pdf/2401.99999v1", "https://arxiv.org.evil.test/pdf/2401.12345v1"])
def test_redirect_validation_prevents_ssrf_and_identity_swap(tmp_path, redirect):
    def respond(request):
        if "/pdf/" in request.url.path:
            return httpx.Response(302, headers={"location": redirect})
        return httpx.Response(200, content=feed(entry()))
    discovery = service(tmp_path, respond)
    paper = discovery.search("public title")["items"][0]
    with pytest.raises(DiscoveryError, match="重定向"):
        discovery.import_paper(paper["id"])
    assert discovery.library.list_documents() == []


def test_untrusted_pdf_link_is_metadata_only(tmp_path):
    discovery = service(tmp_path, lambda request: httpx.Response(200, content=feed(entry(pdf_host="evil.test"))))
    paper = discovery.search("public title")["items"][0]
    assert paper["pdf_url"] is None
    with pytest.raises(DiscoveryError, match="只有元数据"):
        discovery.import_paper(paper["id"])


@pytest.mark.parametrize("body", [b"<html>sign in</html>", b"not a PDF"])
def test_html_download_is_never_registered_as_pdf(tmp_path, body):
    discovery = service(tmp_path, lambda request: httpx.Response(200, content=body if "/pdf/" in request.url.path else feed(entry())))
    paper = discovery.search("public title")["items"][0]
    with pytest.raises(DiscoveryError, match="不是PDF"):
        discovery.import_paper(paper["id"])
    assert discovery.library.list_documents() == []


def test_download_size_bound(tmp_path):
    discovery = service(tmp_path, lambda request: httpx.Response(200, content=b"%PDF-" + b"x" * 100 if "/pdf/" in request.url.path else feed(entry())))
    paper = discovery.search("public title")["items"][0]
    discovery.max_pdf_bytes = 20
    with pytest.raises(DiscoveryError, match="大小"):
        discovery.import_paper(paper["id"])
    assert discovery.library.list_documents() == []


def test_subscription_due_persistence_dedup_pause_and_recovery(tmp_path):
    mode = {"failure": True}
    calls = []
    def respond(request):
        calls.append(request)
        if mode["failure"]:
            return httpx.Response(503)
        return httpx.Response(200, content=feed(entry()))
    discovery = service(tmp_path, respond)
    sub = discovery.create_subscription("evidence")
    assert discovery.create_subscription("evidence")["id"] == sub["id"]
    failure = discovery.due_checks()[0]
    assert failure["error"]
    assert failure["subscription"]["status"] == "error"
    assert failure["subscription"]["last_success_at"] is None
    assert discovery.due_checks() == []
    mode["failure"] = False
    success = discovery.check_subscription(sub["id"])
    assert len(success["new_items"]) == 1
    assert success["subscription"]["failure_count"] == 0
    assert success["subscription"]["last_success_at"]
    reopened = DiscoveryService(discovery.library, discovery.client, request_interval=0)
    repeated = reopened.check_subscription(sub["id"])
    assert repeated["new_items"] == []
    assert repeated["cached"]
    assert len(calls) == 2
    reopened.update_subscription(sub["id"], enabled=False)
    with reopened._db() as db:
        db.execute("UPDATE subscriptions SET next_check_at=?", ("2000-01-01T00:00:00+00:00",))
    assert reopened.due_checks() == []
    reopened.update_subscription(sub["id"], enabled=True)
    assert len(reopened.due_checks()) == 1


def test_interrupted_subscription_lease_is_recovered(tmp_path):
    discovery = service(tmp_path, lambda request: httpx.Response(200, content=feed(entry())))
    sub = discovery.create_subscription("research")
    stale = (datetime.now(timezone.utc) - timedelta(minutes=3)).isoformat()
    with discovery._db() as db:
        db.execute("UPDATE subscriptions SET status='checking',check_started_at=?,check_token='crashed'", (stale,))
    result = discovery.due_checks()[0]
    assert result["error"] is None
    assert result["subscription"]["status"] == "idle"
    with discovery._db() as db:
        db.execute("UPDATE subscriptions SET status='checking',check_started_at=?", (datetime.now(timezone.utc).isoformat(),))
    assert discovery.check_subscription(sub["id"])["error"] == "该订阅正在检查。"


def test_recommendation_feedback_and_workspace_isolation(tmp_path):
    content = feed(entry("2401.12345v1", "Evidence Retrieval Methods") + entry("2401.12346v1", "Quantum Research Review"))
    discovery = service(tmp_path, lambda request: httpx.Response(200, content=content))
    discovery.search("research")
    discovery.library.import_document(pdf_bytes("Evidence Retrieval"), "local.pdf")
    ranked = discovery.recommendations()["items"]
    assert ranked[0]["id"] == "arxiv:2401.12345v1"
    assert ranked[0]["related_documents"]
    before = ranked[0]["score"]
    discovery.feedback(ranked[0]["id"], -1)
    reopened = DiscoveryService(discovery.library, discovery.client, request_interval=0)
    after = next(item for item in reopened.recommendations()["items"] if item["id"] == ranked[0]["id"])
    assert after["score"] < before
    assert after["feedback"] == -1
    other = DiscoveryService(Library(tmp_path / "other"), discovery.client, request_interval=0)
    assert other.recommendations()["items"] == []
    with pytest.raises(KeyError):
        other.feedback(ranked[0]["id"], 1)


@pytest.mark.parametrize("payload", [b'<!DOCTYPE x [<!ENTITY e "large">]><feed/>', b"<html/>", b"<broken"])
def test_non_atom_or_entity_payload_rejected(tmp_path, payload):
    discovery = service(tmp_path, lambda request: httpx.Response(200, content=payload))
    with pytest.raises(DiscoveryError):
        discovery.search("public term")
    assert discovery.list_papers() == []


def test_identifier_validation():
    assert arxiv_identifier("https://arxiv.org/pdf/hep-th/9901001v2.pdf") == "hep-th/9901001v2"
    assert arxiv_identifier("https://evil.test/abs/2401.12345v1") is None
    assert arxiv_identifier("https://arxiv.org@evil.test/abs/2401.12345v1") is None
    assert arxiv_identifier("../../private") is None
