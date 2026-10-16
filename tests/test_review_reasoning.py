"""Official Pro review calls use bounded reasoning while returning only final JSON."""
import asyncio
import json

import httpx
import pytest

from research_workspace.intelligence import ChatCompletionProvider, ClaimSupportPayload, ModelConfig


@pytest.mark.parametrize("model,host,payload,thinking,tokens", [
    ("deepseek-v4-pro", "api.deepseek.com", {"task": "review"}, "enabled", 9216),
    ("deepseek-v4-pro", "api.deepseek.com", {"task": "verify_single_claim", "response_kind": "review", "claim": {"citations": [{}, {}]}}, "enabled", 9216),
    ("deepseek-v4-pro", "api.deepseek.com", {"task": "verify_review_claim_coverage", "claim": {"citations": [{}, {}]}}, "enabled", 9216),
    ("deepseek-v4-pro", "api.deepseek.com", {"task": "verify_single_claim", "response_kind": "review", "claim": {"citations": [{}]}}, "enabled", 9216),
    ("deepseek-v4-pro", "api.deepseek.com", {"task": "verify_review_claim_coverage", "claim": {"citations": [{}]}}, "disabled", 1024),
    ("deepseek-v4-pro", "api.deepseek.com", {"task": "verify_response_coverage", "response_kind": "review"}, "disabled", 1024),
    ("deepseek-v4-pro", "api.deepseek.com", {"task": "complete_review_missing_dimensions"}, "enabled", 9216),
    ("deepseek-v4-pro", "api.deepseek.com", {"task": "qa"}, "enabled", 9216),
    ("deepseek-v4-pro", "api.deepseek.com", {"task": "compare"}, "enabled", 9216),
    ("deepseek-v4-pro", "api.deepseek.com", {"task": "draft"}, "enabled", 9216),
    ("deepseek-v4-pro", "api.deepseek.com", {"task": "repair_grounded_claim"}, "enabled", 9216),
    ("deepseek-v4-pro", "api.deepseek.com", {"task": "verify_single_claim", "response_kind":"qa"}, "enabled", 9216),
    ("deepseek-v4-pro", "api.deepseek.com", {"task": "verify_single_claim", "response_kind":"compare"}, "enabled", 9216),
    ("deepseek-v4-pro", "api.deepseek.com", {"task": "verify_single_claim", "response_kind":"draft"}, "enabled", 9216),
    ("deepseek-v4-pro", "api.deepseek.com", {"task": "future_work"}, "disabled", 1024),
    ("deepseek-v4-pro", "api.deepseek.com", {"task":"verify_response_coverage", "actual_answers_only":True}, "enabled", 9216),
    ("deepseek-v4-pro", "api.deepseek.com", {"task":"verify_response_coverage", "coverage_contract":{"requested_dimensions":["architecture","training goal"]}}, "enabled", 9216),
    ("deepseek-flash", "api.deepseek.com", {"task": "review"}, "disabled", 1024),
    ("deepseek-v4-pro", "models.example", {"task": "review"}, None, 1024),
])
def test_reasoning_is_scoped_and_final_json_excludes_private_trace(model, host, payload, thinking, tokens):
    requests = []
    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps({"verdict": "supported", "reason": "Own quoted method and property."}),
            "reasoning_content": "private-test-trace-never-returned",
        }}]})
    provider = ChatCompletionProvider(ModelConfig(base_url=f"https://{host}", model=model, api_key="test-only-key"),
                                      transport=httpx.MockTransport(respond))
    result = asyncio.run(provider.generate("Return JSON.", payload, ClaimSupportPayload))
    assert requests[0].get("thinking", {}).get("type") == thinking
    assert requests[0]["max_tokens"] == tokens
    assert requests[0].get("reasoning_effort") == ("low" if thinking == "enabled" else None)
    assert "private-test-trace" not in result.model_dump_json()
