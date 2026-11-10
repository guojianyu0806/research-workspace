import pymupdf
import pytest
from fastapi.testclient import TestClient

from research_workspace.api import create_app
from research_workspace.jobs import JobStore


def pdf_bytes(text):
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((40,80),text)
        return pdf.tobytes()


@pytest.fixture
def application(tmp_path,monkeypatch):
    for key in ('RESEARCH_MODEL_BASE_URL','RESEARCH_MODEL','RESEARCH_MODEL_API_KEY'):
        monkeypatch.delenv(key,raising=False)
    return create_app(tmp_path,start_worker=False)


def client(app):
    return TestClient(app,base_url='http://127.0.0.1:8765')


def test_two_accounts_cannot_read_documents_datasets_or_tasks(application):
    with client(application) as alice, client(application) as bob, client(application) as guest:
        a = alice.post('/api/account/register',json={'name':'Alice','password':'only-test-pass-123'}).json()['user']
        bob.post('/api/account/register',json={'name':'Bob','password':'only-test-pass-456'})
        uploaded = alice.post('/api/documents',files={'file':('alice.pdf',pdf_bytes('Private workspace evidence'))}).json()['document']
        data = alice.post('/api/datasets',files={'file':('alice.csv',b'x,y\n1,2')}).json()['dataset']
        library = application.state.libraries[a['id']]
        job = JobStore(library).create('qa',{'question':'private','document_ids':[uploaded['id']]})
        for other in (bob,guest):
            assert other.get('/api/documents').json()['documents']==[]
            assert other.get('/api/documents/'+uploaded['id']).status_code==404
            assert other.get('/api/datasets/'+data['id']).status_code==404
            assert other.get('/api/research/tasks/'+job['id']).status_code==404
            history = other.get('/api/research/tasks',params={'document_id':uploaded['id']}).json()
            assert history == {'jobs':[], 'documents':[]}
        assert alice.get('/api/documents/'+uploaded['id']).status_code==200
        alice.post('/api/account/logout')
        assert alice.get('/api/documents/'+uploaded['id']).status_code==404


def test_metadata_archive_restore_and_versions_api(application):
    with client(application) as browser:
        first = browser.post('/api/documents',files={'file':('1706.03762v1.pdf',pdf_bytes('one source'))}).json()['document']
        second = browser.post('/api/documents',files={'file':('1706.03762v7.pdf',pdf_bytes('another source'))}).json()['document']
        path = '/api/documents/'+first['id']
        assert browser.patch(path,json={'title':'Updated title','authors':['Test Author']}).json()['document']['title']=='Updated title'
        browser.post(path+'/archive')
        assert len(browser.get('/api/documents').json()['documents'])==1
        assert len(browser.get('/api/documents?include_archived=true').json()['documents'])==2
        browser.post(path+'/restore')
        versions = browser.get(path+'/versions').json()
        assert versions['candidates'][0]['document']['id']==second['id']
        assert browser.post(path+'/versions',json={'other_id':second['id'],'action':'use_other'}).status_code==200
        assert browser.get(path+'/versions').json()['family']['current_document_id']==second['id']


def test_chart_and_diagram_use_real_csv_data(application):
    with client(application) as browser:
        dataset = browser.post('/api/datasets',files={'file':('data.csv',b'epoch,loss\n1,0.9\n2,0.2')}).json()['dataset']
        response = browser.post('/api/datasets/'+dataset['id']+'/charts',json={'kind':'line','x':'epoch','y':['loss']})
        chart = response.json()['chart']
        assert chart['spec']['series'][0]['values']==[0.9,0.2]
        assert '<svg' in browser.get('/api/charts/'+chart['id']+'/svg').text
        assert 'open("x"' in browser.get('/api/charts/'+chart['id']+'/py').text
        diagram = browser.post('/api/diagrams',json={'nodes':[{'id':'a','label':'Input'},{'id':'b','label':'Output'}], 'edges':[{'source':'a','target':'b'}]})
        assert diagram.status_code==200 and '<svg' in diagram.text
        script = browser.post('/api/diagrams/script',json={'nodes':[{'id':'a','label':'Input'}],'edges':[]})
        assert script.status_code==200
        assert script.json()['overwrite_existing'] is False
        assert 'open("x"' in script.json()['script']
        assert browser.post('/api/diagrams/script',json={'nodes':[{'id':'a','label':'Input'}],
            'edges':[{'source':'a','target':'missing'}]}).status_code==400


