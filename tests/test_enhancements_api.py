import pymupdf
from fastapi.testclient import TestClient

from research_workspace.api import create_app
from research_workspace.discovery import DiscoveryService, ARXIV_API


def fixture_pdf():
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.insert_text((40,60),'LAYOUT AND REFERENCES TEST FIXTURE',fontsize=18)
        page.insert_text((40,100),'1 Methods\nWe use retrieval methods [1].')
        page = pdf.new_page()
        page.insert_text((40,60),'References\n[1] Alice. Retrieval Methods. 2024. arXiv:2401.12345v1.\n[2] Bob. Stellar Astronomy. 2023.')
        return pdf.tobytes()


def upload(browser,library):
    doc = browser.post('/api/documents',files={'file':('fixture.pdf',fixture_pdf())}).json()['document']
    library.process_next_job()
    return browser.get('/api/documents/'+doc['id']).json()['document']


def test_layout_and_reference_routes_pin_versions_and_export(tmp_path,monkeypatch):
    monkeypatch.setenv('RESEARCH_OCR_ENABLED','0')
    app = create_app(tmp_path,start_worker=False)
    with TestClient(app,base_url='http://127.0.0.1') as browser:
        doc = upload(browser,app.state.library)
        root = '/api/documents/'+doc['id']
        run = doc['available_run']['id']
        layout = browser.get(root+'/layout/pages/1',params={'run_id':run})
        assert layout.status_code == 200
        assert layout.json()['run_id'] == run
        assert layout.json()['source_sha256'] == doc['sha256']
        exported = browser.get(root+'/layout/pages/1/export',params={'run_id':run})
        assert exported.json() == layout.json()
        assert 'attachment' in exported.headers['content-disposition']
        assert browser.get(root+'/layout/pages/99').status_code == 400
        assert browser.get(root+'/layout/pages/1?run_id=missing').status_code == 404
        summary = browser.get(root+'/layout/summary').json()
        assert summary['run_id'] == run
        ranked = browser.get(root+'/references/ranked',params={'q':'retrieval'}).json()
        assert len(ranked['included']) == 1 and len(ranked['filtered']) == 1
        assert len(browser.get(root+'/references/ranked?q=retrieval&include_filtered=true').json()['items']) == 2
        assert browser.post(root+'/references/search',json={'reference_ids':['absent']}).status_code == 400
        assert browser.post(root+'/references/search',json={'reference_ids':['reference-1'],'max_searches':11}).status_code == 422
        newer = browser.post(root+'/parse-runs').json()['run']
        app.state.library.process_next_job()
        assert newer['id'] != run
        assert browser.get(root+'/layout/pages/1',params={'run_id':run}).json()['run_id'] == run
        assert browser.get(root+'/references/ranked',params={'run_id':run}).json()['run_id'] == run


def test_new_routes_preserve_account_isolation(tmp_path):
    app = create_app(tmp_path,start_worker=False)
    with TestClient(app,base_url='http://127.0.0.1') as owner, TestClient(app,base_url='http://127.0.0.1') as guest:
        account = owner.post('/api/account/register',json={'name':'LayoutOwner','password':'fixture-password-only'}).json()['user']
        owner.get('/api/documents')
        doc = upload(owner,app.state.libraries[account['id']])
        root = '/api/documents/'+doc['id']
        for suffix in ('/layout/pages/1','/layout/pages/1/export','/layout/summary','/references/ranked'):
            assert guest.get(root+suffix).status_code == 404
        assert guest.post(root+'/references/search',json={'reference_ids':['reference-1']}).status_code == 404


def test_explicit_reference_lookup_uses_only_selected_bibliography(tmp_path,monkeypatch):
    calls = []
    def lookup(self,references,**kwargs):
        calls.append(references)
        return {'results':[{'items':[],'source':'arxiv','source_url':ARXIV_API,
                            'retrieved_at':'2026-10-09T00:00:00+00:00','cached':False}]}
    monkeypatch.setattr(DiscoveryService,'search_references',lookup)
    app = create_app(tmp_path,start_worker=False)
    with TestClient(app,base_url='http://127.0.0.1') as browser:
        doc = upload(browser,app.state.library)
        root = '/api/documents/'+doc['id']
        browser.get(root+'/references/ranked')
        assert not calls
        result = browser.post(root+'/references/search',json={'reference_ids':['reference-1'],'max_searches':1}).json()
        assert result['requests_used'] == 1 and result['automatic_download'] is False
        assert result['results'][0]['status'] == 'not_found'
        assert calls == [['2401.12345v1']]
        assert len(browser.get('/api/documents').json()['documents']) == 1
