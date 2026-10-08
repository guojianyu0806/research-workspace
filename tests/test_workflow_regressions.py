"""Offline workflow regressions: cancellation, restart, and workspace boundaries.

Providers here return fixture data only. No request reaches a model or arXiv.
"""

import asyncio
import json

import httpx
from fastapi.testclient import TestClient
import pymupdf
import pytest

from research_workspace.api import create_app
from research_workspace.intelligence import ChatCompletionProvider, IntelligenceService, ModelConfig
from research_workspace.jobs import JobStore
from research_workspace.library import Library


def test_cancel_during_translation_preserves_returned_block(tmp_path):
    """Cancel stops the next paid block, rather than discarding this response."""
    store = JobStore(Library(tmp_path))
    job = store.create('translate', {'document_id': 'fixture', 'run_id': 'immutable-run'})

    class Service:
        calls = 0

        def list_translation_blocks(self, *args, **kwargs):
            return {'document_id': 'fixture', 'run_id': 'immutable-run', 'missing_pages': [],
                    'blocks': [{'block_id': 'first'}, {'block_id': 'second'}]}

        async def translate_block(self, block, **kwargs):
            self.calls += 1
            store.cancel(job['id'])  # User cancelled while the request was in flight.
            return dict(block, translated_text='returned response', source_text='fixture text')

    service = Service()
    asyncio.run(store.process_next(service))
    saved = store.get(job['id'])
    assert saved['status'] == 'cancelled'
    assert service.calls == 1
    assert saved['progress'] == 1
    assert saved['completed_items'][0]['block_id'] == 'first'


def test_restart_does_not_automatically_replay_unknown_model_outcome(tmp_path, monkeypatch):
    """A crash after an external response but before commit is not safe to replay."""
    store = JobStore(Library(tmp_path))
    job = store.create('qa', {'question': 'fixture', 'document_ids': ['fixture']})

    class SimulatedProcessExit(BaseException):
        pass

    class Service:
        calls = 0

        async def answer(self, *args, **kwargs):
            self.calls += 1
            return {'answer': 'fixture answer'}

    finish = store.finish

    def interrupted_finish(job_id, status, **kwargs):
        if status == 'completed':
            raise SimulatedProcessExit()
        return finish(job_id, status, **kwargs)

    service = Service()
    monkeypatch.setattr(store, 'finish', interrupted_finish)
    with pytest.raises(SimulatedProcessExit):
        asyncio.run(store.process_next(service))
    assert store.get(job['id'])['status'] == 'running'
    monkeypatch.setattr(store, 'finish', finish)
    store.recover()
    asyncio.run(store.process_next(service))
    assert service.calls == 1, 'Recovery must require explicit retry for an unknown model-call outcome'


def test_cancel_long_text_stops_after_first_completed_segment(tmp_path):
    """Exercise the actual text splitter with an offline chat-completions transport."""
    library = Library(tmp_path)
    store = JobStore(library)
    text = 'A reproducible research sentence. ' * 400
    job = store.create('translate_text', {'text': text, 'target_language': 'en'})
    calls = []

    def handler(request):
        payload = json.loads(json.loads(request.content)['messages'][1]['content'])
        calls.append(payload['segment'])
        if len(calls) == 1:
            store.cancel(job['id'])
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop',
            'message': {'content': json.dumps({'translated_text': payload['text']})}}]})

    provider = ChatCompletionProvider(ModelConfig(base_url='https://model.invalid/v1',
        model='offline-fixture', api_key='test-only-value'), transport=httpx.MockTransport(handler))
    asyncio.run(store.process_next(IntelligenceService(library, provider)))
    saved = store.get(job['id'])
    assert saved['status'] == 'cancelled'
    assert calls == [1], 'Cancellation must stop later model segments'
    assert saved['progress'] == 1


