"""Explicit fixture responses; actual public checks recorded separately."""
import base64
import json
from datetime import datetime,timedelta,timezone

import httpx
import pytest

from research_workspace.code_verification import repository_name,verify_repository
from research_workspace.discovery import DiscoveryService
from research_workspace.library import Library


PAPER={'id':'arxiv:2401.12345v1','canonical_id':'2401.12345','arxiv_id':'2401.12345v1','version':1,
    'source_url':'https://arxiv.org/abs/2401.12345v1',
    'title':'Evidence Retrieval Methods for Scientific Research','summary':'Evidence retrieval',
    'code_links':[{'url':'https://github.com/example/research','verification':'unverified_metadata_link'}]}


def transport(association=True,license='MIT',status=200,redirect=False):
    def handle(request):
        assert request.url.host=='api.github.com' and 'authorization' not in request.headers
        if redirect:return httpx.Response(302,headers={'Location':'http://127.0.0.1/private'})
        if request.url.path.endswith('/readme'):
            text='Paper https://arxiv.org/abs/2401.12345v1' if association else 'A different unrelated paper.'
            data={'encoding':'base64','size':len(text),'content':base64.b64encode(text.encode()).decode(),'sha':'readme-sha'}
        elif '/git/trees/' in request.url.path:data={'sha':'tree-sha','truncated':False,'tree':[{'type':'blob','path':'src/train.py'}]}
        else:data={'full_name':'example/research','private':False,'default_branch':'main','license':{'spdx_id':license}}
        return httpx.Response(status,json=data)
    return httpx.Client(transport=httpx.MockTransport(handle))


@pytest.mark.parametrize('url',['http://github.com/a/b','https://github.com/a/b/extra','https://github.com.evil/a/b',
    'https://user:secret@github.com/a/b','https://github.com/a/b?token=secret','https://github.com/a/../b','https://127.0.0.1/a/b'])
def test_repository_urls_reject_untrusted_destinations(url):assert repository_name(url) is None


def test_bidirectional_identity_license_and_source_are_distinct_from_reproduction():
    result=verify_repository(PAPER,PAPER['code_links'][0]['url'],transport())
    assert result['open_source_verified'] and result['association_basis']=='arxiv_identifier'
    assert result['repository_sha']=='tree-sha' and result['source_file_examples']==['src/train.py']
    assert result['reproduction_verified'] is False
    assert not verify_repository(PAPER,PAPER['code_links'][0]['url'],transport(association=False))['open_source_verified']
    assert verify_repository(PAPER,PAPER['code_links'][0]['url'],transport(license='NOASSERTION'))['verification']=='association_only'
    assert verify_repository(PAPER,PAPER['code_links'][0]['url'],transport(license='BUSL-1.1'))['verification']=='association_only'
    assert not verify_repository(PAPER,PAPER['code_links'][0]['url'],transport(redirect=True))['open_source_verified']
    assert not verify_repository(PAPER,PAPER['code_links'][0]['url'],transport(status=429))['open_source_verified']


def put(service,paper):
    with service._db() as db:db.execute('INSERT INTO papers VALUES(?,?,?,?,?,?)',
        (paper['id'],paper['canonical_id'],paper['version'],json.dumps(paper),'2026-01-01','2026-01-01'))


def test_ranking_uses_only_fresh_verified_code_and_keeps_workspace_boundary(tmp_path):
    service=DiscoveryService(Library(tmp_path/'a'),transport(),request_interval=0)
    put(service,PAPER)
    other={**PAPER,'id':'arxiv:2401.12346v1','canonical_id':'2401.12346','code_links':[]}
    put(service,other)
    before=service.recommendations(keywords='evidence retrieval')['items']
    assert before[0]['score']==before[1]['score']
    assert service.verify_code(PAPER['id'])['verified_code']
    after=service.recommendations(keywords='evidence retrieval')['items']
    assert after[0]['id']==PAPER['id'] and after[0]['score']>after[1]['score']
    assert DiscoveryService(Library(tmp_path/'b'),transport()).list_papers()==[]
    with service._db() as db:
        row=db.execute('SELECT id,record FROM paper_code_checks').fetchone();record=json.loads(row['record'])
        record['checked_at']=(datetime.now(timezone.utc)-timedelta(hours=25)).isoformat()
        db.execute('UPDATE paper_code_checks SET record=? WHERE id=?',(json.dumps(record),row['id']))
    assert service.get_paper(PAPER['id'])['verified_code'] is False
    assert service.verify_code(PAPER['id'])['verified_code']
    with service._db() as db:assert db.execute('SELECT COUNT(*) FROM paper_code_checks').fetchone()[0]==2


def test_downloaded_original_pdf_supplies_code_hint_with_frozen_source(tmp_path):
    import pymupdf
    service=DiscoveryService(Library(tmp_path),transport(),request_interval=0)
    paper={**PAPER,'code_links':[]};put(service,paper)
    with pymupdf.open() as pdf:
        page=pdf.new_page();page.insert_text((30,70),'Code: https://github.com/example/\nresearch')
        page.insert_link({'kind':pymupdf.LINK_URI,'from':pymupdf.Rect(30,90,100,110),'uri':'https://github.com/example/second'})
        doc=service.library.import_document(pdf.tobytes(),'source.pdf')['document']
    while service.library.process_next_job():pass
    with service._db() as db:db.execute('INSERT INTO imports VALUES(?,?,?,?)',(paper['id'],doc['id'],'2026-01-01','https://arxiv.org/pdf/2401.12345v1'))
    found=service.get_paper(paper['id'])
    assert found['code_links'][0]['source_evidence']['run_id']==service.library.get_document(doc['id'])['available_run']['id']
    assert found['code_links'][0]['source_type']=='original_pdf' and not found['verified_code']
    assert any(x['source_type']=='pdf_link_annotation' and x['source_evidence']['annotation_uri'].endswith('/second') for x in found['code_links'])
    assert service.verify_code(paper['id'])['verified_code']


def test_code_check_endpoint_cannot_read_another_accounts_paper(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from research_workspace.api import create_app
    import research_workspace.code_verification as module
    original=module.verify_repository
    monkeypatch.setattr(module,'verify_repository',lambda paper,url,client=None:original(paper,url,transport()))
    app=create_app(tmp_path,start_worker=False)
    with TestClient(app,base_url='http://127.0.0.1:8765') as alice,TestClient(app,base_url='http://127.0.0.1:8765') as bob:
        user=alice.post('/api/account/register',json={'name':'CodeAlice','password':'fixture-only-pass-123'}).json()['user']
        bob.post('/api/account/register',json={'name':'CodeBob','password':'fixture-only-pass-456'})
        alice.get('/api/documents')
        service=DiscoveryService(app.state.libraries[user['id']]);put(service,PAPER)
        path='/api/discover/code/'+PAPER['id']
        assert bob.post(path,json={}).status_code==404
        response=alice.post(path,json={})
        assert response.status_code==200 and response.json()['paper']['verified_code']
