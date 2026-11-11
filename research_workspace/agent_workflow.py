"""A bounded model-directed tool loop with durable execution checkpoints."""
from __future__ import annotations

import hashlib
import json
import time

from .agent_protocol import request_tool_turn
from .agent_tools import AgentTools, decode_arguments, selected_document_references
from .intelligence import Evidence, MAX_DOCUMENTS, ModelOutputError, _prompt
from .library import LibraryError
from .research_scope import validate_pinned_documents

MAX_ROUNDS = 8
MAX_CALLS = 12
PARSE_WAIT_SECONDS = 120
MAX_TOOL_RESULT_CHARS = 18000

TOOL_LABELS = {
    "search_evidence": "检索原文证据",
    "build_citation_graph": "计算引用图谱",
    "search_arxiv": "搜索公开论文",
    "check_arxiv_updates": "检查已有订阅",
    "import_paper": "导入公开论文",
    "prepare_parse": "检查固定版本解析",
}
EXTERNAL_TOOLS = {"search_arxiv", "check_arxiv_updates", "import_paper"}
ANSWER_PHASE_MESSAGES = {
    "generate": "正在根据固定版本原文证据生成回答。",
    "repair": "正在根据原文证据修正待审核回答。",
    "review": "正在核验原文引句与论断支持度。",
    "coverage": "正在核验回答是否覆盖研究问题。",
}


def prepare_scope(library, payload: dict) -> dict:
    """Freeze the source runs and public actions authorized at task creation."""
    question = payload.get("question", "").strip()
    if not question:
        raise LibraryError("请填写研究问题。")
    document_ids = list(dict.fromkeys(payload.get("document_ids", [])))
    public_query = " ".join((payload.get("agent_public_query") or "").split())
    subscription_id = payload.get("agent_subscription_id")
    import_limit = payload.get("agent_import_limit", 0)
    mode = payload.get("agent_mode", "qa")
    if public_query and len(public_query) < 2:
        raise LibraryError("公开检索词至少需要两个字符。")
    if not document_ids and not (public_query or subscription_id):
        raise LibraryError("请选择可用文献或填写允许提交给 arXiv 的公开检索词。")
    if import_limit and not (public_query or subscription_id):
        raise LibraryError("导入公开论文需要本次公开检索或订阅检查范围。")
    if len(document_ids) + import_limit > MAX_DOCUMENTS:
        raise LibraryError(f"已选文献与允许导入数合计最多{MAX_DOCUMENTS}篇。")
    if mode == "compare" and len(document_ids) + import_limit < 2:
        raise LibraryError("跨文献比较需要至少两篇可选文献。")
    if subscription_id:
        from .discovery import DiscoveryService
        if not any(item["id"] == subscription_id for item in DiscoveryService(library).list_subscriptions()):
            raise LibraryError("所选订阅不属于当前工作空间。")
    selected = []
    for document_id in document_ids:
        document = library.get_document(document_id)
        run = document.get("available_run")
        if document.get("archived_at") or not run:
            raise LibraryError("请选择未归档且已有可用解析的本地文献。")
        selected.append({"document_id": document_id, "title": document["title"][:300], "run_id": run["id"]})
    return {
        "mode": mode, "document_ids": document_ids, "documents": selected,
        "allow_arxiv": bool(public_query or subscription_id),
        "public_query": public_query, "subscription_id": subscription_id,
        "import_limit": import_limit, "refresh_search": payload.get("refresh_search", True),
        "research_days": payload.get("research_days", 90),
    }


def _new_state(scope, question):
    references = {identifier: reference for reference, identifier in selected_document_references(scope).items()}
    model_documents = [{"document_id": references[item["document_id"]], "title": item["title"]}
                       for item in scope["documents"]]
    return {
        "messages": [
            {"role": "system", "content": _prompt("agent_tools_v2")},
            {"role": "user", "content": json.dumps({
                "question": question, "mode": scope["mode"], "selected_documents": model_documents,
                "public_query": scope["public_query"], "may_check_subscription": bool(scope["subscription_id"]),
                "import_limit": scope["import_limit"],
            }, ensure_ascii=False)},
        ],
        "run_ids": {item["document_id"]: item["run_id"] for item in scope["documents"]},
        "rounds": 0, "calls": 0, "pending": [], "current": None,
        "outcomes": {}, "trace": [], "stop_reason": None,
    }


