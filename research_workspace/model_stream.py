"""Bounded, unverified public-output previews; never expose private reasoning."""
from contextlib import contextmanager, suppress
from contextvars import ContextVar
import asyncio
import codecs
import json
import time
from uuid import uuid4

import httpx

_active = ContextVar('model_stream_job', default=None)
MAX_PREVIEW = 24000
MAX_CONTENT = 2 * 1024 * 1024
# SSE envelopes and private-reasoning chunks add transport overhead; keep the
# original 2MiB public JSON limit separate from the bounded wire budget.
MAX_WIRE = 16 * 1024 * 1024


class StreamProtocolError(Exception):
    """No provider body or partially generated text belongs in this error."""


@contextmanager
def stream_job(store, job_id):
    token = _active.set((store, job_id))
    try:
        yield
    finally:
        _active.reset(token)


def enabled(schema):
    return _active.get() is not None and schema.__name__ in {
        'AnswerPayload', 'SynthesisPayload', 'TranslationPayload'}


class PublicText:
    """Incrementally decode selected JSON string values, including split escapes.

    This is only a draft projection. Full JSON/schema/source validation is still
    required before any result becomes an answer, citation, or export.
    """
    fields = {'text', 'heading', 'translated_text', 'message', 'proposed_work'}

    def __init__(self):
        self.text = ''
        self.in_string = False
        self.escape = False
        self.unicode = None
        self.surrogate = None
        self.value = ''
        self.last_string = None
        self.expect_value = False
        self.capture = False
        self.pending_separator = False

    def emit(self, char):
        if len(self.text) >= MAX_PREVIEW:
            return
        if self.pending_separator:
            if self.text:
                self.text += '\n\n'[:MAX_PREVIEW-len(self.text)]
            self.pending_separator = False
        if len(self.text) < MAX_PREVIEW:
            self.text += char

    def decoded(self, char):
        # Hold UTF-16 high surrogates until their low surrogate arrives.
        point = ord(char)
        if self.surrogate is not None:
            if 0xDC00 <= point <= 0xDFFF:
                char = chr(0x10000 + ((self.surrogate - 0xD800) << 10) + point - 0xDC00)
            else:
                if self.capture:
                    self.emit('\ufffd')
            self.surrogate = None
        elif 0xD800 <= point <= 0xDBFF:
            self.surrogate = point
            return
        elif 0xDC00 <= point <= 0xDFFF:
            char = '\ufffd'
        if self.capture:
            self.emit(char)
        if len(self.value) < 80:
            self.value += char

    def feed(self, chunk):
        for char in chunk:
            if self.in_string:
                if self.unicode is not None:
                    self.unicode += char
                    if len(self.unicode) == 4:
                        try:
                            self.decoded(chr(int(self.unicode, 16)))
                        except ValueError:
                            raise StreamProtocolError() from None
                        self.unicode = None
                elif self.escape:
                    self.escape = False
                    if char == 'u':
                        self.unicode = ''
                    elif char in '"\\/bfnrt':
                        self.decoded({'b': '\b', 'f': '\f', 'n': '\n', 'r': '\r', 't': '\t'}.get(char, char))
                    else:
                        raise StreamProtocolError()
                elif char == '\\':
                    self.escape = True
                elif char == '"':
                    self.in_string = False
                    self.last_string = self.value
                    self.capture = False
                else:
                    self.decoded(char)
            elif char == '"':
                self.in_string = True
                self.value = ''
                self.capture = self.expect_value
                self.pending_separator = self.capture
                self.expect_value = False
            elif char == ':':
                self.expect_value = self.last_string in self.fields
                self.last_string = None
            elif not char.isspace():
                self.expect_value = False
                self.last_string = None


class Preview:
    def __init__(self, schema):
        self.store, self.job_id = _active.get()
        self.call_id = uuid4().hex
        self.schema = schema
        self.projection = PublicText()
        self.last_write = 0
        self.last_cancel_check = 0
        self.deltas = 0
        self.write('receiving')

    def write(self, status):
        self.store.set_stream(self.job_id, {
            'call_id': self.call_id, 'schema': self.schema, 'status': status,
            'text': self.projection.text if status in {'receiving', 'reviewing'} else '',
            'deltas': self.deltas, 'truncated': len(self.projection.text) >= MAX_PREVIEW,
            'verified': False,
        })
        self.last_write = time.monotonic()

    def feed(self, text):
        self.check_cancel()
        self.projection.feed(text)
        self.deltas += 1
        if time.monotonic() - self.last_write >= .08:
            self.write('receiving')

    def check_cancel(self):
        now = time.monotonic()
        if now - self.last_cancel_check >= .1:
            self.store.check_cancel(self.job_id)
            self.last_cancel_check = now


