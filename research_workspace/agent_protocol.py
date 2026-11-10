"""One bounded, non-streaming function-calling turn; never executes a tool.

The caller owns cancellation checkpoints, tool authorization, durable execution,
and final answer validation. A returned arguments string may contain invalid JSON:
the runner must report that error to the model instead of executing it.
"""
from __future__ import annotations

import json
import re
from urllib.parse import urlsplit

import httpx

from .intelligence import ModelOutputError, ModelProviderError

PROTOCOL_VERSION = 'agent-function-calling-v1'
OUTPUT_SCHEMA = 'AgentToolTurnV1'
MAX_TOOL_CALLS = 8
MAX_ARGUMENT_CHARS = 16000
MAX_CONTENT_CHARS = 32000
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_NAME = re.compile(r'[A-Za-z_][A-Za-z0-9_-]{0,63}')
_ID = re.compile(r'[A-Za-z0-9_.:-]{1,128}')


def _invalid():
    return ModelOutputError('模型工具调用未通过完整性或协议校验；未执行工具。')


def _tool_calls(value):
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_TOOL_CALLS:
        raise _invalid()
    result, seen = [], set()
    for call in value:
        if not isinstance(call, dict) or call.get('type') != 'function':
            raise _invalid()
        identifier, function = call.get('id'), call.get('function')
        if (not isinstance(identifier, str) or not _ID.fullmatch(identifier)
                or identifier in seen or not isinstance(function, dict)):
            raise _invalid()
        name, arguments = function.get('name'), function.get('arguments')
        if (not isinstance(name, str) or not _NAME.fullmatch(name)
                or not isinstance(arguments, str) or len(arguments) > MAX_ARGUMENT_CHARS):
            raise _invalid()
        seen.add(identifier)
        result.append({'id': identifier, 'type': 'function',
                       'function': {'name': name, 'arguments': arguments}})
    return result


def _assistant(message):
    if not isinstance(message, dict) or message.get('role') != 'assistant':
        raise _invalid()
    content = message.get('content')
    if content is not None and (not isinstance(content, str) or len(content) > MAX_CONTENT_CHARS):
        raise _invalid()
    normalized = {'role': 'assistant', 'content': content}
    calls = message.get('tool_calls')
    if calls is not None and calls != []:
        normalized['tool_calls'] = _tool_calls(calls)
    if 'tool_calls' not in normalized and (not isinstance(content, str) or not content.strip()):
        raise _invalid()
    return normalized


def _inputs(messages, tools):
    """Keep public protocol fields only; do not send private reasoning back."""
    if not isinstance(messages, list) or not 1 <= len(messages) <= 128:
        raise ValueError('工具轮次需要 1 至 128 条对话消息。')
    normalized, pending = [], set()
    try:
        for message in messages:
            if not isinstance(message, dict):
                raise ValueError()
            role, content = message.get('role'), message.get('content')
            if role == 'assistant':
                if pending:
                    raise ValueError()
                item = _assistant(message)
                ids = {call['id'] for call in item.get('tool_calls', [])}
                pending.update(ids)
            elif role in {'system', 'user', 'tool'}:
                if not isinstance(content, str) or len(content) > 128000:
                    raise ValueError()
                item = {'role': role, 'content': content}
                if role == 'tool':
                    identifier = message.get('tool_call_id')
                    if not isinstance(identifier, str) or identifier not in pending:
                        raise ValueError()
                    item['tool_call_id'] = identifier
                    pending.remove(identifier)
                elif pending:
                    raise ValueError()
            else:
                raise ValueError()
            normalized.append(item)
        if pending or len(json.dumps(normalized, ensure_ascii=False)) > 512000:
            raise ValueError()
        if not isinstance(tools, list) or not 1 <= len(tools) <= 32:
            raise ValueError()
        names = set()
        for tool in tools:
            if not isinstance(tool, dict) or tool.get('type') != 'function':
                raise ValueError()
            function = tool.get('function')
            if not isinstance(function, dict):
                raise ValueError()
            name = function.get('name')
            if (not isinstance(name, str) or not _NAME.fullmatch(name) or name in names
                    or not isinstance(function.get('parameters'), dict)):
                raise ValueError()
            names.add(name)
        encoded_tools = json.dumps(tools, ensure_ascii=False, allow_nan=False)
        if len(encoded_tools) > 128000:
            raise ValueError()
        return normalized, json.loads(encoded_tools)
    except (ValueError, TypeError, RecursionError, ModelOutputError):
        raise ValueError('工具轮次的对话顺序、调用标识或工具定义无效；未请求模型。') from None


