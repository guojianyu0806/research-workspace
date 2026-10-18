"""Offline SSE transport, checkpoint isolation and source-validation regression."""
import asyncio
import json

import httpx
import pymupdf
import pytest
from fastapi.testclient import TestClient

from research_workspace.api import create_app
from research_workspace.intelligence import (
    AnswerPayload, ChatCompletionProvider, IntelligenceService, ModelConfig,
    ModelOutputError, ModelProviderError, TranslationPayload,
)
from research_workspace.jobs import JobStore, TaskCancelled
from research_workspace.library import Library
from research_workspace.model_stream import MAX_PREVIEW, PublicText, stream_job
from research_workspace.model_usage import record_model_calls


def event(content=None, finish=None, usage=None, reasoning=None):
    delta = {}
    if content is not None: delta['content'] = content
    if reasoning is not None: delta['reasoning_content'] = reasoning
    body = {'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish}]}
    if usage is not None: body['usage'] = usage
    return ('data: ' + json.dumps(body, ensure_ascii=False) + '\r\n\r\n').encode()


class Chunks(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            if isinstance(chunk, BaseException): raise chunk
            yield chunk

    async def aclose(self): self.closed = True


def streamed(payload, *, finish='stop', done=True, extra=b''):
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=Chunks([
        b': keepalive\r\n\r\n', event(reasoning='PRIVATE_REASONING_NEVER_SAVE'),
        *[event(text[i:i+11]) for i in range(0, len(text), 11)],
        event(finish=finish, usage={'prompt_tokens': 12, 'completion_tokens': 8, 'total_tokens': 20}),
        extra, b'data: [DONE]\n\n' if done else b'',
    ]))


def provider(handler, attempts=3):
    async def no_wait(_): pass
    return ChatCompletionProvider(ModelConfig('https://fixture.invalid/v1', 'fixture', 'PRIVATE_KEY', max_attempts=attempts),
        transport=httpx.MockTransport(handler), sleep=no_wait)


def run_generate(store, job, adapter, schema=TranslationPayload):
    async def run():
        with stream_job(store, job['id']), record_model_calls(store.usage_store, job['id']):
            return await adapter.generate('PRIVATE_PROMPT', {}, schema)
    return asyncio.run(run())


def test_actual_incremental_preview_precedes_response_completion_and_receipt(tmp_path):
    store = JobStore(Library(tmp_path)); job = store.create('translate_text', {})
    async def run():
        observed = asyncio.Event(); release = asyncio.Event()
        class Paused(Chunks):
            async def __aiter__(self):
                yield event('{"translated_text":"实时')
                await asyncio.sleep(.09)
                yield event('预览')
                observed.set()
                await release.wait()
                yield event('完成。"}')
                yield event(finish='stop')
                yield b'data: {"choices":[],"usage":{"total_tokens":42}}\n\n'
                yield b'data: [DONE]\n\n'
        transport = Paused([])
        def handle(request):
            body = json.loads(request.content)
            assert body['stream'] is True and body['stream_options']['include_usage'] is True
            return httpx.Response(200, headers={'content-type':'text/event-stream'}, stream=transport)
        with stream_job(store, job['id']), record_model_calls(store.usage_store, job['id']):
            task = asyncio.create_task(provider(handle).generate('PRIVATE_PROMPT', {}, TranslationPayload))
            await asyncio.wait_for(observed.wait(), 2)
            await asyncio.sleep(0)
            preview = store.get(job['id'])['stream']
            assert preview['text'] == '实时预览' and preview['verified'] is False
            assert not task.done() and store.get(job['id'])['usage']['in_flight'] == 1
            release.set()
            assert (await task).translated_text == '实时预览完成。'
        saved = store.get(job['id'])
        assert saved['stream']['status'] == 'reviewing'
        assert saved['usage']['total_tokens'] == 42 and saved['usage']['calls'] == 1
        assert transport.closed
    asyncio.run(run())


@pytest.mark.parametrize('split', [1, 2, 7, 59])
def test_utf8_tcp_splits_escapes_crlf_usage_and_reasoning_exclusion(tmp_path, split):
    store = JobStore(Library(tmp_path)); job = store.create('translate_text', {})
    payload = {'translated_text': '中文😀\n"引号"\\根号√'}
    wire = event(reasoning='PRIVATE_REASONING_NEVER_SAVE') + event(json.dumps(payload, ensure_ascii=True)) + event(finish='stop') + b'data: [DONE]\r\n\r\n'
    def handle(_):
        return httpx.Response(200, headers={'content-type':'text/event-stream'},
            stream=Chunks([wire[i:i+split] for i in range(0,len(wire),split)]))
    result = run_generate(store, job, provider(handle))
    assert result.translated_text == payload['translated_text']
    assert store.get(job['id'])['stream']['text'] == payload['translated_text']
    saved = json.dumps(store.get(job['id']), ensure_ascii=False)
    assert all(secret not in saved for secret in ['PRIVATE_KEY','PRIVATE_PROMPT','PRIVATE_REASONING_NEVER_SAVE'])


@pytest.mark.parametrize('mode', ['missing_done','length','bad_sse','read_error','provider_error','oversize','invalid_utf8','content_after_finish'])
def test_partial_or_invalid_stream_is_never_replayed_or_a_final_answer(tmp_path, mode):
    store = JobStore(Library(tmp_path)); job = store.create('translate_text', {})
    requests = []
    def handle(request):
        requests.append(request)
        if mode == 'missing_done': return streamed({'translated_text':'待审核'}, done=False)
        if mode == 'length': return streamed({'translated_text':'待审核'}, finish='length')
        chunks = [event('{"translated_text":"待审核')]
        chunks += {
            'bad_sse': [b'data: NOT_JSON\n\n'],
            'read_error': [httpx.ReadError('PRIVATE_PROVIDER_ERROR')],
            'provider_error': [b'data: {"error":{"message":"PRIVATE_PROVIDER_ERROR"}}\n\n'],
            'oversize': [b'x' * (2*1024*1024+1)],
            'invalid_utf8': [b'\xff\n\n'],
            'content_after_finish': [event(finish='stop'),event('tail')],
        }[mode]
        return httpx.Response(200, headers={'content-type':'text/event-stream'}, stream=Chunks(chunks))
    with pytest.raises((ModelOutputError, ModelProviderError)) as error:
        run_generate(store, job, provider(handle))
    saved = store.get(job['id'])
    assert len(requests) == 1 and saved['result'] is None
    assert saved['stream']['status'] == 'discarded' and saved['stream']['text'] == ''
    assert saved['usage']['unknown_outcomes'] == 1
    assert 'PRIVATE_PROVIDER_ERROR' not in str(error.value)


def test_completed_invalid_schema_repairs_once_and_replaces_preview(tmp_path):
    store = JobStore(Library(tmp_path)); job = store.create('translate_text', {}); calls = []
    def handle(_):
        calls.append(1)
        return streamed({'translated_text':123} if len(calls)==1 else {'translated_text':'修正后的译文'})
    assert run_generate(store,job,provider(handle,attempts=2)).translated_text == '修正后的译文'
    assert store.get(job['id'])['stream']['text'] == '修正后的译文'
    assert store.get(job['id'])['usage']['calls'] == 2


@pytest.mark.parametrize('status', [401,402,429,503])
def test_stream_http_failure_uses_existing_retry_policy(tmp_path,status):
    store = JobStore(Library(tmp_path));job=store.create('translate_text',{}); calls=[]
    def handle(_):
        calls.append(1)
        return httpx.Response(status, text='PRIVATE_RESPONSE')
    with pytest.raises(ModelProviderError): run_generate(store,job,provider(handle))
    assert len(calls) == (3 if status in {429,503} else 1)
    assert store.get(job['id'])['stream']['text'] == ''


def test_idle_stream_cancellation_closes_transport_without_waiting_for_next_token(tmp_path):
    store=JobStore(Library(tmp_path));job=store.create('translate_text',{})
    async def run():
        entered=asyncio.Event()
        class Idle(Chunks):
            async def __aiter__(self):
                yield event('{"translated_text":"部分')
                entered.set()
                await asyncio.Event().wait()
        transport=Idle([])
        adapter=provider(lambda _:httpx.Response(200,headers={'content-type':'text/event-stream'},stream=transport))
        with stream_job(store,job['id']), record_model_calls(store.usage_store,job['id']):
            task=asyncio.create_task(adapter.generate('fixture',{},TranslationPayload))
            await entered.wait();store.cancel(job['id'])
            with pytest.raises(TaskCancelled): await asyncio.wait_for(task,1)
        assert transport.closed and store.get(job['id'])['stream']['text']==''
    asyncio.run(run())


def test_nonstreaming_compatible_response_is_not_faked_or_requested_twice(tmp_path):
    store=JobStore(Library(tmp_path));job=store.create('translate_text',{});calls=[]
    def handle(_):
        calls.append(1)
        return httpx.Response(200,json={'choices':[{'message':{'content':'{"translated_text":"完整返回"}'},'finish_reason':'stop'}]})
    assert run_generate(store,job,provider(handle)).translated_text=='完整返回'
    assert len(calls)==1 and store.get(job['id'])['stream']['status']=='unavailable'


def test_preview_is_bounded_and_does_not_project_quotes_identifiers_or_reasoning():
    parser=PublicText()
    text=json.dumps({'claims':[{'text':'x'*30000,'citations':[{'quote':'PRIVATE_QUOTE','evidence_id':'PRIVATE_ID'}]}],
        'reasoning_content':'PRIVATE_REASONING'})
    for char in text: parser.feed(char)
    assert parser.text=='x'*MAX_PREVIEW


def test_translation_job_clears_draft_on_completion_and_keeps_receipt(tmp_path):
    store=JobStore(Library(tmp_path));job=store.create('translate_text',{'text':'Vector search improves retrieval.','target_language':'zh'})
    adapter=provider(lambda _:streamed({'translated_text':'向量搜索改善检索。'}))
    asyncio.run(store.process_next(IntelligenceService(store.library,adapter)))
    saved=store.get(job['id'])
    assert saved['status']=='completed' and saved['result']['translated_text']=='向量搜索改善检索。'
    assert saved['stream'] is None and saved['usage']['total_tokens']==20


def test_qa_job_stream_still_validates_original_quote_and_final_audit(tmp_path):
    library=Library(tmp_path)
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((40,80),'The model uses an encoder and a decoder.')
        doc=library.import_document(pdf.tobytes(),'fixture.pdf')['document']
    library.process_next_job();store=JobStore(library)
    job=store.create('qa',{'document_ids':[doc['id']],'question':'What architecture does the model use?'})
    def handle(request):
        body=json.loads(request.content);data=json.loads(body['messages'][1]['content'])
        if data.get('task')=='verify_response_coverage':
            return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'unverified_statements':[],'missing_aspects':[]})}}]})
        item=data['evidence'][0]
        return streamed({'status':'answered','claims':[{'text':item['text'],'citations':[{'evidence_id':item['evidence_id'],'quote':item['text']}]}],'message':''})
    asyncio.run(store.process_next(IntelligenceService(library,provider(handle))))
    saved=store.get(job['id'])
    assert saved['status']=='completed',saved['error']
    result=saved['result']
    assert result['claims'][0]['text']=='The model uses an encoder and a decoder.'
    assert result['sources'][0]['document_id']==doc['id']
    assert result['sources'][0]['run_id']==library.get_document(doc['id'])['available_run']['id']
    assert result['grounding']['status']=='passed'
    assert saved['stream'] is None


