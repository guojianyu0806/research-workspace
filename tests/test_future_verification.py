"""Offline integration/guardrail tests, not evidence of model research quality."""

import asyncio
from copy import deepcopy

import pytest

from research_workspace.future_verification import verify_future_work


QUOTE = "We introduce a causal attention method for graph prediction."
OLD_LIMITATION = "未进行外部最新文献检索；是否已解决及新颖性均为证据不足，需后续研究核验。"


def run(awaitable):
    return asyncio.run(awaitable)


def paper(identifier="2601.00001v1", **changes):
    result = {"id": f"arxiv:{identifier}", "arxiv_id": identifier,
              "source": "arxiv", "source_url": f"https://arxiv.org/abs/{identifier}",
              "title": "Causal Attention for Graph Prediction", "summary": QUOTE,
              "published": "2026-01-02T00:00:00Z", "updated": "2026-01-02T00:00:00Z",
              "retrieved_at": "2026-10-07T10:00:00+00:00"}
    return {**result, **changes}


def synthesis(count=1):
    headings = ["Causal attention", "Robust graph", "Bayesian uncertainty", "Sparse retrieval"]
    return {"kind": "future_work", "status": "generated", "content": "Original academic synthesis",
            "sections": [{"heading": headings[index], "proposed_work": f"Study {headings[index]} for prediction.",
                          "claims": [{"text": "Original local evidence", "citations": []}]} for index in range(count)],
            "references": [{"document_id": "real-local-id", "title": "Graph Prediction"}],
            "sources": [{"evidence_id": "local-source", "text": "Keep original source"}],
            "limitations": [OLD_LIMITATION], "prompt_version": "research-evidence-v1",
            "trace": [{"step": "model", "detail": "Original observable event"}],
            "future_work_verification": "evidence_insufficient"}


class FakeDiscovery:
    def __init__(self, items=None, *, fail=False, cached=False):
        self.items = [paper()] if items is None else items
        self.fail = fail
        self.cached = cached
        self.calls = []

    def search(self, query, **kwargs):
        self.calls.append({"query": query, **kwargs})
        if self.fail:
            raise RuntimeError("fixture-secret-key and private server body")
        return {"query": query, "items": self.items, "retrieved_at": "2026-10-07T10:00:00+00:00",
                "cached": self.cached, "total_results": len(self.items),
                "next_network_refresh_at": "2026-10-08T10:00:00+00:00"}


class FakeProvider:
    def __init__(self, transform=None, *, configured=True, fail=False):
        self.configured = configured
        self.transform = transform
        self.fail = fail
        self.calls = []

    async def generate(self, prompt, payload, output_model):
        self.calls.append(deepcopy(payload))
        if self.fail:
            raise RuntimeError("fixture-secret-key and private provider response")
        data = {"directions": [{"direction_id": item["direction_id"],
                                "status": "potentially_addressed", "coverage": "explicit_direction_coverage",
                                "citations": [{"source_id": item["allowed_source_ids"][0], "quote": QUOTE}]}
                               for item in payload["directions"]]}
        if self.transform:
            data = self.transform(data, payload)
        return output_model.model_validate(data)


def test_real_sources_are_bound_and_original_synthesis_is_preserved():
    original = synthesis()
    before = deepcopy(original)
    discovery, provider = FakeDiscovery(cached=True), FakeProvider()
    result = run(verify_future_work(None, provider, original, discovery=discovery))
    assert original == before
    assert result["sections"] == before["sections"]
    assert result["sources"] == before["sources"]
    assert result["content"] == before["content"]
    assert result["prompt_version"] == "research-evidence-v1"
    audit = result["future_verification"]
    assert audit["prompt_version"] == "future-verification-v1"
    assert audit["status"] == "completed"
    assert audit["model_called"] is True
    assert audit["full_text_reviewed"] is False
    assert audit["novelty_proven"] is False
    assert audit["queries"][0]["retrieved_at"] == "2026-10-07T10:00:00+00:00"
    assert audit["queries"][0]["cached"] is True
    assert result["search_time_range"]["date_filter"] is None
    assert OLD_LIMITATION not in result["limitations"]
    excluded = result["excluded_directions"][0]
    assert excluded["status"] == "potentially_addressed"
    assert excluded["requires_full_text_confirmation"] is True
    assert excluded["citations"] == [{"source_id": "arxiv:2601.00001v1", "quote": QUOTE,
                                       "url": "https://arxiv.org/abs/2601.00001v1",
                                       "published": "2026-01-02T00:00:00Z"}]
    assert "全文确认" in excluded["reason"]
    assert result["retained_directions"] == []
    assert len(provider.calls) == 1


