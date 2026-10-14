"""Bounded English mechanism expansion without domain- or paper-specific rules."""

import asyncio

import pytest

from research_workspace.intelligence import Evidence, IntelligenceService, RetrievalQueryPayload


class Provider:
    configured = True

    def __init__(self, terms=(), failure=False):
        self.terms = terms
        self.failure = failure
        self.calls = []

    async def generate(self, prompt, payload, schema):
        assert schema is RetrievalQueryPayload
        self.calls.append(payload)
        if self.failure:
            raise RuntimeError("private provider response")
        return schema.model_validate({"terms": [
            {"source_phrase": anchor, "term": term} for anchor, term in self.terms]})


def service_with(provider):
    passages = ["The cache reduces response delay by reusing recently computed results.",
                "The invalidation protocol ensures that stale entries cannot be served after a write."]
    blocks = [Evidence(evidence_id="E_" + str(i), document_id="doc", document_title="Cache study",
                       filename="cache.pdf", run_id="frozen-run", block_id=str(i), page=i + 1,
                       bbox=[0.1, 0.1, 0.8, 0.8], text=text) for i, text in enumerate(passages)]
    service = IntelligenceService(None, provider)
    service._blocks = lambda ids: blocks
    return service


@pytest.mark.parametrize("question", [
    "Why does vectorized processing improve throughput and what limits its memory use?",
    "What advantage does sparse routing have over dense routing for computation and model capacity?",
    "How does adaptive caching affect latency and consistency?",
])
def test_multifacet_english_mechanisms_trigger_one_bounded_call(question):
    provider = Provider()
    result = asyncio.run(service_with(provider).retrieve_for_question(question, ["doc"]))
    assert len(provider.calls) == result["expansion"]["calls"] == 1
    assert result["expansion"]["trigger"] == "english_multifacet_mechanism"
    assert result["expansion"]["max_calls"] == 1
    assert result["expansion"]["max_output_tokens"] == 512
    assert provider.calls[0]["max_terms"] == 6


@pytest.mark.parametrize("question", [
    "LayerNorm feature activations", "What is an encoder?", "Why is caching useful?",
    "How does compression work?", "List latency and memory usage.",
    "What advantage does caching offer? Cite sources and provide references.",
    "What advantage does caching offer, and cite the supplied paper?",
])
def test_simple_english_queries_do_not_gain_paid_expansion(question):
    provider = Provider()
    result = asyncio.run(service_with(provider).retrieve_for_question(question, ["doc"]))
    assert provider.calls == []
    assert result["expansion"]["trigger"] == "not_needed"


def test_expansion_covers_two_mechanisms_using_only_canonical_source_blocks():
    question = "How does caching affect latency and consistency?"
    provider = Provider([("latency", "response delay"), ("consistency", "invalidation stale entries")])
    result = asyncio.run(service_with(provider).retrieve_for_question(question, ["doc"], limit=2))
    assert {item.block_id for item in result["evidence"]} == {"0", "1"}
    assert {item.run_id for item in result["evidence"]} == {"frozen-run"}
    assert result["expansion"]["status"] == "applied"
    assert len(provider.calls) == 1


def test_explicit_off_still_prevents_english_mechanism_expansion():
    provider = Provider()
    result = asyncio.run(service_with(provider).retrieve_for_question(
        "How does caching affect latency and consistency?", ["doc"], expansion="off"))
    assert provider.calls == []
    assert result["expansion"]["status"] == "disabled"


def test_failed_english_expansion_does_not_retry_or_expose_provider_details():
    provider = Provider(failure=True)
    result = asyncio.run(service_with(provider).retrieve_for_question(
        "Why does the cache reduce response delay and how are stale entries invalidated?", ["doc"]))
    assert len(provider.calls) == 1
    assert result["expansion"]["status"] == "failed_fallback"
    assert result["evidence"]
    assert "private provider response" not in str(result)