def _fingerprint(name, arguments):
    try:
        parts = ["json", name, decode_arguments(arguments)]
        serialized = json.dumps(parts, sort_keys=True, ensure_ascii=True, allow_nan=False)
    except (ValueError, RecursionError):
        serialized = json.dumps(["raw", name, arguments], ensure_ascii=True)
    return hashlib.sha256(serialized.encode()).hexdigest()


def _error(code):
    return {"status": "error", "code": code, "retryable": False}


def _planning_result(outcome):
    """Send previews to the planner and keep full canonical evidence for review."""
    result = dict(outcome)
    if "evidence" in result:
        result["evidence"] = [
            {**{key: item[key] for key in ("evidence_id", "document_id", "run_id", "block_id", "page")},
             "document_title": item["document_title"][:150], "text": item["text"][:600],
             "truncated": item.get("truncated", False) or len(item["text"]) > 600}
            for item in result["evidence"]
        ]
    if "papers" in result:
        papers = result["papers"]
        result["paper_count"] = len(papers)
        result["papers"] = [{**item, "title": item["title"][:200], "summary": item["summary"][:300]}
                            for item in papers[:10]]
    if "graph" in result:
        graph = result["graph"]
        result["graph"] = {
            **graph, "node_count": len(graph["nodes"]), "edge_count": len(graph["edges"]),
            "nodes": [{**node, "title": node["title"][:150]} for node in graph["nodes"][:24]],
            "edges": graph["edges"][:24],
            "truncated": graph["truncated"] or len(graph["nodes"]) > 24 or len(graph["edges"]) > 24,
        }
    return result


def _tool_message(call, outcome):
    """Return the model message and the receipt shared by the trace and journal."""
    content = json.dumps(outcome, ensure_ascii=False, allow_nan=False)
    if len(content) > MAX_TOOL_RESULT_CHARS:
        outcome = _error("tool_result_too_large")
        content = json.dumps(outcome, ensure_ascii=False, allow_nan=False)
    return {"role": "tool", "tool_call_id": call["id"], "content": content}, outcome


def _record(state, name, outcome, current, *, reused=False):
    tool = name if name in TOOL_LABELS else "unknown"
    record = {
        "tool": tool, "status": outcome["status"], "code": outcome["code"],
        "attempt": current["attempt"] if current else 0,
        "elapsed_ms": max(0, round((time.time() - current["started_at"]) * 1000)) if current else 0,
    }
    if reused:
        record["reused"] = True
    if "retrieval" in outcome:
        record["retrieval"] = outcome["retrieval"]
    if "cached" in outcome:
        record["cached"] = outcome["cached"]
    state["trace"].append(record)
    status = {"ok": "完成", "error": "未完成", "waiting": "等待"}[record["status"]]
    label = TOOL_LABELS.get(tool, "未注册工具")
    message = (f'{label} · 超出本次工具调用上限，未执行。' if record["code"] == "tool_budget_exhausted"
               else f'{label} · {status}（{record["code"]}）')
    return "agent_tool", message


async def _execute_pending(store, job_id, state, tools, check_cancel):
    """Return False when the same parse call must resume in a later worker tick."""
    call = state["pending"][0]
    name, arguments = call["function"]["name"], call["function"]["arguments"]
    key = _fingerprint(name, arguments)
    current = state["current"]
    reused = False
    if current is not None:
        if name == "import_paper":
            outcome = await tools.execute(name, arguments, recover_import=True)
        elif name in EXTERNAL_TOOLS:
            # A process may have died after an external operation took effect.
            outcome = _error("operation_outcome_unknown")
        else:
            outcome = await tools.execute(name, arguments)
    else:
        previous = state["outcomes"].get(key)
        attempt = previous["attempt"] if previous else 0
        state["calls"] += 1
        current = {"attempt": attempt + 1, "started_at": time.time(), "waiting": False}
        if previous and (not previous["result"].get("retryable") or attempt >= 2):
            current["attempt"] = attempt
            outcome = previous["result"]
            reused = True
        else:
            check_cancel()
            state["current"] = current
            started = ("agent_tool_started", f'正在{TOOL_LABELS.get(name, "校验工具调用")}。')
            store.save_agent_state(job_id, state, events=(started,))
            outcome = await tools.execute(name, arguments)
    if outcome["status"] == "waiting" and time.time() - current["started_at"] >= PARSE_WAIT_SECONDS:
        outcome = _error("parse_wait_timeout")
    if outcome["status"] == "waiting":
        events = () if current["waiting"] else (_record(state, name, outcome, current),)
        current["waiting"] = True
        state["current"] = current
        store.save_agent_state(job_id, state, wait_seconds=1, events=events)
        store._yield_step(job_id, stage="agent_waiting_parse", message="等待后台解析完成，已保存工具调用。",
                          repeat_event=False)
        return False
    message, outcome = _tool_message(call, _planning_result(outcome))
    state["outcomes"][key] = {"attempt": current["attempt"], "result": outcome}
    state["messages"].append(message)
    state["pending"].pop(0)
    state["current"] = None
    events = [_record(state, name, outcome, current, reused=reused)]
    if current["waiting"]:
        events.append(("agent_resume_parse", "固定版本解析检查已返回，继续执行研究任务。"))
    store.save_agent_state(job_id, state, events=events)
    return True


