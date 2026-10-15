"""Official Pro review calls use bounded reasoning while returning only final JSON."""
import asyncio
import json

import httpx
import pytest

from research_workspace.intelligence import ChatCompletionProvider, ClaimSupportPayload, ModelConfig


@pytest.mark.parametrize("model,host,payload,thinking,tokens", [
    ("deepseek-v4-pro", "api.deepseek.com", {"task": "review"}, "enabled", 9216),
    ("deepseek-v4-pro", "api.deepseek.com", {"task": "verify_single_claim", "response_kind": "review"}, "enabled", 9216),
    ("deepseek-v4-pro", "api.deepseek.com", {"task": "verify_review_claim_coverage"}, "enabled", 9216),
    ("deepseek-v4-pro", "api.deepseek.com", {"task": "complete_review_missing_dimensions"}, "enabled", 9216),
    ("deepseek-v4-pro", "api.deepseek.com", {"task": "qa"}, "disabled", 1024),
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