def test_query_budget_is_global_and_unsearched_direction_is_not_assumed_verified():
    discovery = FakeDiscovery(items=[paper(f"2601.{index:05d}v1") for index in range(1, 9)])
    provider = FakeProvider()
    result = run(verify_future_work(None, provider, synthesis(4), discovery=discovery))
    assert len(discovery.calls) == 3
    for call in discovery.calls:
        assert call["limit"] == 5
        assert call["sort_by"] == "submittedDate"
        assert call["field"] == "all"
    assert all(len(query["source_ids"]) <= 5 for query in result["future_verification"]["queries"])
    assert len(result["future_verification"]["sources"]) == 5
    assert result["future_verification"]["status"] == "partial"
    retained = result["retained_directions"]
    assert len(retained) == 1 and retained[0]["direction_id"] == "direction-4"
    assert retained[0]["status"] == "evidence_insufficient"
    assert retained[0]["query_limitation"] == "query_budget_exhausted"


def test_identical_queries_are_shared_without_redundant_network_calls():
    value = synthesis()
    value["sections"] = value["sections"] * 4
    discovery = FakeDiscovery()
    result = run(verify_future_work(None, FakeProvider(), value, discovery=discovery))
    assert len(discovery.calls) == 1
    assert len(result["excluded_directions"]) == 4
    assert result["future_verification"]["queries"][0]["direction_ids"] == [f"direction-{i}" for i in range(1, 5)]


@pytest.mark.parametrize("change", [
    {"source_id": "arxiv:2601.99999v1", "quote": QUOTE},
    {"source_id": "arxiv:2601.00001v1", "quote": "We solved the entire research problem without limitations."},
    {"source_id": "arxiv:2601.00001v1", "quote": "a causal attention method"},
])
def test_invented_source_paraphrase_and_incomplete_quote_cannot_exclude(change):
    def mutate(data, payload):
        data["directions"][0]["citations"] = [change]
        return data
    result = run(verify_future_work(None, FakeProvider(mutate), synthesis(), discovery=FakeDiscovery()))
    assert result["excluded_directions"] == []
    direction = result["retained_directions"][0]
    assert direction["status"] == "evidence_insufficient"
    assert direction["citations"] == []
    assert "原句校验" in direction["reason"]


def test_quote_normalizes_whitespace_without_changing_spelling():
    value = paper(summary=QUOTE.replace("causal attention", "causal\n attention"))
    result = run(verify_future_work(None, FakeProvider(), synthesis(), discovery=FakeDiscovery([value])))
    assert len(result["excluded_directions"]) == 1


@pytest.mark.parametrize("status,coverage", [
    ("potentially_addressed", "related_only"),
    ("not_found_in_search", "explicit_direction_coverage"),
])
def test_inconsistent_model_classifications_are_downgraded(status, coverage):
    def mutate(data, payload):
        data["directions"][0].update(status=status, coverage=coverage)
        return data
    result = run(verify_future_work(None, FakeProvider(mutate), synthesis(), discovery=FakeDiscovery()))
    assert result["retained_directions"][0]["status"] == "evidence_insufficient"
    assert result["excluded_directions"] == []


