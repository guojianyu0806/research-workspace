"""Offline integration of model tool turns, durable jobs and the original citation gates."""
import asyncio
from datetime import datetime, timedelta, timezone
import json

import httpx
import pymupdf
import pytest
from fastapi.testclient import TestClient

from research_workspace.api import create_app
from research_workspace.discovery import DiscoveryService
from research_workspace.intelligence import ChatCompletionProvider, IntelligenceService, ModelConfig
from research_workspace.jobs import JobStore


def pdf_bytes(title, text, reference=None):
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.insert_text((40, 60), title)
        page.insert_text((40, 100), text)
        if reference:
            page = pdf.new_page()
            page.insert_text((40, 60), "References")
            page.insert_text((40, 100), f"[1] Example Author. {reference}. 2024.")
        pdf.set_metadata({"title": title})
        return pdf.tobytes()


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCH_MODEL_BASE_URL", "https://model.invalid")
    monkeypatch.setenv("RESEARCH_MODEL", "offline-fixture")
    monkeypatch.setenv("RESEARCH_MODEL_API_KEY", "fixture-only-not-a-real-key")
    return create_app(tmp_path, start_worker=False)


def request_job(app, **body):
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        response = client.post("/api/research/tasks", json={"kind": "agent_research", **body})
    assert response.status_code == 202, response.text
    return response.json()["job"]


def tool_turn(identifier, name, arguments):
    arguments = arguments if isinstance(arguments, str) else json.dumps(arguments)
    return httpx.Response(200, json={"choices": [{"finish_reason": "tool_calls", "message": {
        "role": "assistant", "content": None, "tool_calls": [{"id": identifier, "type": "function",
        "function": {"name": name, "arguments": arguments}}],
    }}]})


def stop_turn(content="Planning complete."):
    return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
        "role": "assistant", "content": content,
    }}]})


def completion(payload, *, corrupt=False):
    task = payload["task"]
    if task == "verify_response_coverage":
        answer = {"unverified_statements": [], "missing_aspects": []}
    elif task == "verify_single_claim":
        answer = {"verdict": "supported", "reason": "Original sentence supports the claim."}
    else:
        assert task in {"qa", "compare"}, task
        claims = {}
        for block in payload["evidence"]:
            for span in block["citation_spans"]:
                if "encoder has" in span["text"]:
                    claims.setdefault(block["document_id"], {"text": span["text"], "citations": [{
                        "evidence_id": block["evidence_id"], "quote_id": "Q999" if corrupt else span["quote_id"],
                        "quote": "",
                    }]})
        assert claims
        answer = {"status": "answered", "claims": list(claims.values())}
    return stop_turn(json.dumps(answer))


def run_job(library, store, handler):
    provider = ChatCompletionProvider(
        ModelConfig("https://model.invalid", "offline-fixture", "fixture-not-a-real-key", max_attempts=1),
        transport=httpx.MockTransport(handler),
    )
    assert asyncio.run(store.process_next(IntelligenceService(library, provider)))


def model_input(request):
    assert request.url.host == "model.invalid"
    return json.loads(request.content)


@pytest.mark.parametrize("corrupt", [False, True])
def test_local_compare_handles_tool_error_and_keeps_original_quote_gate(app, corrupt):
    library = app.state.library
    title = "Beta Evidence Retrieval Methods"
    beta = library.import_document(pdf_bytes(title, "The Beta encoder has 12 layers."), "beta.pdf")["document"]
    alpha = library.import_document(pdf_bytes("Alpha Evidence Retrieval Methods",
        "The Alpha encoder has 6 layers.", title), "alpha.pdf")["document"]
    while library.process_next_job():
        pass
    ids = [alpha["id"], beta["id"]]
    job = request_job(app, question="How many encoder layers do Alpha and Beta have?",
                      document_ids=ids, agent_mode="compare")
    pinned = {item["document_id"]: item["run_id"] for item in job["payload"]["agent_scope"]["documents"]}
    library.create_run(alpha["id"])
    while library.process_next_job():
        pass
    assert library.get_document(alpha["id"])["available_run"]["id"] != pinned[alpha["id"]]

    plans = []

    def model(request):
        body = model_input(request)
        if "tools" not in body:
            payload = json.loads(body["messages"][1]["content"])
            assert all(block["run_id"] == pinned[block["document_id"]] for block in payload.get("evidence", []))
            return completion(payload, corrupt=corrupt)
        plans.append(body)
        assert {entry["function"]["name"] for entry in body["tools"]} == {"search_evidence", "build_citation_graph"}
        if len(plans) == 1:
            return tool_turn("graph", "build_citation_graph", {"document_ids": ids})
        message = body["messages"][-1]
        assert message["role"] == "tool"
        outcome = json.loads(message["content"])
        args = {"query": "encoder layers", "document_ids": ids}
        if len(plans) == 2:
            assert message["tool_call_id"] == "graph" and outcome["graph"]["method"]["damping"] == 0.85
            assert outcome["graph"]["edges"]
            return tool_turn("invalid", "search_evidence", '{"query":"wrong",' + json.dumps(args)[1:])
        if len(plans) == 3:
            assert message["tool_call_id"] == "invalid" and outcome["code"] == "invalid_arguments"
            return tool_turn("repaired", "search_evidence", args)
        assert message["tool_call_id"] == "repaired" and outcome["code"] == "evidence_found"
        return stop_turn()

    store = JobStore(library)
    run_job(library, store, model)
    saved = store.get(job["id"])
    assert len(plans) == 4, saved["error"]
    if corrupt:
        assert saved["status"] == "failed" and saved["result"] is None
        audit = saved["completed_items"][0]
        assert audit["stage"] == "grounding_validation" and audit["result"]["diagnostic_only"]
        assert "原文片段编号" in json.dumps(audit, ensure_ascii=False)
        return
    assert saved["completed_items"] == []
    result = saved["result"]
    assert saved["status"] == "completed" and result["status"] == "answered"
    assert [item["code"] for item in result["agent_trace"]] == ["graph_built", "invalid_arguments", "evidence_found"]
    assert {item["run_id"] for item in result["sources"]} == set(pinned.values())
    assert all(citation["quote"] and citation["quote_id"] == "Q1"
               for claim in result["claims"] for citation in claim["citations"])
    assert result["grounding"]["mode"] == "isolated_claim_citations"


