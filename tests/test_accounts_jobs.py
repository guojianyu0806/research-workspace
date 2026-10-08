import pytest

from research_workspace.accounts import Accounts, AuthenticationError
from research_workspace.jobs import JobStore, TaskCancelled
from research_workspace.library import Library


def test_password_session_and_workspace_isolation(tmp_path):
    accounts = Accounts(tmp_path)
    token, alice = accounts.register('Alice','test-only-secret-1')
    _, bob = accounts.register('Bob','test-only-secret-2')
    assert accounts.directory(alice) != accounts.directory(bob)
    assert accounts.resolve(token)['name']=='Alice'
    with accounts.connection() as db:
        stored = db.execute('SELECT digest FROM sessions').fetchone()[0]
    assert stored != token
    with pytest.raises(AuthenticationError):
        accounts.login('Alice','wrong-test-password')
    new_token, _ = accounts.login('Alice','test-only-secret-1')
    accounts.logout(token)
    with pytest.raises(AuthenticationError):
        accounts.resolve(token)
    assert accounts.resolve(new_token)['id']==alice['id']


def test_job_claim_cancel_recovery_and_progress(tmp_path):
    store = JobStore(Library(tmp_path))
    job = store.create('translate',{'document_id':'test-only'})
    assert store.claim()['id']==job['id']
    store.save_item(job['id'],'b1',{'block_id':'b1','translated_text':'第一段'},3)
    store.recover()
    assert store.get(job['id'])['status']=='interrupted'
    assert '重复费用' in store.get(job['id'])['error']
    assert store.claim() is None
    store.retry(job['id'])
    assert store.claim()['completed_items'][0]['translated_text']=='第一段'
    store.cancel(job['id'])
    with pytest.raises(TaskCancelled):
        store.check_cancel(job['id'])
    store.finish(job['id'],'cancelled')
    store.retry(job['id'])
    assert store.get(job['id'])['progress']==1
    assert store.get(job['id'])['status']=='queued'
