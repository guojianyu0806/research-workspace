"""Cancel/retry must support bounded responses without materializing saved text."""
from fastapi.testclient import TestClient
import pytest

from research_workspace.api import create_app
from research_workspace.jobs import JobStore
from scripts.freeze_long_reading import synthetic_blocks


def prepared(tmp_path, monkeypatch):
    for name in ('RESEARCH_MODEL_BASE_URL', 'RESEARCH_MODEL', 'RESEARCH_MODEL_API_KEY'):
        monkeypatch.delenv(name, raising=False)
    app = create_app(tmp_path, start_worker=False)
    store = JobStore(app.state.library)
    blocks = synthetic_blocks(120)
    job = store.create('translate', {k: blocks[0][k] for k in ('document_id', 'run_id')} | {'target_language': 'zh'})
    with store.library._connection() as db:
        import json
        db.executemany('INSERT INTO research_job_items VALUES(?,?,?)',
            [(job['id'], 'block-'+str(i), json.dumps(block)) for i, block in enumerate(blocks)])
        db.execute('UPDATE research_jobs SET progress=120,total=240 WHERE id=?', (job['id'],))
    return app, store, job['id'], blocks


@pytest.mark.parametrize('action', ['cancel', 'retry'])
def test_optional_action_summary_is_bounded_and_checkpoints_survive(tmp_path, monkeypatch, action):
    app, store, job_id, blocks = prepared(tmp_path, monkeypatch)
    if action == 'retry':
        store.cancel(job_id)
    observed = []
    original = JobStore.get
    def get(self, identity, **options):
        observed.append(options.get('include_result', True))
        return original(self, identity, **options)
    monkeypatch.setattr(JobStore, 'get', get)
    with TestClient(app, base_url='http://127.0.0.1:8765') as client:
        response = client.post(f'/api/research/tasks/{job_id}/{action}?include_result=false')
        assert response.status_code == 200
        header = response.json()['job']
        assert header['items_deferred'] and header['completed_items'] == [] and header['result'] is None
        assert header['translation_summary']['total'] == len(blocks)
        assert observed == [False]
        assert store.translation_page(job_id, limit=200)['blocks'] == blocks


@pytest.mark.parametrize('action', ['cancel', 'retry'])
def test_default_action_response_remains_compatible(tmp_path, monkeypatch, action):
    app, store, job_id, blocks = prepared(tmp_path, monkeypatch)
    if action == 'retry':
        store.cancel(job_id)
    with TestClient(app, base_url='http://127.0.0.1:8765') as client:
        response = client.post(f'/api/research/tasks/{job_id}/{action}')
        assert response.status_code == 200
        assert response.json()['job']['completed_items'] == blocks


def test_bad_summary_flag_is_validated_and_foreign_account_cannot_mutate(tmp_path, monkeypatch):
    app, store, job_id, blocks = prepared(tmp_path, monkeypatch)
    with TestClient(app, base_url='http://127.0.0.1:8765') as client:
        assert client.post(f'/api/research/tasks/{job_id}/cancel?include_result=invalid').status_code == 422
        client.post('/api/account/register', json={'name': 'LifecycleOther', 'password': 'synthetic-other-123'})
        for action in ('cancel', 'retry'):
            assert client.post(f'/api/research/tasks/{job_id}/{action}?include_result=false').status_code == 404
    assert store.get(job_id)['status'] == 'queued'
    assert store.translation_page(job_id, limit=200)['blocks'] == blocks