def test_missing_model_does_not_enqueue_fake_result(application):
    with client(application) as browser:
        result = browser.post('/api/research/tasks',json={'kind':'qa','document_ids':['not-a-doc'],'question':'test'})
        assert result.status_code==503
        assert browser.get('/api/research/tasks').json()['jobs']==[]


def test_task_history_filters_before_limit_and_lists_archived_document_choices(application):
    with client(application) as browser:
        old_doc = browser.post('/api/documents',files={'file':('old.pdf',pdf_bytes('Old evidence'))}).json()['document']
        recent_doc = browser.post('/api/documents',files={'file':('recent.pdf',pdf_bytes('Recent evidence'))}).json()['document']
        archived = browser.post(f'/api/documents/{old_doc["id"]}/archive').json()['document']
        store = JobStore(application.state.library)
        completed = store.create('qa',{'question':' 原文\n怎样验证？ ', 'document_ids':[old_doc['id']]})
        store.finish(completed['id'],'completed',result={'answer':'private full answer'})
        cancelled = store.create('translate',{'document_id':old_doc['id']})
        store.cancel(cancelled['id'])
        for _ in range(51):
            store.create('qa',{'question':'Newer question','document_ids':[recent_doc['id']]})

        path = '/api/research/tasks'
        recent = browser.get(path).json()
        assert len(recent['jobs']) == 50
        assert all(job['document_ids'] == [recent_doc['id']] for job in recent['jobs'])
        filtered = browser.get(path,params={'document_id':old_doc['id'],'status':'completed'})
        assert filtered.status_code == 200
        assert [job['id'] for job in filtered.json()['jobs']] == [completed['id']]
        assert filtered.json()['jobs'][0]['title'] == '原文 怎样验证？'
        assert 'private full answer' not in filtered.text
        assert filtered.json()['documents'] == recent['documents']
        assert {'id':old_doc['id'],'title':old_doc['title'],'archived_at':archived['archived_at']} in recent['documents']
        assert [job['id'] for job in browser.get(path,params={'document_id':old_doc['id']}).json()['jobs']] == [
            cancelled['id'], completed['id']]
        assert [job['id'] for job in browser.get(path,params={'status':'completed'}).json()['jobs']] == [completed['id']]
        assert browser.get(path,params={'document_id':recent_doc['id'],'status':'completed'}).json()['jobs'] == []
        assert browser.get(path,params={'status':'complete'}).status_code == 422


def test_runtime_model_settings_reject_pending_work_and_do_not_persist_key(application,monkeypatch):
    # Register environment restoration before exercising the real application callback.
    for name in ('RESEARCH_MODEL_BASE_URL','RESEARCH_MODEL','RESEARCH_MODEL_API_KEY'):
        monkeypatch.setenv(name,'')
    with client(application) as browser:
        store = JobStore(application.state.library)
        job = store.create('qa',{'question':'fixture','document_ids':[]})
        payload = {'api_key':'fixture-secret-memory-only','model':'deepseek-flash'}
        assert browser.post('/api/model',json=payload).status_code == 503
        assert browser.get('/api/capabilities').json()['llm'] is False
        store.cancel(job['id'])
        saved = browser.post('/api/model',json=payload)
        assert saved.status_code == 200 and 'fixture-secret' not in saved.text
        assert browser.get('/api/capabilities').json()['llm'] is True
        assert browser.get('/api/model').json()['configured'] is True
    for file in application.state.library.data_dir.rglob('*'):
        if file.is_file():
            assert b'fixture-secret-memory-only' not in file.read_bytes()


def test_job_export_keeps_actual_version_and_partial_result(application):
    with client(application) as browser:
        store = JobStore(application.state.library)
        job = store.create('translate',{'document_id':'fixture','run_id':'original-run'})
        store.save_item(job['id'],'block-a',{'block_id':'block-a','source_text':'source','translated_text':'译文','page':1},2)
        store.finish(job['id'],'failed',error='fixture interrupted')
        exported = browser.get('/api/research/tasks/'+job['id']+'/export')
        assert 'source' in exported.text and '译文' in exported.text
        assert 'failed' in exported.text
        assert browser.post('/api/research/tasks/'+job['id']+'/retry').json()['job']['progress']==1
