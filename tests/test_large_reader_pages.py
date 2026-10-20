from pathlib import Path

from fastapi.testclient import TestClient
import pymupdf

from research_workspace.api import create_app

SOURCE=Path(__file__).parent/'fixtures/book_reading/20261020-v1/oversize-native.pdf'


def test_large_original_can_render_at_lower_scale_without_relaxing_pixel_limit(tmp_path,monkeypatch):
    for key in ('RESEARCH_MODEL_BASE_URL','RESEARCH_MODEL','RESEARCH_MODEL_API_KEY'):monkeypatch.delenv(key,raising=False)
    app=create_app(tmp_path,start_worker=False)
    with TestClient(app,base_url='http://127.0.0.1:8765') as owner,TestClient(app,base_url='http://127.0.0.1:8765') as other:
        assert owner.post('/api/account/register',json={'name':'LargeReader','password':'synthetic-password-123'}).status_code==200
        uploaded=owner.post('/api/documents',files={'file':(SOURCE.name,SOURCE.read_bytes())})
        assert uploaded.status_code==202
        document=uploaded.json()['document'];prefix='/api/documents/'+document['id']+'/pages/'
        for number,scale,width,height in [(1,1,3200,4000),(2,.25,3000,4000)]:
            meta=owner.get(prefix+str(number)).json();assert meta['status']=='pending'
            assert owner.get(prefix+str(number)+'/image?scale=2').status_code==400
            response=owner.get(prefix+str(number)+'/image',params={'scale':scale})
            assert response.status_code==200 and response.headers['content-type']=='image/png'
            pix=pymupdf.Pixmap(response.content)
            assert (pix.width,pix.height)==(width,height) and pix.width*pix.height<=16000000
            assert other.get(prefix+str(number)+'/image',params={'scale':scale}).status_code==404
        assert owner.get(prefix+'3/image?scale=0.25').status_code==400
        assert owner.get(prefix+'1/image?scale=0.24').status_code==422
        assert owner.get('/api/documents/'+document['id']).json()['document']['sha256']==document['sha256']