async def _final_answer(service, payload, scope, state, check_cancel, report_phase):
    # Only successful tool results, never planner prose, reach answer generation.
    runs = {doc: run for doc, run in state["run_ids"].items() if run}
    evidence = [Evidence.model_validate(item) for item in state.get("evidence", [])
                if item.get("run_id") and runs.get(item.get("document_id")) == item["run_id"]]
    mode = scope["mode"]
    if runs and evidence and (mode != "compare" or len(runs) >= 2):
        answer = await service.answer(payload["question"], list(runs), mode=mode, run_ids=runs,
            check_cancel=check_cancel, report_phase=report_phase, tool_retrieval={
                "evidence": evidence, "mode": "agent_tool_evidence",
                "trace": [service._trace("agent", "已固定本任务工具返回的原文证据。")],
            })
    else:
        answer = {"status": "insufficient_evidence", "answer": "本次工具执行未取得足够的固定版本原文，相关结论保留为未知。",
                  "claims": [], "sources": [], "evidence": [item.model_dump() for item in evidence],
                  "verification": "no_matching_evidence"}
    return {
        **answer, "agent_trace": state["trace"], "agent_stop_reason": state["stop_reason"],
        "agent_public_papers": state.get("public_papers", []), "agent_graphs": state.get("graphs", []),
    }


async def agent_step(store, service, job):
    job_id, payload = job["id"], job["payload"]
    scope = payload["agent_scope"]
    state = store.load_agent_state(job_id)
    if state is not None and state.get("answer") is not None:
        return state["answer"]
    follow_up_runs = None
    if payload.get("source_task_id"):
        follow_up_runs = {item["document_id"]: item["run_id"] for item in scope["documents"]}
        validate_pinned_documents(store.library, follow_up_runs)
    if state is None:
        store.event(job_id, "execute", "正在读取本次研究的固定文献和工具范围。")
        state = _new_state(scope, payload["question"])

    def check_cancel():
        store.check_cancel(job_id)

    def report_phase(phase):
        store.event(job_id, f"agent_answer_{phase}", ANSWER_PHASE_MESSAGES[phase])

    tools = AgentTools(service, scope=scope, state=state, check_cancel=check_cancel)
    terminal_events = []
    while not state["stop_reason"]:
        check_cancel()
        if state["pending"] and (state["current"] or state["calls"] < MAX_CALLS):
            if not await _execute_pending(store, job_id, state, tools, check_cancel):
                return None
            continue
        if state["calls"] >= MAX_CALLS:
            # A saved assistant turn can contain more calls than the remaining
            # budget. Close each unexecuted call without consuming an attempt.
            for call in state["pending"]:
                message, outcome = _tool_message(call, _error("tool_budget_exhausted"))
                state["messages"].append(message)
                terminal_events.append(_record(state, call["function"]["name"], outcome, None))
            state["pending"].clear()
            state["stop_reason"] = "tool_limit"
            break
        if state["rounds"] >= MAX_ROUNDS:
            state["stop_reason"] = "round_limit"
            break
        definitions = tools.schemas()
        if not definitions:
            raise ModelOutputError("本次任务没有可使用的工具。")
        state["rounds"] += 1
        store.event(job_id, "agent_plan", f'模型正在选择工具（第 {state["rounds"]} 轮）。')
        store.save_agent_state(job_id, state)
        message = await request_tool_turn(service.provider, state["messages"], definitions)
        calls = message.get("tool_calls", [])
        if calls:
            state["messages"].append({**message, "content": None})
            state["pending"] = list(calls)
        else:
            state["stop_reason"] = "model_finished"
        store.save_agent_state(job_id, state)

    store.save_agent_state(job_id, state, events=terminal_events)
    check_cancel()
    if follow_up_runs is not None:
        validate_pinned_documents(store.library, follow_up_runs)
    result = await _final_answer(service, payload, scope, state, check_cancel, report_phase)
    state["answer"] = result
    store.save_agent_state(job_id, state)
    return result
