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


def test_history_metadata_uses_saved_scope_and_keeps_private_data_in_details(tmp_path):
    store = JobStore(Library(tmp_path))
    question = ' 研究\n' + '文' * 120 + 'PRIVATE_QUESTION_TAIL'
    job = store.create('agent_research', {
        'question': question,
        'document_ids': ['unverified-selection'],
        'agent_scope': {'document_ids': ['selected-document']},
        'source_task_id': 'source-task',
    })
    assert job['title'] == '研究 ' + '文' * 69 + '…'
    assert job['document_ids'] == ['selected-document']

    store.save_agent_state(job['id'], {
        'run_ids': {'selected-document': 'original-run', 'parsed-import': 'imported-run'},
        'imported': {'one': {'document_id': 'parsed-import'}, 'two': {'document_id': 'not-yet-parsed'}},
        'messages': [{'content': 'PRIVATE_AGENT_PROMPT'}],
    })
    store.finish(job['id'],'completed',result={'answer':'PRIVATE_MODEL_OUTPUT'})
    assert store.list(document_id='unverified-selection') == []
    listed = store.list(document_id='not-yet-parsed')
    assert len(listed) == 1 and listed[0]['id'] == job['id']
    for summary in (listed[0], store.summary(job['id'])):
        assert summary['title'] == job['title']
        assert summary['document_ids'] == ['selected-document','parsed-import','not-yet-parsed']
        assert summary['source_task_id'] == 'source-task'
        assert not {'payload','result'} & summary.keys()
        assert not any(key.startswith('history_') for key in summary)
        assert 'PRIVATE_' not in str(summary)
    assert store.header(job['id'])['document_ids'] == listed[0]['document_ids']
    assert store.get(job['id'])['title'] == listed[0]['title']
    stream_status = store.summary(job['id'], include_stage_event=True, include_history=False)
    assert stream_status['status'] == 'completed'
    assert not {'title','document_ids'} & stream_status.keys()


def test_history_metadata_follows_discovery_checkpoint_and_future_source(tmp_path):
    store = JobStore(Library(tmp_path))
    discovery = store.create('discover_qa',{'paper_id':'public-record','question':'What does the paper show?'})
    assert discovery['document_ids'] == []
    store.save_item(discovery['id'],'download',{'document_id':'public-import'},3)
    assert store.list(document_id='public-import')[0]['id'] == discovery['id']

    parent = store.create('future_work',{'document_ids':['scope-document','scope-document'],
                                        'instruction':'  下一步\n如何验证？  '})
    child = store.create('future_filter',{'source_task_id':parent['id']})
    repeated = store.create('future_filter',{'source_task_id':child['id']})
    assert [job['id'] for job in store.list(document_id='scope-document')] == [
        repeated['id'],child['id'],parent['id']]
    assert child['document_ids'] == ['scope-document']
    assert child['title'] is None
    assert parent['title'] == '下一步 如何验证？'
    translation = store.create('translate_text',{'instruction':'private','text':'private text'})
    assert translation['title'] is None