def test_foreign_task_mutations_streams_and_exports_are_rejected(tmp_path):
    """Possession of another account's task ID grants no task operations."""
    app = create_app(tmp_path, start_worker=False)
    with TestClient(app, base_url='http://127.0.0.1:8765') as owner, \
            TestClient(app, base_url='http://127.0.0.1:8765') as other:
        user = owner.post('/api/account/register', json={
            'name': 'Owner', 'password': 'offline-owner-password'}).json()['user']
        other.post('/api/account/register', json={
            'name': 'Other', 'password': 'offline-other-password'})
        owner.get('/api/documents')
        store = JobStore(app.state.libraries[user['id']])
        job = store.create('qa', {'question': 'private question', 'document_ids': []})
        store.finish(job['id'], 'failed', error='offline fixture')
        base = '/api/research/tasks/' + job['id']
        for path in ('', '/events', '/export'):
            response = other.get(base + path)
            assert response.status_code == 404
            assert 'private question' not in response.text
        for path in ('/cancel', '/retry'):
            assert other.post(base + path).status_code == 404
        assert store.get(job['id'])['status'] == 'failed'


@pytest.mark.parametrize('kind', ['qa', 'review'])
def test_cancel_racing_single_call_retains_result_and_retry_reuses_it(tmp_path, kind):
    store = JobStore(Library(tmp_path))
    job = store.create(kind, {'question': 'fixture', 'document_ids': ['fixture']})

    class Service:
        calls = 0

        async def answer(self, *args, **kwargs):
            self.calls += 1
            store.cancel(job['id'])
            return {'answer': 'completed fixture'}

        synthesize = answer

    service = Service()
    asyncio.run(store.process_next(service))
    saved = store.get(job['id'])
    assert saved['status'] == 'cancelled'
    assert saved['result']['answer'] == 'completed fixture'
    store.retry(job['id'])
    asyncio.run(store.process_next(service))
    assert service.calls == 1
    assert store.get(job['id'])['status'] == 'completed'


def test_long_text_retry_resumes_saved_segments_with_protected_spans(tmp_path):
    library = Library(tmp_path)
    store = JobStore(library)
    text = ('Evidence context. ' * 230) + 'Value 12.5 [9] and `a = 7`. ' + ('Closing sentence. ' * 400)
    job = store.create('translate_text', {'text': text, 'target_language': 'en'})
    calls = []

    def handler(request):
        payload = json.loads(json.loads(request.content)['messages'][1]['content'])
        calls.append(payload['segment'])
        if len(calls) == 1:
            store.cancel(job['id'])
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop',
            'message': {'content': json.dumps({'translated_text': payload['text']})}}]})

    provider = ChatCompletionProvider(ModelConfig(base_url='https://model.invalid/v1',
        model='offline-fixture', api_key='test-only-value'), transport=httpx.MockTransport(handler))
    service = IntelligenceService(library, provider)
    asyncio.run(store.process_next(service))
    assert store.get(job['id'])['progress'] == 1
    store.retry(job['id'])
    for _ in range(10):
        previous = len(calls)
        if not asyncio.run(store.process_next(service)):
            break
        assert len(calls) - previous <= 1
    saved = store.get(job['id'])
    assert saved['status'] == 'completed'
    assert calls == list(range(1, saved['total'] + 1))
    assert ''.join(part['source_text'] for part in saved['result']['segments']) == text
    assert '12.5 [9] and `a = 7`' in saved['result']['translated_text']


def test_one_block_step_yields_to_older_queued_job(tmp_path):
    store = JobStore(Library(tmp_path))
    translation = store.create('translate', {'document_id': 'fixture'})
    question = store.create('qa', {'question': 'fixture', 'document_ids': ['fixture']})
    calls = []

    class Service:
        def list_translation_blocks(self, *args, **kwargs):
            return {'missing_pages': [], 'blocks': [{'block_id': 'one'}, {'block_id': 'two'}]}

        async def translate_block(self, block, **kwargs):
            calls.append(block['block_id'])
            return dict(block, translated_text='fixture')

        async def answer(self, *args, **kwargs):
            calls.append('question')
            return {'answer': 'fixture'}

    service = Service()
    asyncio.run(store.process_next(service))
    assert store.get(translation['id'])['progress'] == 1
    assert store.get(translation['id'])['status'] == 'queued'
    asyncio.run(store.process_next(service))
    assert store.get(question['id'])['status'] == 'completed'
    asyncio.run(store.process_next(service))
    assert calls == ['one', 'question', 'two']
    assert store.get(translation['id'])['status'] == 'completed'


