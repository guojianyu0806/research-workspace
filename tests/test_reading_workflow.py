"""Bounded pipeline checkpoints with fixture PDF/model, never paid requests."""
import asyncio
import json

import httpx
import pymupdf
import pytest

from research_workspace.discovery import DiscoveryService,DiscoveryError
from research_workspace.intelligence import IntelligenceService,ChatCompletionProvider,ModelConfig
from research_workspace.jobs import JobStore
from research_workspace.library import Library,LibraryError


def setup(tmp_path,monkeypatch):
    library=Library(tmp_path);store=JobStore(library);downloads=[];calls=[]
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((30,70),'The encoder has 6 layers.');data=pdf.tobytes()
    def download(service,paper_id):
        downloads.append(paper_id)
        return {**library.import_document(data,'fixture.pdf'),'source':{'paper_id':paper_id,'url':'https://arxiv.org/pdf/2401.12345v1'}}
    monkeypatch.setattr(DiscoveryService,'import_paper',download)
    def model(request):
        calls.append(1);payload=json.loads(json.loads(request.content)['messages'][1]['content'])
        if payload.get('task')=='verify_response_coverage':result={'unverified_statements':[],'missing_aspects':[]}
        else:
            block=payload['evidence'][0];result={'status':'answered','message':'','claims':[{'text':block['text'],'citations':[{'evidence_id':block['evidence_id'],'quote':block['text']}]}]}
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(result)}}]})
    service=IntelligenceService(library,ChatCompletionProvider(ModelConfig('https://model.invalid','fixture-model','fixture-only-key'),transport=httpx.MockTransport(model)))
    job=store.create('discover_qa',{'paper_id':'arxiv:2401.12345v1','question':'How many encoder layers?'})
    return library,store,service,job,downloads,calls


def advance(store,service):
    with store.library._connection() as db:db.execute("UPDATE research_jobs SET updated_at='2000-01-01T00:00:00+00:00' WHERE status='queued'")
    return asyncio.run(store.process_next(service))


def test_pipeline_waits_then_pins_parse_and_reuses_saved_answer_without_paid_replay(tmp_path,monkeypatch):
    library,store,service,job,downloads,calls=setup(tmp_path,monkeypatch)
    advance(store,service)
    assert len(downloads)==1 and calls==[] and store.claim() is None
    advance(store,service)
    assert store.get(job['id'])['status']=='queued' and '等待原文解析' in store.get(job['id'])['events'][-1]['message']
    library.process_next_job();advance(store,service)
    parsed=next(x for x in store.get(job['id'])['completed_items'] if x['stage']=='parse')
    # A newer parse becomes available; answer must keep the checkpoint version.
    library.create_run(parsed['document_id']);library.process_next_job()
    advance(store,service)
    saved=store.get(job['id'])
    assert saved['status']=='completed' and saved['progress']==3 and len(downloads)==1 and calls
    assert {s['run_id'] for s in saved['result']['sources']}=={parsed['run_id']}
    count=len(calls)
    # Simulate interruption after paid answer checkpoint but before final commit.
    with library._connection() as db:db.execute("UPDATE research_jobs SET status='queued',result=NULL WHERE id=?",(job['id'],))
    advance(store,service)
    assert len(calls)==count and store.get(job['id'])['status']=='completed'


def test_cancel_after_download_prevents_parsing_answer_and_retains_file(tmp_path,monkeypatch):
    library,store,service,job,downloads,calls=setup(tmp_path,monkeypatch)
    advance(store,service);store.cancel(job['id'])
    assert advance(store,service) is False and calls==[]
    saved=store.get(job['id']);assert saved['status']=='cancelled'
    assert library.get_document(saved['completed_items'][0]['document_id'])


def test_download_failure_is_explicit_and_retry_uses_same_task(tmp_path,monkeypatch):
    library,store,service,job,downloads,calls=setup(tmp_path,monkeypatch)
    original=DiscoveryService.import_paper
    def fail(*args):raise DiscoveryError('公开来源连接超时，请稍后重试。')
    monkeypatch.setattr(DiscoveryService,'import_paper',fail)
    advance(store,service);saved=store.get(job['id'])
    assert saved['status']=='failed' and saved['completed_items']==[] and calls==[]
    monkeypatch.setattr(DiscoveryService,'import_paper',original);store.retry(job['id']);advance(store,service)
    assert len(downloads)==1 and store.get(job['id'])['progress']==1


def test_pinned_run_cannot_borrow_another_documents_blocks(tmp_path,monkeypatch):
    library,store,service,job,downloads,calls=setup(tmp_path,monkeypatch)
    advance(store,service);library.process_next_job()
    first=library.list_documents()[0]
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((30,70),'Other unrelated document.');other=library.import_document(pdf.tobytes(),'other.pdf')['document']
    library.process_next_job()
    with pytest.raises(LibraryError):
        service._blocks([first['id']],run_ids={first['id']:library.get_document(other['id'])['available_run']['id']})


def test_workflow_api_validates_public_source_and_workspace_before_queue(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from research_workspace.api import create_app
    monkeypatch.setenv('RESEARCH_MODEL_BASE_URL','https://model.invalid')
    monkeypatch.setenv('RESEARCH_MODEL','fixture-model')
    monkeypatch.setenv('RESEARCH_MODEL_API_KEY','fixture-not-real')
    app=create_app(tmp_path,start_worker=False)
    with TestClient(app,base_url='http://127.0.0.1:8765') as alice,TestClient(app,base_url='http://127.0.0.1:8765') as bob:
        user=alice.post('/api/account/register',json={'name':'FlowAlice','password':'fixture-account-pass-1'}).json()['user']
        bob.post('/api/account/register',json={'name':'FlowBob','password':'fixture-account-pass-2'})
        alice.get('/api/documents');service=DiscoveryService(app.state.libraries[user['id']])
        paper={'id':'arxiv:2401.12345v1','arxiv_id':'2401.12345v1','canonical_id':'2401.12345','title':'Fixture Paper','version':1,'pdf_url':'https://arxiv.org/pdf/2401.12345v1'}
        with service._db() as db:db.execute('INSERT INTO papers VALUES(?,?,?,?,?,?)',(paper['id'],paper['canonical_id'],1,json.dumps(paper),'2026-01-01','2026-01-01'))
        body={'kind':'discover_qa','paper_id':paper['id'],'question':'How many layers?'}
        assert bob.post('/api/research/tasks',json=body).status_code==404
        created=alice.post('/api/research/tasks',json=body)
        assert created.status_code==202 and created.json()['job']['kind']=='discover_qa'
        assert alice.post('/api/research/tasks',json={**body,'question':''}).status_code==400
        identifier=created.json()['job']['id']
        assert bob.get('/api/research/tasks/'+identifier).status_code==404