def test_not_found_is_a_bounded_screening_result_and_never_a_gap_proof():
    def mutate(data, payload):
        data["directions"][0].update(status="not_found_in_search", coverage="related_only")
        return data
    result = run(verify_future_work(None, FakeProvider(mutate), synthesis(), discovery=FakeDiscovery()))
    direction = result["retained_directions"][0]
    assert direction["status"] == "not_found_in_search"
    assert "不能证明研究空白" in direction["reason"]
    assert direction["citations"]
    assert result["excluded_directions"] == []


def test_classification_requires_evidence_even_for_not_found():
    def mutate(data, payload):
        data["directions"][0].update(status="not_found_in_search", coverage="related_only", citations=[])
        return data
    result = run(verify_future_work(None, FakeProvider(mutate), synthesis(), discovery=FakeDiscovery()))
    assert result["retained_directions"][0]["status"] == "evidence_insufficient"


@pytest.mark.parametrize("operation", ["missing", "duplicate", "invented", "extra_field"])
def test_invalid_model_schema_or_direction_set_preserves_all_candidates(operation):
    def mutate(data, payload):
        if operation == "missing":
            data["directions"].pop()
        elif operation == "duplicate":
            data["directions"][1] = data["directions"][0]
        elif operation == "invented":
            data["directions"][1]["direction_id"] = "direction-99"
        else:
            data["directions"][0]["resolved"] = True
        return data
    result = run(verify_future_work(None, FakeProvider(mutate), synthesis(2), discovery=FakeDiscovery()))
    assert len(result["retained_directions"]) == 2
    assert result["excluded_directions"] == []
    assert all(item["status"] == "evidence_insufficient" for item in result["retained_directions"])
    assert result["future_verification"]["errors"][0]["code"] == "verification_failed"


def test_own_query_source_scope_prevents_cross_direction_citation_substitution():
    class SeparateDiscovery(FakeDiscovery):
        def search(self, query, **kwargs):
            response = super().search(query, **kwargs)
            response["items"] = [paper(f"2601.{len(self.calls):05d}v1")]
            return response
    def mutate(data, payload):
        data["directions"][0]["citations"][0]["source_id"] = "arxiv:2601.00002v1"
        return data
    result = run(verify_future_work(None, FakeProvider(mutate), synthesis(2), discovery=SeparateDiscovery()))
    assert len(result["excluded_directions"]) == 1
    assert result["excluded_directions"][0]["direction_id"] == "direction-2"
    assert result["retained_directions"][0]["direction_id"] == "direction-1"


@pytest.mark.parametrize("items", [[], [paper(source_url="https://invalid.example/abs/2601.00001v1")],
                                    [paper(source_url="https://arxiv.org/abs/2601.00002v1")],
                                    [paper(summary="")]])
def test_empty_or_invalid_public_records_skip_model_and_cannot_prove_gap(items):
    provider = FakeProvider()
    result = run(verify_future_work(None, provider, synthesis(), discovery=FakeDiscovery(items)))
    assert provider.calls == []
    assert result["future_verification"]["status"] == "evidence_insufficient"
    assert result["future_verification"]["sources"] == []
    assert result["retained_directions"][0]["status"] == "evidence_insufficient"
    assert result["excluded_directions"] == []


@pytest.mark.parametrize("failure", ["search", "model"])
def test_failures_are_safe_and_never_leak_provider_exception_text(failure):
    provider = FakeProvider(fail=failure == "model")
    discovery = FakeDiscovery(fail=failure == "search")
    result = run(verify_future_work(None, provider, synthesis(), discovery=discovery))
    assert result["excluded_directions"] == []
    assert result["retained_directions"][0]["status"] == "evidence_insufficient"
    assert "fixture-secret-key" not in str(result)
    assert "private provider" not in str(result)
    assert "private server" not in str(result)
    if failure == "search":
        assert provider.calls == []
        assert result["future_verification"]["queries"][0]["status"] == "failed"