def test_summaries_do_not_copy_task_inputs_or_translation_results(tmp_path):
    store = JobStore(Library(tmp_path))
    job = store.create('translate_text', {'text': 'PRIVATE_LONG_INPUT' * 1000})
    store.save_item(job['id'], 'segment:1', {'segment': 1, 'translated_text': 'PAID_LONG_OUTPUT' * 1000}, 1)
    store.finish(job['id'], 'completed', result={'translated_text': 'FULL_RESULT' * 1000})
    for summary in (store.summary(job['id']), store.list()[0]):
        assert summary['has_result'] is True and summary['progress'] == 1
        assert not {'payload', 'result', 'completed_items', 'events'} & summary.keys()
        assert len(json.dumps(summary)) < 2000
    assert store.get(job['id'])['result']['translated_text'].startswith('FULL_RESULT')


def test_selected_text_is_revalidated_and_only_selected_source_is_translated(tmp_path):
    library = Library(tmp_path)
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((40, 80), 'First sentence. Selected 42 results. Final sentence.')
        document = library.import_document(pdf.tobytes(), 'selected.pdf')['document']
    library.process_next_job()
    seen = []

    def handler(request):
        payload = json.loads(json.loads(request.content)['messages'][1]['content'])
        seen.append(payload['text'])
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop',
            'message': {'content': json.dumps({'translated_text': payload['text']})}}]})

    provider = ChatCompletionProvider(ModelConfig(base_url='https://model.invalid/v1',
        model='offline-fixture', api_key='test-only-value'), transport=httpx.MockTransport(handler))
    service = IntelligenceService(library, provider)
    block = service.list_translation_blocks(document['id'])['blocks'][0]
    store = JobStore(library)
    payload = {'document_id': document['id'], 'run_id': block['run_id'],
               'block_ids': [block['block_id']], 'selected_text': 'Selected 42 results.'}
    job = store.create('translate', payload)
    asyncio.run(store.process_next(service))
    result = store.get(job['id'])['result']['blocks'][0]
    assert result['source_text'] == result['translated_text'] == 'Selected 42 results.'
    assert result['run_id'] == block['run_id'] and result['bbox'] == block['bbox']
    assert 'First sentence.' not in seen[0]
    invalid = store.create('translate', dict(payload, selected_text='Text outside the real source.'))
    asyncio.run(store.process_next(service))
    assert store.get(invalid['id'])['status'] == 'failed'
    assert len(seen) == 1


def test_worker_tick_visits_subscriptions_and_next_account_between_blocks(tmp_path, monkeypatch):
    from research_workspace import activity_worker, intelligence

    libraries = [Library(tmp_path / name) for name in ('alice', 'bob')]
    events = []

    class Service:
        def __init__(self, library):
            self.name = library.data_dir.name

        def list_translation_blocks(self, *args, **kwargs):
            return {'missing_pages': [], 'blocks': [{'block_id': 'one'}, {'block_id': 'two'}]}

        async def translate_block(self, block, **kwargs):
            events.append((self.name, block['block_id']))
            return dict(block, translated_text='fixture')

    class Discovery:
        def __init__(self, library):
            self.name = library.data_dir.name

        def due_checks(self, *, limit):
            events.append((self.name, 'subscription'))
            return []

    monkeypatch.setattr(intelligence, 'IntelligenceService', Service)
    monkeypatch.setattr(activity_worker, 'DiscoveryService', Discovery)
    for library in libraries:
        JobStore(library).create('translate', {'document_id': 'fixture'})
        activity_worker.workspace_tick(library)
    assert events == [('alice', 'one'), ('alice', 'subscription'),
                      ('bob', 'one'), ('bob', 'subscription')]