def test_stream_context_isolated_across_concurrent_jobs_and_resets(tmp_path):
    stores=[JobStore(Library(tmp_path/str(i))) for i in range(2)]
    jobs=[s.create('translate_text',{'text':'Search works.','target_language':'zh'}) for s in stores]
    adapter=provider(lambda _:streamed({'translated_text':'搜索有效。'}))
    async def run():
        await asyncio.gather(*(s.process_next(IntelligenceService(s.library,adapter)) for s in stores))
    asyncio.run(run())
    for store,job in zip(stores,jobs):
        saved=store.get(job['id'])
        assert saved['status']=='completed' and saved['stream'] is None and saved['usage']['calls']==1
        assert saved['model_calls'][0]['job_id']==job['id']


def test_recovery_retry_and_export_do_not_promote_stream_draft(tmp_path,monkeypatch):
    for key in ('RESEARCH_MODEL_BASE_URL','RESEARCH_MODEL','RESEARCH_MODEL_API_KEY'):
        monkeypatch.delenv(key,raising=False)
    app=create_app(tmp_path,start_worker=False)
    with TestClient(app,base_url='http://127.0.0.1:8765') as client:
        store=JobStore(app.state.library);job=store.create('qa',{})
        store.claim();store.set_stream(job['id'],{'status':'receiving','text':'UNVERIFIED_DRAFT','verified':False})
        exported=client.get('/api/research/tasks/'+job['id']+'/export')
        assert 'UNVERIFIED_DRAFT' not in exported.text
        # Terminal stream snapshot is replayable on reconnect, account scoped.
        store.finish(job['id'],'failed',error='fixture')
        store.set_stream(job['id'],{'status':'discarded','text':'','verified':False})
        response=client.get('/api/research/tasks/'+job['id']+'/events')
        assert 'event: model_stream' in response.text and 'no-transform' in response.headers['cache-control']
        client.post('/api/account/register',json={'name':'Other','password':'fixture-test-password'})
        assert client.get('/api/research/tasks/'+job['id']+'/events').status_code==404
        store.retry(job['id']);assert store.get(job['id'])['stream']['text']==''
        store.claim();store.set_stream(job['id'],{'status':'receiving','text':'STALE_DRAFT'})
        store.recover();assert store.get(job['id'])['stream'] is None
