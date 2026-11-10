"""Agent adapter boundaries using generated PDFs and offline arXiv transports."""
import asyncio
import json

import httpx
import pymupdf
import pytest

from research_workspace.agent_tools import AgentTools, MAX_EVIDENCE, MAX_EVIDENCE_CHARS
from research_workspace.discovery import DiscoveryError, DiscoveryService
from research_workspace.graph import GraphService
from research_workspace.intelligence import IntelligenceService
from research_workspace.jobs import TaskCancelled
from research_workspace.library import Library, LibraryError


class NoModel:
    configured = False

    async def generate(self, *args, **kwargs):
        raise AssertionError("No model may be called by these tests")


class NoSemantic:
    async def rank(self, *args, **kwargs):
        return [], {"status":"disabled"}


def pdf(title="Reliable Evidence Retrieval", references=None):
    with pymupdf.open() as source:
        page = source.new_page()
        page.insert_text((35,60), title)
        page.insert_text((35,110), "The retrieval architecture uses original evidence for reliable answers.")
        if references:
            page = source.new_page()
            page.insert_text((35,60), "References")
            page.insert_text((35,100), references)
        source.set_metadata({"title":title})
        return source.tobytes()


def add(library, title="Reliable Evidence Retrieval", references=None):
    doc = library.import_document(pdf(title,references), title+".pdf")["document"]
    while library.process_next_job():
        pass
    return library.get_document(doc["id"])


def invoke(tools, name, arguments=None):
    return asyncio.run(tools.execute(name,json.dumps(arguments or {})))


@pytest.fixture
def setup(tmp_path):
    library = Library(tmp_path/"workspace")
    first = add(library)
    service = IntelligenceService(library, provider=NoModel(), semantic=NoSemantic())
    scope = {"document_ids":[first["id"]],"allow_arxiv":False,"import_limit":0,"subscription_id":None,
             "refresh_search":False,"research_days":90}
    state = {"run_ids":{first["id"]:first["available_run"]["id"]}}
    tools = AgentTools(service,scope=scope,state=state,check_cancel=None)
    return library, first, tools


def test_retrieval_distinguishes_budget_rejection_from_no_hits(setup, monkeypatch):
    library, first, tools = setup
    second = add(library, "Another Evidence Retrieval Paper")
    tools.scope["document_ids"].append(second["id"])
    tools.state["run_ids"][second["id"]] = second["available_run"]["id"]
    monkeypatch.setattr("research_workspace.agent_tools.MAX_EVIDENCE", 1)
    search = {"query": "reliable answers", "document_ids": [first["id"]], "limit": 1}
    assert invoke(tools, "search_evidence", search)["code"] == "evidence_found"
    search["document_ids"] = [second["id"]]
    rejected = invoke(tools, "search_evidence", search)
    assert rejected["code"] == "evidence_budget_reached" and rejected["omitted_due_to_budget"] == 1


def remote(tools, monkeypatch, handler=None):
    calls = []
    content = pdf("Public Fixture Paper")
    def default(request):
        calls.append(request)
        if handler:
            return handler(request)
        if "/pdf/" in request.url.path:
            return httpx.Response(200,content=content)
        feed = '''<feed xmlns="http://www.w3.org/2005/Atom"><entry>
        <id>https://arxiv.org/abs/2401.12345v1</id><title>Public Fixture Paper</title>
        <summary>Original public metadata.</summary><author><name>Fixture Author</name></author>
        <published>2024-01-01T00:00:00Z</published><updated>2024-01-01T00:00:00Z</updated>
        <link title="pdf" href="https://arxiv.org/pdf/2401.12345v1"/></entry></feed>'''
        return httpx.Response(200,content=feed.encode())
    discovery = DiscoveryService(tools.library,httpx.Client(transport=httpx.MockTransport(default)),request_interval=0)
    monkeypatch.setattr(tools,"_discovery",lambda:discovery)
    tools.scope.update(allow_arxiv=True,import_limit=1,public_query="2401.12345v1")
    return discovery,calls


def test_schemas_follow_capabilities_and_reject_unknown_names_without_echo(setup):
    _,_,tools = setup
    assert {s["function"]["name"] for s in tools.schemas()} == {"search_evidence","build_citation_graph"}
    assert all(s["function"]["parameters"]["additionalProperties"] is False for s in tools.schemas())
    result = invoke(tools,"secret-private-shell-command",{"path":"private"})
    assert result == {"status":"error","code":"tool_not_allowed","retryable":False}
    tools.scope.update(allow_arxiv=True,import_limit=True)
    assert "import_paper" not in {s["function"]["name"] for s in tools.schemas()}