async def request_tool_turn(provider, messages: list[dict], tools: list[dict]) -> dict:
    """Return a normalized assistant message after exactly one HTTP attempt.

    No response repair or transport replay happens here. In particular a read
    timeout may already have incurred a provider charge. asyncio cancellation
    propagates to the caller and the shared usage recorder unchanged.
    """
    endpoint = provider.config.endpoint()
    messages, tools = _inputs(messages, tools)
    body = {'model': provider.config.model, 'messages': messages, 'tools': tools,
            'tool_choice': 'auto', 'temperature': 0, 'max_tokens': 4096, 'stream': False}
    official_deepseek = urlsplit(endpoint).hostname == 'api.deepseek.com'
    if official_deepseek:
        body['thinking'] = {'type': 'disabled'}
    try:
        from .model_usage import tracked_post
        async with httpx.AsyncClient(transport=provider._transport,
                                    timeout=httpx.Timeout(provider.config.timeout_seconds),
                                    follow_redirects=False, trust_env=False) as client:
            response = await tracked_post(client, endpoint,
                headers={'Authorization': f'Bearer {provider.config.api_key}'}, json=body,
                model=provider.config.model, schema=OUTPUT_SCHEMA,
                prompt=next((message['content'] for message in messages if message['role'] == 'system'),
                            PROTOCOL_VERSION), attempt=1)
    except (httpx.ConnectTimeout, httpx.ConnectError):
        raise ModelProviderError('模型工具规划连接失败；未自动重试。', retryable=True) from None
    except (httpx.ReadTimeout, httpx.WriteTimeout, httpx.ReadError, httpx.WriteError):
        raise ModelProviderError('模型工具规划请求中断，服务端结果未知；未自动重试。') from None
    except httpx.HTTPError:
        raise ModelProviderError('模型工具规划 HTTP 请求失败；请检查服务配置。') from None
    if response.status_code in {401, 403}:
        raise ModelProviderError('模型认证失败，请检查密钥与服务访问权限。')
    if response.status_code == 402 and official_deepseek:
        raise ModelProviderError('DeepSeek 账户余额不足（HTTP 402）；请检查账户余额。')
    if response.status_code == 429 or response.status_code >= 500:
        raise ModelProviderError('模型服务限流或暂时不可用；未自动重试。', retryable=True)
    if not 200 <= response.status_code < 300:
        raise ModelProviderError(f'模型服务返回 HTTP {response.status_code}；工具规划未完成。')
    try:
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise _invalid()
        envelope = response.json()
        if not isinstance(envelope, dict) or envelope.get('error'):
            raise _invalid()
        choices = envelope.get('choices')
        if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
            raise _invalid()
        choice = choices[0]
        if type(choice.get('index', 0)) is not int or choice.get('index', 0) != 0:
            raise _invalid()
        finish = choice.get('finish_reason')
        if finish not in {'stop', 'tool_calls'}:
            raise _invalid()
        message = _assistant(choice.get('message'))
        calls = message.get('tool_calls', [])
        if bool(calls) != (finish == 'tool_calls'):
            raise _invalid()
        return message
    except (ValueError, TypeError, KeyError, IndexError, RecursionError):
        raise _invalid() from None
