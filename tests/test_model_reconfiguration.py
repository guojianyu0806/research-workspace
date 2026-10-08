"""Concurrent local configuration tests; fixture keys, no provider or worker calls."""

import asyncio
import os
import threading

import httpx
import pytest

from research_workspace.api import create_app
from research_workspace.jobs import JobStore


FIXTURE_KEY = 'offline-fixture-not-a-real-credential'


def test_slow_sync_configuration_does_not_block_health_requests(tmp_path):
    app = create_app(tmp_path,start_worker=False)
    entered,release,finished = threading.Event(),threading.Event(),threading.Event()

    def configure(key,model):
        entered.set()
        release.wait(2)
        finished.set()

    app.state.configure_model = configure

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1:8765') as client:
            saving = asyncio.create_task(client.post('/api/model',json={'api_key':FIXTURE_KEY}))
            try:
                assert await asyncio.to_thread(entered.wait,1)
                health = await client.get('/api/health')
                assert health.status_code == 200
                assert not finished.is_set(), 'A slow settings callback blocked unrelated HTTP requests'
            finally:
                release.set()
                response = await saving
            assert response.status_code == 200
            assert FIXTURE_KEY not in response.text

    asyncio.run(scenario())


@pytest.mark.parametrize('operation',['create','retry'])
def test_enqueue_and_retry_wait_until_configuration_replacement_finishes(tmp_path,monkeypatch,operation):
    app = create_app(tmp_path,start_worker=False)
    store = JobStore(app.state.library)
    existing = None
    if operation == 'retry':
        existing = store.create('translate_text',{'text':'fixture source'})
        store.finish(existing['id'],'failed',error='fixture failure')
    entered,release = threading.Event(),threading.Event()

    class Environment(dict):
        def __setitem__(self,name,value):
            # The real callback has already checked every queue and is now about
            # to change the first setting. A concurrent enqueue here is unsafe.
            if name == 'RESEARCH_MODEL_BASE_URL' and value == 'https://api.deepseek.com':
                entered.set()
                assert release.wait(3), 'Concurrent test did not release configuration'
            super().__setitem__(name,value)

    environment = Environment(os.environ)
    dict.update(environment,RESEARCH_MODEL_BASE_URL='https://old-provider.invalid/v1',
                RESEARCH_MODEL='offline-old-model',RESEARCH_MODEL_API_KEY=FIXTURE_KEY)
    monkeypatch.setattr(os,'environ',environment)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1:8765') as client:
            saving = asyncio.create_task(client.post('/api/model',json={'api_key':FIXTURE_KEY,'model':'deepseek-v4-pro'}))
            enqueue = None
            try:
                assert await asyncio.to_thread(entered.wait,1)
                if operation == 'create':
                    enqueue = asyncio.create_task(client.post('/api/research/tasks',json={'kind':'translate_text','text':'fixture source'}))
                else:
                    enqueue = asyncio.create_task(client.post('/api/research/tasks/'+existing['id']+'/retry'))
                await asyncio.sleep(.1)
                assert not enqueue.done(), 'Task entered a queue while model settings were being replaced'
                assert all(job['status'] not in {'queued','running'} for job in store.list())
                assert (await client.get('/api/health')).status_code == 200
            finally:
                release.set()
                responses = await asyncio.gather(saving,*([enqueue] if enqueue else []))
            assert responses[0].status_code == 200
            assert responses[1].status_code == (202 if operation == 'create' else 200)
            assert responses[1].json()['job']['status'] == 'queued'
            assert environment['RESEARCH_MODEL'] == 'deepseek-v4-pro'
            assert all(FIXTURE_KEY not in response.text for response in responses)

    asyncio.run(scenario())


def test_existing_queued_task_prevents_configuration_change(tmp_path,monkeypatch):
    monkeypatch.setenv('RESEARCH_MODEL_BASE_URL','https://old-provider.invalid/v1')
    monkeypatch.setenv('RESEARCH_MODEL','offline-old-model')
    monkeypatch.setenv('RESEARCH_MODEL_API_KEY',FIXTURE_KEY)
    app = create_app(tmp_path,start_worker=False)
    JobStore(app.state.library).create('translate_text',{'text':'fixture source'})

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1:8765') as client:
            response = await client.post('/api/model',json={'api_key':FIXTURE_KEY,'model':'deepseek-v4-pro'})
            assert response.status_code == 503
            assert FIXTURE_KEY not in response.text
        assert os.environ['RESEARCH_MODEL'] == 'offline-old-model'
        assert os.environ['RESEARCH_MODEL_BASE_URL'] == 'https://old-provider.invalid/v1'

    asyncio.run(scenario())