@pytest.mark.parametrize("change",[{"limit":True},{"limit":"3"},{"limit":13},{"document_ids":[]},
                                   {"query":"x","unknown_private_field":"secret"}])
def test_arguments_are_strict_and_error_is_redacted(setup,change):
    _,doc,tools = setup
    result = invoke(tools,"search_evidence",{"query":"retrieval","document_ids":[doc["id"]],**change})
    assert result["code"] == "invalid_arguments"
    assert "secret" not in json.dumps(result)


@pytest.mark.parametrize("text",['{"query":"x","query":"y"}','{"limit":NaN}','[]','null','{"query":'])
def test_malformed_or_ambiguous_json_is_not_executed(setup,text):
    result = asyncio.run(setup[2].execute("search_evidence",text))
    assert result["code"] == "invalid_arguments"


def test_selected_scope_and_account_boundary_apply_before_retrieval(setup,tmp_path):
    library,doc,tools = setup
    outside = add(library,"Unselected Private Article")
    foreign = add(Library(tmp_path/"other-account"),"Different Account Article")
    for identifier in [outside["id"],foreign["id"]]:
        result = invoke(tools,"search_evidence",{"query":"retrieval","document_ids":[identifier]})
        assert result["code"] == "document_out_of_scope"
    tools.scope["document_ids"].append(foreign["id"])
    assert invoke(tools,"build_citation_graph",{"document_ids":[foreign["id"]]})["code"] == "resource_unavailable"
    library.archive_document(doc["id"])
    assert invoke(tools,"search_evidence",{"query":"retrieval","document_ids":[doc["id"]]})["code"] == "document_unavailable"


def test_retrieval_keeps_fixed_run_and_canonical_evidence_without_model(setup):
    library,doc,tools = setup
    fixed = doc["available_run"]["id"]
    library.create_run(doc["id"]); library.process_next_job()
    assert library.get_document(doc["id"])["available_run"]["id"] != fixed
    result = invoke(tools,"search_evidence",{"query":"retrieval architecture","document_ids":[doc["id"]],"limit":4})
    assert result["status"] == "ok" and result["evidence"]
    assert result["retrieval"]["semantic"]["status"] == "disabled"
    assert result["retrieval"]["expansion"]["calls"] == 0
    assert all(item["run_id"] == fixed for item in result["evidence"])
    for item in result["evidence"]:
        page = library.get_page(doc["id"],item["page"],fixed)
        original = next(block for block in page["blocks"] if block["id"] == item["block_id"])
        assert item["text"] == original["text"] and item["bbox"] == original["bbox"]
    before = len(tools.state["evidence"])
    invoke(tools,"search_evidence",{"query":"retrieval architecture","document_ids":[doc["id"]]})
    assert len(tools.state["evidence"]) == before


def test_graph_exact_runs_and_damping_preserve_existing_default_api(setup):
    library,target,tools = setup
    source = add(library,"Applications of Reliable Evidence","[1] Reliable Evidence Retrieval. 2024.")
    tools.scope["document_ids"].append(source["id"])
    tools.state["run_ids"][source["id"]] = source["available_run"]["id"]
    library.create_run(source["id"]); library.process_next_job()
    result = invoke(tools,"build_citation_graph",{"document_ids":[source["id"],target["id"]]})
    assert result["graph"]["method"]["damping"] == .85
    assert len(result["graph"]["edges"]) == 1
    assert result["graph"]["edges"][0]["evidence_ids"]
    assert all(block["run_id"] == source["available_run"]["id"] for block in tools.state["evidence"])
    assert GraphService(library).build()["nodes"]
    with pytest.raises(LibraryError):
        GraphService(library).build([target["id"]],run_ids={target["id"]:source["available_run"]["id"]})
    with pytest.raises(LibraryError):
        GraphService(library).build([target["id"]],run_ids={})


def test_evidence_is_bounded_and_model_text_is_not_trusted(setup,monkeypatch):
    _,doc,tools = setup
    original = tools.service._blocks([doc["id"]])[0]
    async def fake(*args,**kwargs):
        assert kwargs["expansion"] == "off"
        assert kwargs["run_ids"] == tools.state["run_ids"]
        return {"evidence":[original.model_copy(update={"text":"FABRICATED TEXT","page":999})]}
    monkeypatch.setattr(tools.service,"retrieve_for_question",fake)
    result = invoke(tools,"search_evidence",{"query":"retrieval","document_ids":[doc["id"]]})
    assert result["evidence"][0]["text"] == original.text
    assert result["evidence"][0]["page"] == original.page
    tools.state["evidence"] = [{**original.model_dump(),"evidence_id":"filled-"+str(i),"text":"x"*2000} for i in range(MAX_EVIDENCE)]
    assert len(tools.state["evidence"]) == MAX_EVIDENCE
    result = invoke(tools,"search_evidence",{"query":"retrieval","document_ids":[doc["id"]]})
    assert result["evidence"] == []
    assert sum(len(item["text"]) for item in tools.state["evidence"]) <= MAX_EVIDENCE_CHARS


