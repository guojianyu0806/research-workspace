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
    phases = [event["stage"] for event in saved["events"] if event["stage"].startswith("agent_answer_")]
    if corrupt:
        assert saved["status"] == "failed" and saved["result"] is None
        assert phases == ["agent_answer_generate", "agent_answer_review", "agent_answer_repair", "agent_answer_review"]
        audit = saved["completed_items"][0]
        assert audit["stage"] == "grounding_validation" and audit["result"]["diagnostic_only"]
        assert "原文片段编号" in json.dumps(audit, ensure_ascii=False)
        return
    assert saved["completed_items"] == []
    result = saved["result"]
    assert saved["status"] == "completed" and result["status"] == "answered"
    assert phases == ["agent_answer_generate", "agent_answer_review", "agent_answer_coverage"]
    assert [item["code"] for item in result["agent_trace"]] == ["graph_built", "invalid_arguments", "evidence_found"]
    assert {item["run_id"] for item in result["sources"]} == set(pinned.values())
    assert all(citation["quote"] and citation["quote_id"] == "Q1"
               for claim in result["claims"] for citation in claim["citations"])
    assert result["grounding"]["mode"] == "isolated_claim_citations"


@pytest.mark.parametrize("first_error", ["duplicate", "mixed_sources", "repeated_duplicate"])
def test_compare_enforces_requested_one_claim_per_paper(app, first_error):
    library = app.state.library
    statements = {
        "Transformer": "The Transformer model architecture uses an encoder and a decoder.",
        "BERT": "The BERT model architecture uses a bidirectional Transformer encoder.",
    }
    documents = {title: library.import_document(pdf_bytes(title, statement), title + ".pdf")["document"]["id"]
                 for title, statement in statements.items()}
    while library.process_next_job():
        pass
    ids = list(documents.values())
    job = request_job(app, document_ids=ids, agent_mode="compare",
        question="正式回答只写两条事实，每篇各用一句中文改述。第一条说明 Transformer 的总体模型架构，"
                 "第二条说明 BERT 的总体模型架构。每条引用对应论文的原文。不要讨论层数、训练目标或实验结果。")
    generated = []
    checked = []
    store = JobStore(library)

    def model(request):
        body = model_input(request)
        if "tools" in body:
            if body["messages"][-1]["role"] == "tool":
                return stop_turn()
            return tool_turn("retrieve", "search_evidence", {"query": "model architecture encoder", "document_ids": ids})
        payload = json.loads(body["messages"][1]["content"])
        summary = store.summary(job["id"], include_stage_event=True)
        assert summary["last_event"]["stage"] == "model_call"
        phase = summary["last_stage_event"]["stage"]
        if payload["task"] != "compare":
            expected_phase = {"verify_single_claim": "agent_answer_review", "verify_response_coverage": "agent_answer_coverage"}
            assert phase == expected_phase[payload["task"]]
            checked.append(payload["task"])
            return completion(payload)
        assert phase == ("agent_answer_repair" if generated else "agent_answer_generate")
        assert payload["coverage_contract"]["claims_per_document"] == 1
        generated.append(payload)
        citations = {block["document_id"]: {"evidence_id": block["evidence_id"], "quote_id": span["quote_id"], "quote": ""}
                     for block in payload["evidence"] for span in block["citation_spans"]
                     if span["text"] in statements.values()}
        claims = [{"text": "Transformer 采用编码器和解码器。", "citations": [citations[ids[0]]]},
                  {"text": "BERT 采用双向 Transformer 编码器。", "citations": [citations[ids[1]]]}]
        if len(generated) == 1 or first_error == "repeated_duplicate":
            if first_error == "mixed_sources":
                claims[0]["citations"].append(citations[ids[1]])
            else:
                claims.insert(1, claims[0])
        else:
            assert "恰好对应一条结论" in payload["correction"]["problems"][0]
        return stop_turn(json.dumps({"status": "answered", "claims": claims}))

    run_job(library, store, model)
    saved = store.get(job["id"])
    assert len(generated) == 2, saved["error"]
    if first_error == "repeated_duplicate":
        assert saved["status"] == "failed" and saved["result"] is None
        assert "恰好对应一条结论" in saved["error"]
        assert checked == []
        return
    assert saved["status"] == "completed", saved["error"]
    assert checked == ["verify_single_claim", "verify_single_claim", "verify_response_coverage"]
    result = saved["result"]
    assert len(result["claims"]) == 2
    grounding = result["grounding"]
    assert grounding["attempts"][0]["reviews"] == []
    assert [claim["citations"][0]["document_id"] for claim in grounding["claim_catalog"]] == ids
    assert [review["claim_id"] for review in grounding["claim_reviews"]] == [
        claim["claim_id"] for claim in grounding["claim_catalog"]]


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
    waiting = store.summary(job["id"])
    assert waiting["status"] == "queued" and waiting["last_event"]["stage"] == "agent_waiting_parse"
    assert store.claim() is None
    # Make the queued check eligible before the parser has finished.
    checkpoint = store.load_agent_state(job["id"])
    store.save_agent_state(job["id"], checkpoint)
    run_job(library, store, model)
    still_waiting = store.header(job["id"])
    assert still_waiting["status"] == "queued" and len(calls) == 3
    assert [event["stage"] for event in still_waiting["events"]].count("agent_waiting_parse") == 1
    assert not any(event["stage"] == "agent_resume_parse" for event in still_waiting["events"])
    assert library.process_next_job()
    # A resumed worker first checks the run. A completed parse remains usable
    # even if the task spent longer than the polling deadline paused.
    checkpoint = store.load_agent_state(job["id"])
    checkpoint["current"]["started_at"] -= 180
    store.save_agent_state(job["id"], checkpoint)
    run_job(library, store, model)
    saved = store.get(job["id"])
    phases = [event["stage"] for event in saved["events"]]
    assert phases.count("agent_waiting_parse") == phases.count("agent_resume_parse") == 1
    assert phases.index("agent_waiting_parse") < phases.index("agent_resume_parse") < phases.index("agent_answer_generate")
    result = saved["result"]
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
    assert not any(event["stage"].startswith("agent_answer_") for event in saved["events"])
    assert "unverified factual claim" not in json.dumps(saved)


def test_agent_sse_emits_stage_and_terminal_changes_with_same_update_timestamp(app, monkeypatch):
    job = request_job(app, question="Describe new results.", agent_public_query="encoder")
    store = JobStore(app.state.library)
    store.claim()
    store.event(job["id"], "agent_answer_review", "正在核验原文引句与论断支持度。")
    store.event(job["id"], "model_call", "正在调用模型：ClaimSupportPayload（本轮第1次请求）。")
    summary = JobStore.summary
    reads = 0

    def frozen_update_time(self, job_id, **options):
        nonlocal reads
        reads += 1
        if reads == 3:
            self.event(job_id, "agent_answer_coverage", "正在核验回答是否覆盖研究问题。")
            self.event(job_id, "model_call", "正在调用模型：CoveragePayload（本轮第1次请求）。")
        item = summary(self, job_id, **options)
        item["updated_at"] = job["updated_at"]
        if reads == 4:
            # A terminal status can be observed before its final journal entry.
            item["status"] = "completed"
        return item

    monkeypatch.setattr(JobStore, "summary", frozen_update_time)
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        response = client.get(f'/api/research/tasks/{job["id"]}/events')
    assert response.status_code == 200
    items = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
    assert [item["status"] for item in items] == ["running", "running", "completed"]
    assert [item["last_stage_event"]["stage"] for item in items] == [
        "agent_answer_review", "agent_answer_coverage", "agent_answer_coverage"]