@pytest.mark.parametrize("lost_checkpoint", [False, True])
def test_public_import_resumes_same_tool_id_without_repeating_network(app, monkeypatch, lost_checkpoint):
    library = app.state.library
    store = JobStore(library)
    paper_id = "arxiv:2401.12345v1"
    published = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    feed = f"""<feed xmlns="http://www.w3.org/2005/Atom">
    <entry><id>http://arxiv.org/abs/2401.12345v1</id><title>Fixture encoder</title>
    <summary>Metadata without an encoder layer count.</summary><author><name>Fixture Author</name></author>
    <published>{published}</published><updated>{published}</updated>
    <link title="pdf" href="https://arxiv.org/pdf/2401.12345v1"/></entry></feed>""".encode()
    download = pdf_bytes("Fixture encoder", "The fixture encoder has 7 layers.")
    network = []

    def request(self, url, **kwargs):
        network.append(url)
        return (download if "/pdf/" in url else feed), url

    monkeypatch.setattr(DiscoveryService, "_request", request)
    job = request_job(app, question="How many encoder layers does the public paper describe?",
                      agent_public_query="encoder", agent_import_limit=1)
    calls = []

    def model(request):
        body = model_input(request)
        if "tools" not in body:
            return completion(json.loads(body["messages"][1]["content"]))
        calls.append(body)
        if len(calls) == 1:
            return tool_turn("public", "search_arxiv", {"query": "encoder"})
        outcome = json.loads(body["messages"][-1]["content"])
        if len(calls) == 2:
            assert outcome["papers"][0]["id"] == paper_id
            return tool_turn("pdf", "import_paper", {"paper_id": paper_id})
        if len(calls) == 3:
            assert outcome["code"] == "paper_imported"
            return tool_turn("parse", "prepare_parse", {"document_id": outcome["document_id"]})
        if len(calls) == 4:
            assert body["messages"][-1]["tool_call_id"] == "parse" and outcome["code"] == "parse_ready"
            return tool_turn("evidence", "search_evidence", {"query": "encoder layers", "document_ids": [outcome["document_id"]]})
        assert outcome["code"] == "evidence_found"
        return stop_turn()

    if lost_checkpoint:
        save = store.save_agent_state
        interrupted = False

        def checkpoint(job_id, state, **kwargs):
            nonlocal interrupted
            if not interrupted and state["trace"] and state["trace"][-1]["code"] == "paper_imported":
                interrupted = True
                raise RuntimeError("Fixture process interruption after the import")
            return save(job_id, state, **kwargs)

        monkeypatch.setattr(store, "save_agent_state", checkpoint)
        run_job(library, store, model)
        assert interrupted and store.get(job["id"])["status"] == "failed"
        store.retry(job["id"])

    run_job(library, store, model)
    assert len(calls) == 3 and len(network) == 2, store.get(job["id"])["error"]
    assert store.get(job["id"])["status"] == "queued" and store.claim() is None
    assert library.process_next_job()
    # A resumed worker first checks the run. A completed parse remains usable
    # even if the task spent longer than the polling deadline paused.
    checkpoint = store.load_agent_state(job["id"])
    checkpoint["current"]["started_at"] -= 180
    store.save_agent_state(job["id"], checkpoint)
    run_job(library, store, model)
    result = store.get(job["id"])["result"]
    assert result["status"] == "answered" and len(network) == 2
    assert len(library.list_documents()) == 1
    assert [item["code"] for item in result["agent_trace"]] == [
        "public_papers_found", "paper_imported", "parse_pending", "parse_ready", "evidence_found"]
    assert result["claims"][0]["citations"][0]["quote"] == "The fixture encoder has 7 layers."


def test_planner_prose_without_tools_cannot_be_published(app):
    library = app.state.library
    job = request_job(app, question="Describe new results.", agent_public_query="encoder")
    calls = []

    def model(request):
        calls.append(model_input(request))
        return stop_turn("The planner's unverified factual claim.")

    store = JobStore(library)
    run_job(library, store, model)
    saved = store.get(job["id"])
    assert saved["status"] == "completed" and len(calls) == 1
    assert saved["result"]["status"] == "insufficient_evidence" and saved["result"]["claims"] == []
    assert "unverified factual claim" not in json.dumps(saved)