def test_public_query_cannot_leak_private_evidence_and_repeated_search_is_reused(setup,monkeypatch):
    _,_,tools = setup
    _,calls = remote(tools,monkeypatch)
    refused = invoke(tools,"search_arxiv",{"query":"private manuscript content"})
    assert refused["code"] == "public_query_not_authorized" and not calls
    first = invoke(tools,"search_arxiv",{"query":"2401.12345v1"})
    assert first["metadata_only"] and first["papers"][0]["evidence_scope"] == "public_metadata_only"
    assert tools.state["discovered_ids"] == ["arxiv:2401.12345v1"]
    assert tools.state["evidence"] == []
    assert invoke(tools,"search_arxiv",{"query":"2401.12345v1"}) == first
    assert len(calls) == 1
    assert calls[0].url.params["sortBy"] == "submittedDate"


def test_unknown_remote_outcome_is_not_blindly_repeated(setup,monkeypatch):
    _,_,tools = setup
    discovery,_ = remote(tools,monkeypatch)
    calls = []
    def broken(*args,**kwargs):
        calls.append(1);raise OSError("SECRET transport detail")
    monkeypatch.setattr(discovery,"search",broken)
    result = invoke(tools,"search_arxiv",{"query":"2401.12345v1"})
    assert result["code"] == "operation_outcome_unknown" and "SECRET" not in json.dumps(result)
    assert invoke(tools,"search_arxiv",{"query":"2401.12345v1"}) == result and len(calls) == 1


def test_known_safe_search_error_has_only_one_retry(setup,monkeypatch):
    _,_,tools = setup
    discovery,_ = remote(tools,monkeypatch)
    calls = []
    def rejected(*args,**kwargs):
        calls.append(1);raise DiscoveryError("source rejected this public query")
    monkeypatch.setattr(discovery,"search",rejected)
    assert invoke(tools,"search_arxiv",{"query":"2401.12345v1"})["retryable"]
    assert not invoke(tools,"search_arxiv",{"query":"2401.12345v1"})["retryable"]
    assert invoke(tools,"search_arxiv",{"query":"2401.12345v1"})["code"] == "external_retry_exhausted"
    assert len(calls) == 2


def test_import_requires_same_task_discovery_and_parse_poll_is_idempotent(setup,monkeypatch):
    library,original,tools = setup
    _,calls = remote(tools,monkeypatch)
    identifier = "arxiv:2401.12345v1"
    assert invoke(tools,"import_paper",{"paper_id":identifier})["code"] == "paper_not_discovered_in_task"
    assert not calls
    invoke(tools,"search_arxiv",{"query":"2401.12345v1"})
    imported = invoke(tools,"import_paper",{"paper_id":identifier})
    doc_id = imported["document_id"]
    assert imported["parse_status"] == "queued"
    assert invoke(tools,"prepare_parse",{"document_id":original["id"]})["code"] == "document_not_imported_in_task"
    first = invoke(tools,"prepare_parse",{"document_id":doc_id})
    assert first["status"] == "waiting"
    assert invoke(tools,"prepare_parse",{"document_id":doc_id}) == first
    assert len(library.list_runs(doc_id)) == 1
    assert invoke(tools,"import_paper",{"paper_id":identifier})["duplicate"]
    assert len(calls) == 2
    library.process_next_job()
    ready = invoke(tools,"prepare_parse",{"document_id":doc_id})
    assert ready["code"] == "parse_ready"
    assert tools.state["run_ids"][doc_id] == first["run_id"]
    assert invoke(tools,"search_evidence",{"query":"retrieval architecture","document_ids":[doc_id]})["evidence"]
    tools.state["discovered_ids"].append("arxiv:2401.99999v1")
    assert invoke(tools,"import_paper",{"paper_id":"arxiv:2401.99999v1"})["code"] == "import_quota_exhausted"