def test_one_failed_search_retains_all_directions_even_if_another_query_succeeded():
    class PartialFailure(FakeDiscovery):
        def search(self, query, **kwargs):
            response = super().search(query, **kwargs)
            if len(self.calls) == 2:
                raise RuntimeError("network down")
            return response
    provider = FakeProvider()
    result = run(verify_future_work(None, provider, synthesis(2), discovery=PartialFailure()))
    assert provider.calls == []
    assert len(result["retained_directions"]) == 2
    assert all(item["status"] == "evidence_insufficient" for item in result["retained_directions"])
    assert result["future_verification"]["sources"]


def test_no_proposals_and_unconfigured_provider_make_no_requests():
    value = synthesis()
    value["sections"][0]["proposed_work"] = ""
    discovery, provider = FakeDiscovery(), FakeProvider()
    result = run(verify_future_work(None, provider, value, discovery=discovery))
    assert result["retained_directions"] == [] and result["excluded_directions"] == []
    assert result["future_verification"]["status"] == "evidence_insufficient"
    assert discovery.calls == provider.calls == []
    provider = FakeProvider(configured=False)
    result = run(verify_future_work(None, provider, synthesis(), discovery=discovery))
    assert discovery.calls == provider.calls == []
    assert result["future_verification"]["queries"][0]["status"] == "not_attempted"
    assert result["future_verification"]["errors"][0]["code"] == "model_not_configured"
    assert OLD_LIMITATION in result["limitations"]


def test_chinese_direction_uses_disclosed_title_topic_or_remains_unverified():
    value = synthesis()
    value["sections"] = [{"heading": "未来方向", "proposed_work": "研究更稳健的图预测方法。"}]
    discovery = FakeDiscovery()
    result = run(verify_future_work(None, FakeProvider(), value, discovery=discovery))
    assert discovery.calls[0]["query"] == "graph prediction"
    assert result["future_verification"]["queries"][0]["strategy"] == "topic_only"
    value["references"][0]["title"] = "图预测"
    provider, discovery = FakeProvider(), FakeDiscovery()
    result = run(verify_future_work(None, provider, value, discovery=discovery))
    assert discovery.calls == provider.calls == []
    assert result["retained_directions"][0]["query_limitation"] == "no_usable_english_keywords"
    assert result["retained_directions"][0]["status"] == "evidence_insufficient"


def test_returned_query_must_match_the_executed_query():
    class WrongQuery(FakeDiscovery):
        def search(self, query, **kwargs):
            response = super().search(query, **kwargs)
            response["query"] = "different topic"
            return response
    provider = FakeProvider()
    result = run(verify_future_work(None, provider, synthesis(), discovery=WrongQuery()))
    assert provider.calls == []
    assert result["future_verification"]["queries"][0]["status"] == "failed"


def test_cancellation_propagates_instead_of_recording_a_false_completed_review():
    class CancelledProvider(FakeProvider):
        async def generate(self, *args):
            raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        run(verify_future_work(None, CancelledProvider(), synthesis(), discovery=FakeDiscovery()))


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("phase", ["next_search", "model"])
def test_task_cancel_callback_stops_before_next_operation_without_degradation(asynchronous, phase):
    class TaskCancelled(Exception):
        pass
    calls = []
    def cancel_at_second_checkpoint():
        calls.append("checked")
        if len(calls) == 2:
            raise TaskCancelled("task cancelled by user")
    async def async_cancel_at_second_checkpoint():
        cancel_at_second_checkpoint()
    callback = async_cancel_at_second_checkpoint if asynchronous else cancel_at_second_checkpoint
    provider, discovery = FakeProvider(), FakeDiscovery()
    with pytest.raises(TaskCancelled, match="cancelled by user"):
        run(verify_future_work(None, provider, synthesis(2 if phase == "next_search" else 1),
                               discovery=discovery, check_cancel=callback))
    assert len(calls) == 2
    assert len(discovery.calls) == 1
    assert provider.calls == []


def test_other_synthesis_kind_is_not_silently_modified():
    value = synthesis()
    value["kind"] = "review"
    with pytest.raises(ValueError, match="Future Work"):
        run(verify_future_work(None, FakeProvider(), value, discovery=FakeDiscovery()))
