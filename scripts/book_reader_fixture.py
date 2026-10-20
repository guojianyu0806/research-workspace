"""Real worker crash/recovery and complete book provenance on an isolated API."""
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import sqlite3
import subprocess
import sys
import threading
import time

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.freeze_local_acceptance import main_snapshot,sha256
from scripts.retained_process_tree import OwnedTree


def run():
    output=ROOT/'artifacts/book-reader'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output.mkdir(parents=True,exist_ok=False)
    manifest_path=ROOT/'tests/fixtures/book_reading/20261020-v1/manifest.json'
    manifest=json.loads(manifest_path.read_text(encoding='utf-8'));before=main_snapshot()
    names=[str(p.relative_to(ROOT)).replace('\\','/') for p in [*(ROOT/'research_workspace').rglob('*.py'),*(ROOT/'research_workspace/static').glob('*.js'),*(ROOT/'research_workspace/static').glob('*.css')]]
    names+=['research_workspace/static/index.html','scripts/book_reader_fixture.py',str(manifest_path.relative_to(ROOT)).replace('\\','/')]
    hashes={name:sha256(ROOT/name) for name in names}
    (output/'start.json').write_text(json.dumps(dict(pid=os.getpid(),runtime_sha256=hashes,main_before=before,
        source_revision=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()),ensure_ascii=False,indent=2),encoding='utf-8')
    report=dict(checks=[],errors=[],workers=[],pages=[],scope='Real original PDF/worker/API. Native coverage and source integrity, not semantic parsing accuracy.')
    def check(name,passed,detail=None):
        report['checks'].append(dict(name=name,passed=bool(passed),detail=detail))
        if not passed:raise AssertionError(name)
    for name in ('RESEARCH_MODEL_BASE_URL','RESEARCH_MODEL','RESEARCH_MODEL_API_KEY'):os.environ.pop(name,None)
    os.environ.update(RESEARCH_SEMANTIC_ENABLED='auto',RESEARCH_OCR_TESSDATA=str(ROOT/'data/ocr/tessdata'),RESEARCH_OCR_LANG='eng+chi_sim',HF_HUB_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1')
    guard=output/'network-guard';guard.mkdir()
    (guard/'sitecustomize.py').write_text('''import socket
_connect=socket.socket.connect
def guarded(self,address):
    if self.family in (socket.AF_INET,socket.AF_INET6) and address[0] not in ('127.0.0.1','::1','localhost'):raise OSError('External networking disabled for book acceptance')
    return _connect(self,address)
socket.socket.connect=guarded
''',encoding='utf-8')
    os.environ['PYTHONPATH']=str(guard)+os.pathsep+str(ROOT)
    sys.path.insert(0,str(guard));import sitecustomize
    from research_workspace.api import create_app
    from research_workspace.worker_process import start_worker_process,stop_worker
    from research_workspace.jobs import JobStore
    import httpx
    import uvicorn
    app=create_app(output/'workspace',start_worker=False)
    sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(128)
    origin=f'http://127.0.0.1:{sock.getsockname()[1]}'
    server=uvicorn.Server(uvicorn.Config(app,log_level='error'));thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True)
    processes=[];library=None
    def launch():
        log=(output/f'worker-{len(processes)+1}.log').open('ab')
        process=start_worker_process([sys.executable,'-m','research_workspace.worker','--data-dir',str(library.data_dir),
            '--parent-pid',str(os.getpid()),'--poll-interval','0.05'],cwd=ROOT,stdin=subprocess.DEVNULL,stdout=log,stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        observed=OwnedTree(process);observed.refresh();processes.append((process,observed,log));return process,observed
    def stop(process,observed):
        observed.refresh();stop_worker(process)
        check('owned worker actually exited '+str(process.pid),not observed.living())
        report['workers'].append(observed.report())
    def snapshot(run_id):
        with library._connection() as db:
            return {table:[tuple(row) for row in db.execute('SELECT * FROM '+table+' WHERE run_id=? ORDER BY '+('page' if table=='pages' else 'page,ordinal'),(run_id,))] for table in ('pages','blocks')}
    try:
        thread.start();deadline=time.monotonic()+15
        while not server.started:
            if time.monotonic()>deadline:raise RuntimeError('Book API startup timed out')
            time.sleep(.02)
        with httpx.Client(base_url=origin,timeout=1800,trust_env=False) as owner,httpx.Client(base_url=origin,timeout=30,trust_env=False) as other:
            registered=owner.post('/api/account/register',json={'name':'BookReaderAlice','password':'local-book-reader-123'})
            check('synthetic owner registered',registered.status_code==200)
            check('synthetic other registered',other.post('/api/account/register',json={'name':'BookReaderBob','password':'local-book-reader-456'}).status_code==200)
            source=ROOT/manifest['original']['path'];check('original book SHA',sha256(source)==manifest['original']['sha256'])
            with source.open('rb') as stream:uploaded=owner.post('/api/documents',files={'file':(source.name,stream,'application/pdf')})
            check('book queued by actual HTTP',uploaded.status_code==202)
            document=uploaded.json()['document'];did=document['id'];rid=document['current_run']['id']
            library=app.state.libraries[registered.json()['user']['id']]
            first,first_tree=launch();deadline=time.monotonic()+180
            while True:
                value=owner.get('/api/jobs/'+rid).json()['run']
                if value['pages_done']>=160:break
                if first.poll() is not None or time.monotonic()>deadline:raise RuntimeError('Book did not reach middle checkpoint')
                time.sleep(.025)
            check('observed true middle before crash',160<=value['pages_done']<500,value)
            stop(first,first_tree);checkpoint=snapshot(rid)
            check('crash left unfinished persisted run',len(checkpoint['pages'])<541)
            (output/'checkpoint.json').write_text(json.dumps(checkpoint,ensure_ascii=False),encoding='utf-8')
            print(json.dumps(dict(event='book-crashed',output=str(output),pages=len(checkpoint['pages']))),flush=True)
            second,second_tree=launch();deadline=time.monotonic()+300
            while True:
                value=owner.get('/api/jobs/'+rid).json()['run']
                if value['status'] not in ('running','queued'):break
                if second.poll() is not None or time.monotonic()>deadline:raise RuntimeError('Book recovery timed out')
                time.sleep(.05)
            check('same run fully recovered',value['id']==rid and value['pages_done']==541 and value['text_pages']==537 and value['no_text_pages']==4 and value['failed_pages']==0,value)
            complete=snapshot(rid);checkpoint_numbers={row[1] for row in checkpoint['pages']}
            check('all committed checkpoint page rows unchanged',[row for row in complete['pages'] if row[1] in checkpoint_numbers]==checkpoint['pages'])
            # blocks schema: id,run_id,page,ordinal,text,search_text,bbox.
            check('all committed checkpoint block IDs and text unchanged',[row for row in complete['blocks'] if row[2] in checkpoint_numbers]==checkpoint['blocks'])
            def verify_pages(run_id):
                for observation in manifest['original']['pages']:
                    number=observation['page'];response=owner.get(f'/api/documents/{did}/pages/{number}',params={'run_id':run_id})
                    check(f'HTTP run {run_id[:6]} physical page {number}',response.status_code==200)
                    page=response.json();blocks=page['blocks']
                    check(f'coverage and geometry {number}',bool(blocks)==observation['native_has_text'] and page['width']==observation['width'] and page['height']==observation['height'] and page['rotation']==observation['rotation'])
                    if blocks:
                        selected=max(blocks,key=lambda block:len(block['text']));query=selected['text'].strip()[:80]
                        hits=owner.get('/api/search',params={'q':query,'document_id':did,'run_id':run_id,'limit':100}).json()['hits']
                        check(f'original locator and searchable text {number}',any(hit['block_id']==selected['id'] and hit['page']==number and hit['run_id']==run_id and hit['bbox']==selected['bbox'] for hit in hits))
                    if run_id==rid:report['pages'].append(dict(page=number,status=page['status'],blocks=len(blocks)))
                print(json.dumps(dict(event='all-pages-checked',run_id=run_id,pages=541)),flush=True)
            verify_pages(rid)
            retry=owner.post(f'/api/documents/{did}/pages/73/retry',params={'run_id':rid})
            check('single-page retry accepted',retry.status_code==200)
            new_run=retry.json()['run'];new_id=new_run['id'];deadline=time.monotonic()+60
            while True:
                value=owner.get('/api/jobs/'+new_id).json()['run']
                if value['status'] not in ('queued','running'):break
                if time.monotonic()>deadline:raise RuntimeError('Single-page retry timed out')
                time.sleep(.03)
            check('retry completed with all copied pages',value['pages_done']==541 and value['parent_run_id']==rid and value['target_pages']==[73],value)
            check('historical full run never rewritten',snapshot(rid)==complete)
            verify_pages(new_id)
            with library._connection() as db:
                rows=db.execute('SELECT id,page,ordinal,text,search_text,bbox FROM blocks WHERE run_id=? ORDER BY page,ordinal',(new_id,)).fetchall()
                check('all normalized search records exact',all(row['search_text']==' '.join(row['text'].casefold().split()) for row in rows))
                check('all new source IDs unique',len(rows)==len({row['id'] for row in rows}))
            (output/'native-report.json').write_text(json.dumps(dict(checks=report['checks'],pages=report['pages'],
                document_id=did,historical_run_id=rid,current_run_id=new_id,checkpoint_pages=len(checkpoint_numbers),
                status='native_provenance_checked; vector_and_final_shutdown_pending'),ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps(dict(event='offline-vector-index-start',source_blocks=len(rows))),flush=True)
            index_start=time.monotonic()
            search=owner.get('/api/search',params={'q':'StyleGAN style based generator','mode':'hybrid','document_id':did,'run_id':new_id,'limit':20})
            report['cold_index_seconds']=round(time.monotonic()-index_start,3)
            check('actual offline book vector indexing',search.status_code==200 and search.json().get('semantic',{}).get('status')=='applied',search.json())
            vector=search.json();check('vector covers all saved blocks',vector['semantic']['source_blocks']==len(rows) and vector['semantic']['truncated_blocks']==0,vector['semantic'])
            for hit in vector['hits']:
                raw=next(row for row in rows if row['id']==hit['block_id'])
                check('semantic hit preserves canonical source '+hit['block_id'],hit['run_id']==new_id and hit['page']==raw['page'] and hit['text']==raw['text'] and hit['bbox']==json.loads(raw['bbox']))
            (output/'semantic.json').write_text(json.dumps(vector,ensure_ascii=False,indent=2),encoding='utf-8')
            for path in [f'/api/documents/{did}',f'/api/documents/{did}/pages/73?run_id={rid}',f'/api/documents/{did}/pages/73/image?scale=1',f'/api/search?q=StyleGAN&document_id={did}&run_id={rid}']:
                check('foreign account blocked '+path,other.get(path).status_code==404)
            derived=[]
            for case in manifest['derived']:
                path=manifest_path.parent/case['path'];check('derived source digest '+path.name,sha256(path)==case['sha256'])
                result=owner.post('/api/documents',files={'file':(path.name,path.read_bytes(),'application/pdf')})
                check('derived PDF uploaded '+path.name,result.status_code==202)
                derived.append(dict(id=result.json()['document']['id'],path=path.name))
            # A second small existing frozen PDF supports independent page memory.
            small=ROOT/'tests/fixtures/local_acceptance/20261019-v1/shared.pdf'
            extra=owner.post('/api/documents',files={'file':(small.name,small.read_bytes(),'application/pdf')})
            check('second document queued',extra.status_code==202)
            report.update(document_id=did,historical_run_id=rid,current_run_id=new_id,checkpoint_pages=len(checkpoint_numbers),
                          semantic=vector['semantic'],derived=derived,other_document_id=extra.json()['document']['id'])
            print(json.dumps(dict(event='ready',output=str(output),origin=origin,document_id=did,historical_run_id=rid,
                                 current_run_id=new_id,derived=derived,other_document_id=report['other_document_id'])),flush=True)
            if '--serve' in sys.argv:
                for line in sys.stdin:
                    if line.strip()=='stop':break
    except BaseException as error:
        report['errors'].append(type(error).__name__+': '+str(error));raise
    finally:
        for process,observed,log in processes:
            try:
                if process.poll() is None:stop(process,observed)
                check('owned objects gone before observer cleanup '+str(process.pid),not observed.living())
            finally:observed.close();log.close()
        server.should_exit=True
        if thread.ident:thread.join(timeout=15)
        calls=0
        if library:
            JobStore(library)
            with library._connection() as db:calls=db.execute('SELECT COUNT(*) FROM model_calls').fetchone()[0]
        report.update(stopped=not thread.is_alive(),main_unchanged=before==main_snapshot(),model_calls=calls,
                      runtime_sha256=hashes,runtime_unchanged=hashes=={name:sha256(ROOT/name) for name in names})
        report['passed']=not report['errors'] and report['stopped'] and report['main_unchanged'] and not calls and report['runtime_unchanged']
        (output/'parse-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(dict(event='complete',output=str(output),passed=report['passed'],checks=len(report['checks']))),flush=True)
    assert report['passed']


if __name__=='__main__':run()