def test_import_lost_return_reconciles_existing_mapping_without_download(setup,monkeypatch):
    _,_,tools = setup
    discovery,calls = remote(tools,monkeypatch)
    identifier = "arxiv:2401.12345v1"
    invoke(tools,"search_arxiv",{"query":"2401.12345v1"})
    real_import = discovery.import_paper
    def lost_return(*args):
        real_import(*args);raise OSError("response lost after durable import")
    monkeypatch.setattr(discovery,"import_paper",lost_return)
    assert invoke(tools,"import_paper",{"paper_id":identifier})["code"] == "operation_outcome_unknown"
    result = invoke(tools,"import_paper",{"paper_id":identifier})
    assert result["code"] == "paper_imported" and len(calls) == 2


def test_recovered_import_without_mapping_never_downloads_or_releases_quota(setup,monkeypatch):
    _,_,tools = setup
    _,calls = remote(tools,monkeypatch)
    identifier = "arxiv:2401.12345v1"
    invoke(tools,"search_arxiv",{"query":"2401.12345v1"})
    result = asyncio.run(tools.execute("import_paper",json.dumps({"paper_id":identifier}),recover_import=True))
    assert result["code"] == "operation_outcome_unknown" and len(calls) == 1
    tools.state["discovered_ids"].append("arxiv:2401.99999v1")
    assert invoke(tools,"import_paper",{"paper_id":"arxiv:2401.99999v1"})["code"] == "import_quota_exhausted"


def test_recovered_import_uses_durable_mapping_and_preserves_pinned_run(setup,monkeypatch):
    library,_,tools = setup
    discovery,calls = remote(tools,monkeypatch)
    identifier = "arxiv:2401.12345v1"
    invoke(tools,"search_arxiv",{"query":"2401.12345v1"})
    doc = discovery.import_paper(identifier)["document"]
    library.process_next_job()
    fixed = library.get_document(doc["id"])["available_run"]["id"]
    tools.scope["document_ids"].append(doc["id"])
    tools.state["run_ids"][doc["id"]] = fixed
    library.create_run(doc["id"]); library.process_next_job()
    assert library.get_document(doc["id"])["available_run"]["id"] != fixed
    result = asyncio.run(tools.execute("import_paper",json.dumps({"paper_id":identifier}),recover_import=True))
    assert result["code"] == "paper_imported" and len(calls) == 2
    assert tools.state["run_ids"][doc["id"]] == tools.state["parse_runs"][doc["id"]] == fixed
    assert invoke(tools,"prepare_parse",{"document_id":doc["id"]})["run_id"] == fixed


def test_failed_parse_does_not_create_new_version(setup,monkeypatch):
    library,_,tools = setup
    remote(tools,monkeypatch)
    invoke(tools,"search_arxiv",{"query":"2401.12345v1"})
    imported = invoke(tools,"import_paper",{"paper_id":"arxiv:2401.12345v1"})
    library.cancel_run(imported["run_id"])
    assert invoke(tools,"prepare_parse",{"document_id":imported["document_id"]})["code"] == "parse_unavailable"
    assert len(library.list_runs(imported["document_id"])) == 1


def test_subscription_is_selected_by_scope_not_model_and_reuses_check(setup,monkeypatch):
    _,_,tools = setup
    discovery,calls = remote(tools,monkeypatch)
    first = discovery.create_subscription("public authorized topic")
    other = discovery.create_subscription("other topic")
    tools.scope["subscription_id"] = first["id"]
    assert invoke(tools,"check_arxiv_updates",{"subscription_id":other["id"]})["code"] == "invalid_arguments"
    assert not calls
    result = invoke(tools,"check_arxiv_updates")
    assert result["code"] == "subscription_checked" and len(calls) == 1
    assert invoke(tools,"check_arxiv_updates") == result and len(calls) == 1
    assert next(item for item in discovery.list_subscriptions() if item["id"] == other["id"])["last_checked_at"] is None


def test_cancellation_prevents_network_and_success_is_not_lost_to_cancel_race(setup,monkeypatch):
    _,_,tools = setup
    _,calls = remote(tools,monkeypatch)
    def cancelled():
        raise TaskCancelled()
    tools.check_cancel = cancelled
    with pytest.raises(TaskCancelled):
        invoke(tools,"search_arxiv",{"query":"2401.12345v1"})
    assert not calls
    count = []
    def next_call_cancelled():
        count.append(1)
        if len(count)>1:raise TaskCancelled()
    tools.check_cancel = next_call_cancelled
    result = invoke(tools,"search_arxiv",{"query":"2401.12345v1"})
    assert result["status"] == "ok" and tools.state["discovered_ids"]
    assert len(count) == 1