async def cancellable_bytes(response, check_cancel):
    iterator = response.aiter_bytes().__aiter__()
    while True:
        check_cancel()
        pending = asyncio.create_task(iterator.__anext__())
        try:
            while not pending.done():
                await asyncio.wait({pending}, timeout=.1)
                check_cancel()
            try:
                yield pending.result()
            except StopAsyncIteration:
                return
        finally:
            if not pending.done():
                pending.cancel()
            with suppress(asyncio.CancelledError, StopAsyncIteration):
                await pending


async def bounded_lines(response, check_cancel):
    decoder = codecs.getincrementaldecoder('utf-8')()
    buffer = ''
    total = 0
    try:
        async for raw in cancellable_bytes(response, check_cancel):
            total += len(raw)
            if total > MAX_WIRE:
                raise StreamProtocolError()
            buffer += decoder.decode(raw)
            while '\n' in buffer:
                line, buffer = buffer.split('\n', 1)
                yield line.rstrip('\r')
        buffer += decoder.decode(b'', final=True)
        if buffer:
            yield buffer.rstrip('\r')
    except UnicodeError:
        raise StreamProtocolError() from None


async def receive(client, endpoint, *, headers, body, model, schema, prompt, attempt):
    """Consume actual SSE deltas without buffering the whole HTTP response first."""
    from .model_usage import tracked_stream
    preview = Preview(schema)
    content = []
    finish = None
    usage = {}
    done = False
    size = 0
    lines = []

    def event():
        nonlocal finish, usage, done, size
        if not lines:
            return
        data = '\n'.join(lines)
        lines.clear()
        if data == '[DONE]':
            done = True
            return
        try:
            part = json.loads(data)
            if not isinstance(part, dict) or part.get('error'):
                raise StreamProtocolError()
            if isinstance(part.get('usage'), dict):
                usage = part['usage']
            choices = part.get('choices', [])
            if not isinstance(choices, list) or len(choices) > 1:
                raise StreamProtocolError()
            for choice in choices:
                if choice.get('index', 0) != 0:
                    raise StreamProtocolError()
                already_finished = finish is not None
                if choice.get('finish_reason') is not None:
                    finish = choice['finish_reason']
                delta = choice.get('delta', {})
                text = delta.get('content')
                # reasoning_content, tools, provider metadata and raw JSON are
                # never projected into the browser or the preview table.
                if text is not None:
                    if not isinstance(text, str) or already_finished and text:
                        raise StreamProtocolError()
                    size += len(text.encode('utf-8'))
                    if size > MAX_CONTENT:
                        raise StreamProtocolError()
                    if text:
                        content.append(text)
                        preview.feed(text)
        except (ValueError, TypeError, KeyError, AttributeError):
            raise StreamProtocolError() from None

    try:
        async with tracked_stream(client, endpoint, headers=headers, json={**body, 'stream': True,
                'stream_options': {'include_usage': True}}, model=model, schema=schema,
                prompt=prompt, attempt=attempt) as (response, receipt):
            preview.call_id = receipt.get('call_id') or preview.call_id
            preview.write('receiving')
            if not 200 <= response.status_code < 300:
                preview.write('discarded')
                return httpx.Response(response.status_code, headers=response.headers)
            media = response.headers.get('content-type', '').lower()
            if 'application/json' in media:
                # Some compatible/local providers ignore stream=true. Accept a
                # bounded complete response without inventing a typing effect
                # or purchasing a second request.
                raw = bytearray()
                async for part in cancellable_bytes(response, preview.check_cancel):
                    raw.extend(part)
                    if len(raw) > MAX_CONTENT:
                        raise StreamProtocolError()
                try:
                    envelope = json.loads(raw)
                except ValueError:
                    raise StreamProtocolError() from None
                receipt['usage'] = envelope.get('usage', {}) if isinstance(envelope, dict) else {}
                preview.write('unavailable')
                return httpx.Response(200, content=bytes(raw), headers={'content-type': 'application/json'})
            if 'text/event-stream' not in media:
                raise StreamProtocolError()
            # Bound raw bytes before decoding UTF-8 or assembling an SSE line.
            async for line in bounded_lines(response, preview.check_cancel):
                preview.check_cancel()
                if not line:
                    event()
                    receipt['usage'] = usage
                    if done:
                        break
                elif line.startswith('data:'):
                    lines.append(line[5:].lstrip(' '))
            if lines:
                event()
            receipt['usage'] = usage
            if not done or finish != 'stop' or not content:
                raise StreamProtocolError()
        preview.write('reviewing')
        return httpx.Response(200, json={'choices': [{'finish_reason': finish,
            'message': {'content': ''.join(content)}}], 'usage': usage})
    except BaseException:
        preview.write('discarded')
        raise
