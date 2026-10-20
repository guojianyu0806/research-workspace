"""Real loopback HTTP acceptance with saved history and frozen capacity inputs."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
import threading
import time

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.freeze_local_acceptance import main_snapshot,sha256
from scripts.freeze_long_reading import DEFAULT,canonical,synthetic_blocks


def run():
    output=ROOT/'artifacts/translation-pages'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output.mkdir(parents=True,exist_ok=False)
    report=dict(checks=[],cases=[],errors=[],model_calls=0,scope='Saved translation compatibility and synthetic capacity; no model quality claim.')
    def check(name,condition):
        report['checks'].append(dict(name=name,passed=bool(condition)))
        if not condition:raise AssertionError(name)
    manifest=json.loads((DEFAULT/'manifest.json').read_text(encoding='utf-8'))
    before=main_snapshot()
    names=['research_workspace/translation_pages.py','research_workspace/jobs.py','research_workspace/advanced_api.py',
           'research_workspace/api.py','scripts/translation_pages_acceptance.py']
    hashes={name:sha256(ROOT/name) for name in names}
    for name in ('RESEARCH_MODEL_BASE_URL','RESEARCH_MODEL','RESEARCH_MODEL_API_KEY'):os.environ.pop(name,None)
    os.environ['RESEARCH_SEMANTIC_ENABLED']='off'
    from research_workspace.api import create_app
    from research_workspace.jobs import JobStore
    from research_workspace.library import Library
    import httpx
    import uvicorn
    app=create_app(output/'workspace',start_worker=False)
    # Only synthetic identities; no user session or credential is read/copied.
    _,alice=app.state.accounts.register('LocalPagingAlice','local-fixture-password-123')
    app.state.accounts.register('LocalPagingBob','local-fixture-password-456')
    library=Library(app.state.accounts.directory(alice));store=JobStore(library)
    saved_path=ROOT/manifest['saved_translation']['path']
    check('frozen saved result file unchanged',sha256(saved_path)==manifest['saved_translation']['sha256'])
    saved=json.loads(saved_path.read_text(encoding='utf-8'))
    inputs=[('saved-107',saved)]
    for case in manifest['cases']:
        blocks=synthetic_blocks(case['count'],case['variant'])
        check(case['id']+' frozen source digest',hashlib.sha256(canonical(blocks)).hexdigest()==case['canonical_sha256'])
        value=dict(document_id='synthetic-long-document',run_id='synthetic-long-run-v1',target_language='zh',
                   blocks=blocks,total_blocks=len(blocks),status='completed',missing_pages=[])
        inputs.append((case['id'],value))
    jobs=[]
    for label,value in inputs:
        job_id=store.create('translate',{k:value[k] for k in ('document_id','run_id','target_language')})['id']
        store.finish(job_id,'completed',result=value)
        jobs.append((label,job_id,value))
    def originals():
        with library._connection() as db:
            return {table:hashlib.sha256(canonical([tuple(r) for r in db.execute('SELECT * FROM '+table+' ORDER BY rowid')])).hexdigest()
                    for table in ('research_jobs','research_job_items','research_job_events','model_calls')}
    original=originals()
    sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(128)
    origin=f'http://127.0.0.1:{sock.getsockname()[1]}'
    server=uvicorn.Server(uvicorn.Config(app,log_level='error'))
    thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True)
    original_connect=socket.socket.connect
    def local_connect(instance,address):
        if not isinstance(address,tuple) or address[0] not in ('127.0.0.1','::1'):
            raise RuntimeError('Acceptance forbids external connections')
        return original_connect(instance,address)
    socket.socket.connect=local_connect
    try:
        thread.start();deadline=time.monotonic()+15
        while not server.started:
            if not thread.is_alive() or time.monotonic()>deadline:raise RuntimeError('Isolated API did not start')
            time.sleep(.02)
        with httpx.Client(base_url=origin,trust_env=False,timeout=60) as owner,httpx.Client(base_url=origin,trust_env=False,timeout=30) as other:
            check('synthetic owner login',owner.post('/api/account/login',json={'name':'LocalPagingAlice','password':'local-fixture-password-123'}).status_code==200)
            check('synthetic other login',other.post('/api/account/login',json={'name':'LocalPagingBob','password':'local-fixture-password-456'}).status_code==200)
            for label,job_id,value in jobs:
                path='/api/research/tasks/'+job_id
                full=owner.get(path);check(label+' legacy full response',full.status_code==200 and full.json()['job']['result']==value)
                exported=owner.get(path+'/export');check(label+' complete export',exported.status_code==200)
                start=time.perf_counter();metadata=owner.get(path,params={'include_result':False});cold_ms=(time.perf_counter()-start)*1000
                check(label+' bounded metadata',metadata.status_code==200 and metadata.json()['job']['result'] is None)
                info=metadata.json()['job']['translation_summary'];revision=info['revision']
                check(label+' total retained',info['total']==len(value['blocks']))
                check(label+' metadata omits body',all(k not in info['result_metadata'] for k in ('blocks','source_text','translated_text')))
                gathered=[];page_sizes=[];page_ms=[];valid_anchors=[];missing=[]
                for offset in range(0,len(value['blocks']),200):
                    start=time.perf_counter();response=owner.get(path+'/translation-blocks',params={'offset':offset,'limit':200,'revision':revision,'include_index':offset==0})
                    page_ms.append((time.perf_counter()-start)*1000)
                    check(label+f' page {offset} HTTP 200',response.status_code==200)
                    page=response.json();page_sizes.append(len(response.content));gathered.extend(page['blocks'])
                    valid_anchors.extend(row['anchor_valid'] for row in page['record_metadata'])
                    missing.extend(row['missing_translation'] for row in page['record_metadata'])
                    check(label+f' page {offset} original order',page['blocks']==value['blocks'][offset:offset+200])
                    check(label+f' page {offset} ordinal continuity',[r['ordinal'] for r in page['record_metadata']]==list(range(offset,min(offset+200,len(value['blocks'])))))
                    if offset==0:check(label+' complete small index',len(page['index'])==len(value['blocks']) and all('source_text' not in r for r in page['index']))
                check(label+' full reconstruction',gathered==value['blocks'])
                check(label+' raw block count retained',len(gathered)==len(value['blocks']))
                check(label+' export byte stability',owner.get(path+'/export').content==exported.content)
                check(label+' legacy response unchanged',owner.get(path).content==full.content)
                for suffix in ('?include_result=false','/translation-blocks?include_index=true','/export'):
                    check(label+' foreign account blocked '+suffix,other.get(path+suffix).status_code==404)
                if label=='duplicate-1000':
                    check('both duplicate anchors disabled',not valid_anchors[1] and not valid_anchors[-1] and sum(valid_anchors)==998)
                    check('duplicate lookup rejected',owner.get(path+'/translation-blocks',params={'block_id':value['blocks'][1]['block_id']}).status_code==400)
                elif label=='wrong-run-1000':check('wrong-run anchor disabled',sum(valid_anchors)==999 and not valid_anchors[-1])
                else:check(label+' fixed-source anchors',all(valid_anchors))
                check(label+' missing targets counted',sum(missing)==sum(not b['translated_text'].strip() for b in value['blocks']))
                if label=='mixed-5000':
                    offsets=[i*137%5000 for i in range(100)]
                    def parallel_read(offset):
                        response=owner.get(path+'/translation-blocks',params={'offset':offset,'limit':20,'revision':revision})
                        return response.status_code==200 and response.json()['blocks']==value['blocks'][offset:offset+20]
                    with ThreadPoolExecutor(max_workers=10) as pool:check('100 overlapping paged reads preserve exact blocks',all(pool.map(parallel_read,offsets)))
                    check('metadata less than 5 percent of full body',len(metadata.content)<len(full.content)*.05)
                report['cases'].append(dict(id=label,total=len(gathered),full_response_bytes=len(full.content),metadata_bytes=len(metadata.content),
                    max_page_bytes=max(page_sizes),cold_index_ms=round(cold_ms,2),max_page_ms=round(max(page_ms),2),
                    canonical_blocks_sha256=hashlib.sha256(canonical(gathered)).hexdigest(),export_sha256=hashlib.sha256(exported.content).hexdigest()))
            check('all original history rows byte stable',originals()==original)
            with library._connection() as db:check('no model calls',db.execute('SELECT COUNT(*) FROM model_calls').fetchone()[0]==0)
    except BaseException as error:
        report['errors'].append(type(error).__name__+': '+str(error));raise
    finally:
        server.should_exit=True
        if thread.ident:thread.join(timeout=10)
        socket.socket.connect=original_connect
        report['stopped']=not thread.is_alive();report['main_unchanged']=main_snapshot()==before
        report['runtime_sha256']=hashes;report['runtime_unchanged']=hashes=={name:sha256(ROOT/name) for name in names}
        report['manifest_sha256']=sha256(DEFAULT/'manifest.json')
        report['passed']=not report['errors'] and report['stopped'] and report['main_unchanged'] and report['runtime_unchanged']
        (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(dict(output=str(output),passed=report['passed'],checks=len(report['checks']),cases=len(report['cases']))),flush=True)
    assert report['passed']


if __name__=='__main__':run()
