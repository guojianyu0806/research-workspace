"""Function-calling HTTP envelopes tested offline; no live model requests."""
import asyncio
import copy
import json

import httpx
import pytest

from research_workspace.agent_protocol import request_tool_turn, OUTPUT_SCHEMA
from research_workspace.intelligence import ChatCompletionProvider, ModelConfig, ModelOutputError, ModelProviderError

TOOLS = [{'type': 'function', 'function': {'name': 'search_evidence', 'description': 'Search selected papers.',
          'parameters': {'type': 'object', 'properties': {'query': {'type': 'string'}},
                         'required': ['query'], 'additionalProperties': False}}}]
MESSAGES = [{'role': 'system', 'content': 'Use the declared tools.'}, {'role': 'user', 'content': 'Find evidence.'}]


def adapter(handler, *, official=False):
    return ChatCompletionProvider(ModelConfig('https://api.deepseek.com' if official else 'https://model.invalid/v1',
        'offline-fixture', 'fixture-private-key', max_attempts=3), transport=httpx.MockTransport(handler))


def tool_call(identifier='call_1', arguments='{"query":"attention"}'):
    return {'id': identifier, 'type': 'function', 'function': {'name': 'search_evidence', 'arguments': arguments}}


def envelope(*, calls=None, content=None, finish='tool_calls', **extra):
    message = {'role': 'assistant', 'content': content, **extra}
    if calls is not None:
        message['tool_calls'] = calls
    return {'choices': [{'index': 0, 'finish_reason': finish, 'message': message}]}


def test_two_turns_send_tools_and_exact_tool_results_through_usage_tracking(monkeypatch):
    from research_workspace import model_usage
    actual_post, receipts, requests = model_usage.tracked_post, [], []
    async def tracked(*args, **kwargs):
        receipts.append({key: kwargs[key] for key in ('schema', 'prompt', 'attempt')})
        return await actual_post(*args, **kwargs)
    monkeypatch.setattr(model_usage, 'tracked_post', tracked)
    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        assert str(request.url) == 'https://api.deepseek.com/chat/completions'
        assert body['thinking'] == {'type': 'disabled'}
        assert body['tool_choice'] == 'auto' and body['tools'] == TOOLS and body['stream'] is False
        assert 'response_format' not in body
        if len(requests) == 1:
            return httpx.Response(200, json=envelope(calls=[tool_call()], reasoning_content='private reasoning'))
        assert body['messages'][-2] == {'role': 'assistant', 'content': None, 'tool_calls': [tool_call()]}
        assert body['messages'][-1] == {'role': 'tool', 'tool_call_id': 'call_1', 'content': '{"hits":["E1"]}'}
        assert 'private reasoning' not in request.content.decode()
        return httpx.Response(200, json=envelope(finish='stop', calls=[], content='Evidence found.', reasoning_content='private again'))
    provider = adapter(respond, official=True)
    async def conversation():
        first = await request_tool_turn(provider, MESSAGES, TOOLS)
        assert 'reasoning_content' not in first
        return await request_tool_turn(provider, [*MESSAGES, first,
            {'role': 'tool', 'tool_call_id': 'call_1', 'content': '{"hits":["E1"]}'}], TOOLS)
    assert asyncio.run(conversation()) == {'role': 'assistant', 'content': 'Evidence found.'}
    assert len(receipts) == 2
    assert all(item == {'schema': OUTPUT_SCHEMA, 'prompt': MESSAGES[0]['content'], 'attempt': 1} for item in receipts)


def test_malformed_json_arguments_are_preserved_for_runner_replanning():
    broken = '{"query":'
    provider = adapter(lambda request: httpx.Response(200, json=envelope(calls=[tool_call(arguments=broken)])))
    message = asyncio.run(request_tool_turn(provider, MESSAGES, TOOLS))
    assert message['tool_calls'][0]['function']['arguments'] == broken


@pytest.mark.parametrize('bad', [
    envelope(calls=[tool_call()], finish='length'),
    envelope(calls=[tool_call()], finish='stop'),
    envelope(finish='tool_calls', content='No actual call'),
    envelope(calls=[tool_call(), tool_call()]),
    envelope(calls=[tool_call(identifier='x' * 129)]),
    envelope(calls=[tool_call(arguments={})]),
    envelope(calls=[tool_call(arguments='x' * 16001)]),
    envelope(finish='stop', content=None),
    {'choices': []},
])
def test_invalid_protocol_is_rejected_without_repair_or_replay(bad):
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(200, json=bad)
    with pytest.raises(ModelOutputError):
        asyncio.run(request_tool_turn(adapter(respond), MESSAGES, TOOLS))
    assert len(requests) == 1


@pytest.mark.parametrize('status', [302, 400, 401, 402, 429, 503])
def test_http_errors_are_sanitized_and_never_replayed(status):
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(status, text='fixture-private-key and confidential provider body',
                              headers={'location': 'https://another.invalid'})
    with pytest.raises(ModelProviderError) as caught:
        asyncio.run(request_tool_turn(adapter(respond, official=True), MESSAGES, TOOLS))
    assert len(requests) == 1
    assert 'fixture-private-key' not in str(caught.value) and 'confidential' not in str(caught.value)
    assert caught.value.retryable == (status in {429, 503})


def test_unknown_transport_outcome_is_not_replayed_and_cancellation_propagates():
    requests = []
    def timeout(request):
        requests.append(request)
        raise httpx.ReadTimeout('private network data', request=request)
    with pytest.raises(ModelProviderError, match='结果未知') as caught:
        asyncio.run(request_tool_turn(adapter(timeout), MESSAGES, TOOLS))
    assert len(requests) == 1 and 'private network data' not in str(caught.value)
    async def cancelled(request):
        raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(request_tool_turn(adapter(cancelled), MESSAGES, TOOLS))


def test_history_requires_paired_results_and_ids_are_scoped_to_each_turn():
    provider = adapter(lambda request: pytest.fail('No request for an unmatched tool result'))
    with pytest.raises(ValueError):
        asyncio.run(request_tool_turn(provider, [*MESSAGES,
            {'role': 'tool', 'tool_call_id': 'missing', 'content': '{}'}], TOOLS))
    history = [*MESSAGES, {'role': 'assistant', 'content': None, 'tool_calls': [tool_call()]},
               {'role': 'tool', 'tool_call_id': 'call_1', 'content': '{}'}]
    snapshot = copy.deepcopy(history)
    provider = adapter(lambda request: httpx.Response(200, json=envelope(calls=[tool_call()])))
    # The runner's durable key must include the turn: an identifier is a pairing
    # field within that response, not a provider-independent global identity.
    result = asyncio.run(request_tool_turn(provider, history, TOOLS))
    assert result['tool_calls'] == [tool_call()]
    assert history == snapshot
