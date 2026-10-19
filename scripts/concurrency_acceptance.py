"""Sustained local multi-account HTTP acceptance; short preflight is NOT a pass.

Runs against frozen inputs in an isolated instance with actual worker processes.
No provider config, remote network, installation or main-workspace mutation.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import traceback

import httpx

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(Path(__file__).resolve().parent))
from freeze_local_acceptance import main_snapshot, sha256, verify
from local_process_stats import snapshot, stop_owned_tree

DATA=ROOT/'tests/fixtures/local_acceptance/20261019-v1'
PASSWORD='local-concurrency-test-password'


def percentile(values,fraction):
    values=sorted(values)
    return round(values[min(len(values)-1,max(0,int((len(values)-1)*fraction)))],3) if values else None


class Acceptance:
    def __init__(self,quick=False):
        self.manifest=verify(DATA)
        self.contract=json.loads((DATA/'contract.json').read_text(encoding='utf-8'))
        self.quick=quick
        self.output=ROOT/'artifacts/local-concurrency'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        self.output.mkdir(parents=True,exist_ok=False)
        self.metrics=defaultdict(list);self.errors=[];self.operations=Counter();self.checks=[]
        self.accepted={};self.actors=[];self.samples=[];self.phase='setup';self.started=time.monotonic()
        self.phase_spans=[];self.done=False;self.clients=[];self.book=None;self.browser=None
        self.root_before=main_snapshot();self.log=(self.output/'requests.jsonl').open('a',encoding='utf-8')
        self.record={'mode':'preflight_not_sustained_acceptance' if quick else 'full_sustained_acceptance',
            'source_revision':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
            'frozen_manifest_sha256':sha256(DATA/'manifest.json'),'contract':self.contract,
            'output':str(self.output),'checks':self.checks,'errors':self.errors}
        self.source_fingerprints={str(path.relative_to(ROOT)):sha256(path) for path in
            [*sorted((ROOT/'research_workspace').rglob('*.py')),*sorted((ROOT/'research_workspace/static').glob('*.js')),
             *[ROOT/'scripts'/name for name in ['concurrency_acceptance.py','concurrency_fixture_server.py','concurrency_browser.cjs','local_process_stats.py']]]}
        self.record['source_sha256']=self.source_fingerprints
        (self.output/'manifest.json').write_text(json.dumps(self.record,indent=2),encoding='utf-8')

    def check(self,name,ok,details=None):
        self.checks.append({'name':name,'passed':bool(ok),'details':details})
        if not ok:raise AssertionError(name+': '+str(details))

    async def request(self,actor,method,path,*,expected=(200,),tag='light',**kwargs):
        client=actor['client'] if actor else self.control
        start=time.monotonic();response=None
        try:
            response=await client.request(method,path,**kwargs)
            elapsed=(time.monotonic()-start)*1000
            self.metrics[tag].append(elapsed)
            if tag=='semantic' and self.phase in ('mixed_5_accounts','extended_10_accounts'):
                self.metrics['semantic_warm'].append(elapsed)
            line={'phase':self.phase,'actor':actor['name'] if actor else 'control','method':method,
                  'path':path,'status':response.status_code,'elapsed_ms':round(elapsed,3),'at':time.time()}
            self.log.write(json.dumps(line)+'\n');self.log.flush()
            assert response.status_code in expected,(path,response.status_code,response.text[:300])
            if actor and 'application/json' in response.headers.get('content-type','') and response.status_code==200:
                payload=response.json()
                # Search reflects the supplied query. That echo is not another
                # account's source data; inspect returned evidence separately.
                if path=='/api/search':
                    payload={k:v for k,v in payload.items() if k not in ('query','english_query','ranking_queries')}
                serialized=json.dumps(payload,ensure_ascii=False)
                foreign=[a['marker'] for a in self.manifest['fixtures'] if a['name']!=actor['name']]
                assert not any(marker in serialized for marker in foreign),'Private marker leaked across accounts'
            return response
        except Exception as exc:
            self.errors.append({'phase':self.phase,'actor':actor['name'] if actor else 'control',
                'path':path,'status':response.status_code if response is not None else None,'error':repr(exc)})
            raise

    async def json(self,actor,method,path,**kwargs):
        return (await self.request(actor,method,path,**kwargs)).json()

    async def wait_run(self,actor,run_id,timeout=90,terminal=None):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            run=(await self.json(actor,'GET','/api/jobs/'+run_id,tag='poll'))['run']
            if run['status'] not in ('queued','running'):
                if terminal:assert run['status'] in terminal,run
                return run
            await asyncio.sleep(.1 if self.quick else .25)
        raise TimeoutError('Run did not finish: '+run_id)

    async def add_actor(self,index):
        source=self.manifest['fixtures'][index]
        actor={**source,'client':httpx.AsyncClient(base_url=self.origin,timeout=30,trust_env=False),
               'index':index,'iterations':0,'accepted_runs':{},'semantic_successes':0,'semantic_busy':0}
        self.clients.append(actor['client'])
        await self.json(actor,'POST','/api/account/register',expected=(200,),json={'name':actor['name'],'password':PASSWORD},tag='setup')
        uploaded=await self.json(actor,'POST','/api/documents',expected=(202,),files={'file':('same-name.pdf',(DATA/actor['pdf']).read_bytes(),'application/pdf')},tag='setup')
        actor['document_id']=uploaded['document']['id'];run=uploaded['document']['current_run']['id']
        actor['accepted_runs'][run]=actor['document_id']
        await self.wait_run(actor,run,terminal={'completed'})
        actor['history']=await self.json(actor,'POST','/__fixture__/history',params={'document_id':actor['document_id']},tag='setup')
        actor['old_run']=run
        actor['old_page']=await self.json(actor,'GET',f"/api/documents/{actor['document_id']}/pages/1",params={'run_id':run},tag='setup')
        actor['old_job']=await self.json(actor,'GET','/api/research/tasks/'+actor['history']['job_id'],tag='setup')
        shared=await self.json(actor,'POST','/api/documents',expected=(202,),files={'file':('shared.pdf',(DATA/'shared.pdf').read_bytes(),'application/pdf')},tag='setup')
        actor['shared_id']=shared['document']['id'];sr=shared['document']['current_run']['id']
        actor['accepted_runs'][sr]=actor['shared_id'];await self.wait_run(actor,sr,terminal={'completed'})
        dataset=await self.json(actor,'POST','/api/datasets',expected=(201,),files={'file':('same-data.csv',(DATA/actor['csv']).read_bytes(),'text/csv')},tag='setup')
        actor['dataset_id']=dataset['dataset']['id']
        chart=(await self.json(actor,'POST',f"/api/datasets/{actor['dataset_id']}/charts",json={'kind':'line','x':'label','y':['value']},tag='setup'))['chart']
        actor['chart_id']=chart['id']
        deadline=time.monotonic()+20
        while True:
            semantic=await self.json(actor,'GET','/api/search',params={'q':'stable historical source','mode':'hybrid',
                'document_id':actor['document_id'],'run_id':actor['old_run']},tag='semantic_setup')
            if semantic['status']!='busy':
                assert semantic.get('semantic',{}).get('status')=='applied',(actor['name'],semantic.get('semantic'))
                assert semantic['hits'] and all(h['document_id']==actor['document_id'] for h in semantic['hits'])
                actor['semantic_prepared']=True
                break
            assert time.monotonic()<deadline,'Offline semantic initialization stayed busy'
            await asyncio.sleep(.2+index*.03)
        self.actors.append(actor)
        return actor

    def browser_manifest(self):
        value={'origin':self.origin,'actors':[{key:a[key] for key in ('name','marker','document_id','old_run','dataset_id','chart_id')} for a in self.actors[:5]],
               'output':str(self.output),'quick':self.quick}
        for item,actor in zip(value['actors'],self.actors[:5]):item['evidence']=actor['history']['evidence']
        (self.output/'browser-input.json').write_text(json.dumps(value,indent=2),encoding='utf-8')

    async def check_history(self,actor):
        old=await self.json(actor,'GET',f"/api/documents/{actor['document_id']}/pages/1",params={'run_id':actor['old_run']},tag='source')
        assert old==actor['old_page'],'Old parse page changed'
        task=await self.json(actor,'GET','/api/research/tasks/'+actor['history']['job_id'],tag='source')
        assert task==actor['old_job'],'Historical task/checkpoint/audit changed'
        evidence=actor['history']['evidence']
        block=next(b for b in old['blocks'] if b['id']==evidence['block_id'])
        assert block['text'][evidence['char_start']:evidence['char_end']]==actor['historical_quote']
        assert block['bbox']==evidence['bbox']
        self.operations['historical_source_checks']+=1

    async def forbidden(self,actor,other):
        result=await self.json(actor,'GET','/api/search',params={'q':other['marker'],'limit':100},tag='isolation')
        assert not result['hits'],'Foreign private marker leaked through whole-library search'
        paths=[f"/api/documents/{other['document_id']}",f"/api/jobs/{other['old_run']}",
            f"/api/documents/{other['document_id']}/pages/1?run_id={other['old_run']}",
            f"/api/documents/{other['document_id']}/pages/1/image",
            f"/api/documents/{actor['document_id']}/pages/1?run_id={other['old_run']}",
            f"/api/documents/{other['document_id']}/structure",f"/api/datasets/{other['dataset_id']}",
            f"/api/charts/{other['chart_id']}/json",f"/api/research/tasks/{other['history']['job_id']}",
            f"/api/research/tasks/{other['history']['job_id']}/export"]
        for path in paths:
            await self.request(actor,'GET',path,expected=(404,),tag='isolation')
        self.operations['foreign_route_batches']+=1

    async def cycle(self,actor):
        index=actor['iterations']%12;did=actor['document_id']
        if index==0:
            documents=await self.json(actor,'GET','/api/documents')
            assert any(d['id']==did for d in documents['documents'])
            await self.json(actor,'GET','/api/research/tasks')
        elif index==1:await self.check_history(actor)
        elif index==2:
            result=await self.json(actor,'GET','/api/search',params={'q':actor['marker'],'document_id':did,'run_id':actor['old_run'],'limit':20},tag='indexed_search')
            assert len(result['hits'])==8
            assert all(h['document_id']==did and h['run_id']==actor['old_run'] and actor['marker'] in h['text'] for h in result['hits'])
        elif index==3:
            await self.json(actor,'GET',f'/api/documents/{did}/structure',params={'run_id':actor['old_run']})
            result=await self.json(actor,'GET','/api/search',params={'q':'stable historical source','mode':'hybrid','document_id':did,'run_id':actor['old_run']},tag='semantic')
            if result['status']!='busy':
                assert result.get('semantic',{}).get('status')=='applied',(actor['name'],'real semantic channel not applied',result.get('semantic'))
                assert result['hits'] and all(h['document_id']==did and h['run_id']==actor['old_run'] for h in result['hits'])
                self.operations['semantic_cache_reads']+=1
                actor['semantic_successes']+=1
            else:
                actor['semantic_busy']+=1
                self.operations['semantic_busy']+=1
        elif index==4:
            response=await self.request(actor,'GET',f'/api/documents/{did}/pages/1/image',params={'scale':.5},tag='image')
            assert response.content.startswith(b'\x89PNG')
            await self.forbidden(actor,next(a for a in self.actors if a['name']!=actor['name']))
        elif index==5:
            responses=await asyncio.gather(*[self.json(actor,'POST','/api/documents',expected=(200,),files={'file':('same-name.pdf',(DATA/actor['pdf']).read_bytes(),'application/pdf')},tag='write') for _ in range(2)])
            assert all(r['duplicate'] and r['document']['id']==did for r in responses)
            self.operations['same_account_duplicate_pairs']+=1
        elif index==6:
            dataset=await self.json(actor,'POST','/api/datasets',expected=(201,),files={'file':('same-data.csv',(DATA/actor['csv']).read_bytes(),'text/csv')},tag='write')
            assert [int(r[1]) for r in dataset['dataset']['rows']]==actor['expected_values']
        elif index==7:
            chart=(await self.json(actor,'POST',f"/api/datasets/{actor['dataset_id']}/charts",json={'kind':'bar','x':'label','y':['value']},tag='write'))['chart']
            assert chart['spec']['series'][0]['values']==actor['expected_values']
            await self.request(actor,'GET',f"/api/charts/{chart['id']}/svg",tag='export')
            await self.request(actor,'GET',f"/api/charts/{chart['id']}/py",tag='export')
            value=await self.json(actor,'GET',f"/api/charts/{chart['id']}/json",tag='export')
            assert value['spec']==chart['spec']
        elif index==8:
            run=(await self.json(actor,'POST',f'/api/documents/{did}/parse-runs',expected=(202,),tag='write'))['run']
            actor['accepted_runs'][run['id']]=did
            start=time.monotonic()
            observation=await self.json(actor,'POST','/api/jobs/'+run['id']+'/cancel',tag='write')
            final=await self.wait_run(actor,run['id'],timeout=5,terminal={'cancelled','completed'})
            self.metrics['cancel'].append((time.monotonic()-start)*1000)
            self.operations['cancel_completed_before_request' if final['status']=='completed' else 'cancel_confirmed']+=1
            if observation['run']['status']=='running' and final['status']=='cancelled':self.operations['cancel_running_confirmed']+=1
            await self.check_history(actor)
        elif index==9:
            active=(await self.json(actor,'GET','/api/documents/'+did))['document']['current_run']
            if active['status'] not in ('queued','running'):
                run=(await self.json(actor,'POST',f'/api/documents/{did}/pages/2/retry',params={'run_id':actor['old_run']},tag='write'))['run']
                actor['accepted_runs'][run['id']]=did
                await self.wait_run(actor,run['id'],terminal={'completed'})
                self.operations['page_retries']+=1
        elif index==10:
            await self.request(actor,'POST','/api/documents',expected=(400,),files={'file':('broken.pdf',(DATA/'broken.pdf').read_bytes(),'application/pdf')},tag='negative')
            await self.request(actor,'GET','/api/documents',expected=(401,),headers={'Cookie':'research_session=synthetic-expired-session'},tag='negative')
            await self.request(actor,'POST','/api/research/tasks',expected=(503,),json={'kind':'qa','document_ids':[did],'question':'Fixture only; model remains unconfigured.'},tag='negative')
        else:
            runs=(await self.json(actor,'GET',f'/api/documents/{did}/parse-runs'))['runs']
            ids=[r['id'] for r in runs]
            expected={rid for rid,document in actor['accepted_runs'].items() if document==did}
            assert len(ids)==len(set(ids)) and expected<=set(ids),(actor['name'],'accepted private runs',expected-set(ids))
            await self.request(actor,'GET','/api/research/tasks/'+actor['history']['job_id']+'/export',tag='export')
        actor['iterations']+=1;self.operations['cycles']+=1

    async def load(self,actor,end):
        while time.monotonic()<end:
            if self.browser and self.browser.poll() is not None:
                raise RuntimeError('Browser acceptance stopped early; inspect browser-stderr.log')
            await self.cycle(actor)
            await asyncio.sleep(.03 if self.quick else self.contract['actor_pause_seconds'])

    async def book_start(self):
        actor=self.actors[0]
        if not self.book:
            item=self.manifest['book']
            document=(await self.json(actor,'POST','/api/documents',expected=(202,),files={'file':('UnderstandingDeepLearning.pdf',(ROOT/item['path']).read_bytes(),'application/pdf')},tag='book'))['document']
            self.book={'document_id':document['id'],'run_id':document['current_run']['id']}
        else:
            run=(await self.json(actor,'POST',f"/api/documents/{self.book['document_id']}/parse-runs",expected=(202,),tag='book'))['run']
            self.book['run_id']=run['id']
        return self.book

    async def crash_and_recover(self):
        actor=self.actors[0];book=await self.book_start();deadline=time.monotonic()+90
        while time.monotonic()<deadline:
            run=(await self.json(actor,'GET','/api/jobs/'+book['run_id'],tag='poll'))['run']
            if 8<=run['pages_done']<500:break
            await asyncio.sleep(.05)
        assert 8<=run['pages_done']<541,run
        old=await self.json(actor,'GET',f"/api/documents/{book['document_id']}/pages/1",params={'run_id':book['run_id']},tag='source')
        await self.json(None,'POST','/__fixture__/activity/stop',tag='control')
        recovery=await self.json(actor,'POST','/__fixture__/recovery',tag='control')
        await self.json(None,'POST','/__fixture__/activity/restart',tag='control')
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            job=(await self.json(actor,'GET','/api/research/tasks/'+recovery['job_id'],tag='control'))['job']
            if job['status']=='interrupted':break
            await asyncio.sleep(.1)
        self.check('Interrupted research checkpoint retained without automatic replay',job['status']=='interrupted' and job['completed_items']==recovery['checkpoint'])
        await asyncio.sleep(.5)
        current=await self.json(actor,'GET',f"/api/documents/{book['document_id']}/pages/1",params={'run_id':book['run_id']},tag='source')
        self.check('Real book worker crash retains already-saved page IDs and coordinates',old==current)
        self.operations['real_activity_restarts']+=1
        self.book_history={'document_id':book['document_id'],'run_id':book['run_id'],'page':old}
        short=self.actors[1]
        book_state=(await self.json(actor,'GET','/api/jobs/'+book['run_id'],tag='poll'))['run']
        start=time.monotonic()
        short_run=(await self.json(short,'POST',f"/api/documents/{short['document_id']}/parse-runs",expected=(202,),tag='write'))['run']
        short['accepted_runs'][short_run['id']]=short['document_id']
        await self.wait_run(short,short_run['id'],timeout=30,terminal={'completed'})
        self.check('Short account task completes alongside the active long-book account',book_state['pages_done']<541 and time.monotonic()-start<30)
        start=time.monotonic()
        before_cancel=(await self.json(actor,'GET','/api/jobs/'+book['run_id'],tag='poll'))['run']
        await self.json(actor,'POST','/api/jobs/'+book['run_id']+'/cancel',tag='write')
        cancelled=await self.wait_run(actor,book['run_id'],timeout=5,terminal={'cancelled'})
        self.metrics['cancel'].append((time.monotonic()-start)*1000)
        self.check('Partially processed long-book cancellation keeps saved source',0<cancelled['pages_done']<541)
        if before_cancel['status']=='running':self.operations['cancel_running_confirmed']+=1
        self.record['book_cancel_observation']={'before':before_cancel,'after':cancelled}

    async def observe(self):
        while not self.done:
            sample={'at':time.time(),'phase':self.phase,**snapshot(os.getpid())}
            self.samples.append(sample)
            status={'phase':self.phase,'elapsed_seconds':round(time.monotonic()-self.started,1),'actors':len(self.actors),
                'cycles':sum(a['iterations'] for a in self.actors),'request_count':sum(len(v) for v in self.metrics.values()),
                'errors':len(self.errors),'rss_bytes':sample['rss_bytes'],'server_pid':self.server.pid}
            (self.output/'progress.json').write_text(json.dumps(status,indent=2),encoding='utf-8')
            await asyncio.sleep(2 if self.quick else 5)

    async def phase_run(self,name,seconds):
        self.phase=name;start=time.monotonic()
        print(json.dumps({'phase':name,'seconds':seconds,'actors':len(self.actors),'output':str(self.output)}),flush=True)
        (self.output/'phase.json').write_text(json.dumps({'phase':name,'seconds':seconds}),encoding='utf-8')
        async with asyncio.TaskGroup() as group:
            for actor in self.actors:group.create_task(self.load(actor,start+seconds))
        duration=time.monotonic()-start
        self.phase_spans.append({'name':name,'seconds':duration,'actors':len(self.actors)})
        self.check(name+' actual wall-clock duration',duration>=seconds)

    async def finish_book(self):
        actor=self.actors[0];book=self.book
        if self.quick:
            await self.json(actor,'POST','/api/jobs/'+book['run_id']+'/cancel',tag='write')
            value=await self.wait_run(actor,book['run_id'],timeout=5,terminal={'cancelled','completed','partial'})
            self.record['preflight_book_partial_status']=value
            return
        run=await self.wait_run(actor,book['run_id'],timeout=600,terminal={'partial','completed'})
        self.check('Full 541-page book attempted without missing or failed pages',run['pages_done']==541 and run['failed_pages']==0 and run['text_pages']==537 and run['no_text_pages']==4,run)
        blank=[]
        for page in range(1,542):
            value=await self.json(actor,'GET',f"/api/documents/{book['document_id']}/pages/{page}",params={'run_id':book['run_id']},tag='book_coverage')
            assert value['status'] in ('parsed','no_extractable_text')
            if not value['blocks']:blank.append(page)
        self.check('All book pages and original blank page numbers match frozen observations',blank==self.manifest['book']['native_blank_pages'])

    async def run(self):
        env=os.environ.copy()
        env['LOCAL_CONCURRENCY_DIAGNOSTICS']='0'
        for key in ('RESEARCH_MODEL_BASE_URL','RESEARCH_MODEL','RESEARCH_MODEL_API_KEY'):env.pop(key,None)
        self.server=subprocess.Popen([sys.executable,'-u',str(ROOT/'scripts/concurrency_fixture_server.py'),
            '--data-dir',str(self.output/'workspace')],cwd=ROOT,env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,
            stderr=(self.output/'server-stderr.log').open('wb'),text=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        ready_line=await asyncio.wait_for(asyncio.to_thread(self.server.stdout.readline),timeout=30)
        assert ready_line.startswith('{'),ready_line
        ready=json.loads(ready_line);self.origin=ready['origin']
        self.control=httpx.AsyncClient(base_url=self.origin,timeout=40,trust_env=False);self.clients.append(self.control)
        observer=asyncio.create_task(self.observe())
        try:
            await asyncio.gather(*(self.add_actor(i) for i in range(5)))
            self.actors.sort(key=lambda a:a['index'])
            self.check('Identical public PDF is independently owned across five spaces',len({a['shared_id'] for a in self.actors})==5)
            guest=httpx.AsyncClient(base_url=self.origin,timeout=20,trust_env=False);self.clients.append(guest)
            for a in self.actors:
                response=await guest.get('/api/documents/'+a['document_id']);assert response.status_code==404
            for a in self.actors:
                await self.forbidden(a,next(b for b in self.actors if b!=a))
            await self.crash_and_recover()
            self.browser_manifest()
            self.browser=subprocess.Popen([r'C:\Program Files\nodejs\node.exe',str(ROOT/'scripts/concurrency_browser.cjs'),
                str(self.output/'browser-input.json')],cwd=ROOT,stdout=(self.output/'browser-stdout.log').open('wb'),
                stderr=(self.output/'browser-stderr.log').open('wb'),creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            durations=(5,15,10) if self.quick else (self.contract['warmup_seconds'],self.contract['mixed_seconds'],self.contract['extended_seconds'])
            await self.phase_run('warmup',durations[0])
            self.warm_rss=snapshot(os.getpid())['rss_bytes']
            await self.book_start()
            await self.phase_run('mixed_5_accounts',durations[1])
            await asyncio.gather(*(self.add_actor(i) for i in range(5,10)))
            self.actors.sort(key=lambda a:a['index'])
            await self.book_start()
            await self.phase_run('extended_10_accounts',durations[2])
            for index in range(3):
                await self.phase_run('memory_load_'+str(index+1),1 if self.quick else self.contract['memory_load_seconds'])
                self.phase='memory_idle_'+str(index+1)
                await asyncio.sleep(1 if self.quick else self.contract['memory_idle_seconds'])
                self.samples.append({'at':time.time(),'phase':self.phase,**snapshot(os.getpid())})
            await self.finish_book()
            self.phase='final'
            for actor in self.actors:
                for run in actor['accepted_runs']:await self.wait_run(actor,run)
                await self.check_history(actor)
                rows=(await self.json(actor,'GET',f"/api/documents/{actor['document_id']}/parse-runs"))['runs']
                ids=[r['id'] for r in rows]
                expected={rid for rid,document in actor['accepted_runs'].items() if document==actor['document_id']}
                self.check(actor['name']+' accepted runs retained once',expected<=set(ids) and len(ids)==len(set(ids)))
                self.check(actor['name']+' actual offline semantic indexing exercised',actor.get('semantic_prepared') and (self.quick or actor['semantic_successes']>0))
            old_book=await self.json(self.actors[0],'GET',f"/api/documents/{self.book_history['document_id']}/pages/1",params={'run_id':self.book_history['run_id']},tag='source')
            self.check('Original crashed book source remains unchanged after subsequent full parses',old_book==self.book_history['page'])
            violations=self.output/'network-violations.jsonl'
            self.check('No external networking attempted',not violations.exists() or not violations.read_text().strip())
            calls=0
            for database in (self.output/'workspace').rglob('library.sqlite3'):
                with sqlite3.connect(database.as_uri()+'?mode=ro',uri=True) as db:
                    if db.execute("SELECT 1 FROM sqlite_master WHERE name='model_calls'").fetchone():calls+=db.execute('SELECT COUNT(*) FROM model_calls').fetchone()[0]
            self.check('No model call receipts created',calls==0)
        except Exception as exc:
            self.record['failure']=repr(exc)
            (self.output/'failure-traceback.log').write_text(traceback.format_exc(),encoding='utf-8')
            self.errors.append({'phase':self.phase,'error':repr(exc)})
        finally:
            self.done=True;observer.cancel()
            try:await observer
            except asyncio.CancelledError:pass
            if self.browser:
                (self.output/'browser-stop').write_text('stop')
                try:await asyncio.to_thread(self.browser.wait,timeout=25)
                except subprocess.TimeoutExpired:stop_owned_tree(self.browser)
            for client in self.clients:await client.aclose()
            if self.server.poll() is None:
                self.server.stdin.write('stop\n');self.server.stdin.flush()
                try:await asyncio.to_thread(self.server.wait,timeout=30)
                except subprocess.TimeoutExpired:stop_owned_tree(self.server)
            self.log.close()
            (self.output/'memory.json').write_text(json.dumps(self.samples),encoding='utf-8')
            self.record.update(phases=self.phase_spans,operations=dict(self.operations),
                metrics={name:{'count':len(values),'p50_ms':percentile(values,.5),'p95_ms':percentile(values,.95),'p99_ms':percentile(values,.99)} for name,values in self.metrics.items()},
                actors=[{'name':a['name'],'iterations':a['iterations'],'accepted_runs':len(a['accepted_runs']),
                    'semantic_successes':a['semantic_successes'],'semantic_busy':a['semantic_busy']} for a in self.actors],
                elapsed_seconds=round(time.monotonic()-self.started,3))
            self.checks.append({'name':'Runtime source fingerprints unchanged',
                'passed':all(sha256(ROOT/name)==fingerprint for name,fingerprint in self.source_fingerprints.items())})
            if (self.output/'fixture-final.json').exists():
                fixture=json.loads((self.output/'fixture-final.json').read_text());self.record['fixture']=fixture
                self.checks.append({'name':'User main records and original files unchanged','passed':fixture['main_unchanged'] and self.root_before==main_snapshot()})
                self.checks.append({'name':'Standard app shutdown leaves no owned worker orphans','passed':not fixture['shutdown_orphans_before_fallback']})
            else:self.errors.append({'error':'Fixture final record missing'})
            if (self.output/'browser-report.json').exists():
                browser=json.loads((self.output/'browser-report.json').read_text());self.record['browser']=browser
                self.checks.append({'name':'Five simultaneous browser contexts completed without errors','passed':browser.get('passed',False)})
            else:self.errors.append({'error':'Browser completion record missing'})
            if self.samples:
                peak=max(v['rss_bytes'] for v in self.samples)
                self.record['peak_rss_bytes']=peak
                self.checks.append({'name':'Peak owned process RSS below 8 GiB','passed':peak<=self.contract['peak_rss_bytes']})
            idle=[s for s in self.samples if s['phase'].startswith('memory_idle')]
            if idle:
                growth=idle[-1]['rss_bytes']-idle[0]['rss_bytes'];self.record['idle_rss_growth_bytes']=growth
                self.checks.append({'name':'Three memory rounds idle growth below 512 MiB','passed':growth<=self.contract['idle_growth_bytes']})
                if not self.quick and hasattr(self,'warm_rss'):
                    warm_growth=idle[-1]['rss_bytes']-self.warm_rss
                    self.record['idle_rss_growth_since_warmup_bytes']=warm_growth
                    self.checks.append({'name':'Final idle RSS below warmed baseline plus 512 MiB','passed':warm_growth<=self.contract['idle_growth_bytes']})
            if not self.quick:
                self.checks.append({'name':'Warm light API p95 below 1 second','passed':bool(self.metrics['light']) and percentile(self.metrics['light'],.95)<self.contract['light_p95_ms']})
                self.checks.append({'name':'Indexed literal search p95 below 3 seconds','passed':bool(self.metrics['indexed_search']) and percentile(self.metrics['indexed_search'],.95)<self.contract['indexed_search_p95_ms']})
                self.checks.append({'name':'Warm semantic search p95 below 3 seconds','passed':bool(self.metrics['semantic_warm']) and percentile(self.metrics['semantic_warm'],.95)<self.contract['indexed_search_p95_ms']})
            self.checks.append({'name':'Cancellation confirmations within 5 seconds','passed':bool(self.metrics['cancel']) and max(self.metrics['cancel'])<self.contract['cancel_deadline_seconds']*1000})
            self.checks.append({'name':'At least one running parse cancellation confirmed','passed':self.operations['cancel_running_confirmed']>0})
            self.record['passed']=not self.errors and bool(self.checks) and all(c['passed'] for c in self.checks)
            self.record['sustained_accepted']=self.record['passed'] and not self.quick
            (self.output/'report.json').write_text(json.dumps(self.record,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps({'output':str(self.output),'passed':self.record['passed'],'sustained_accepted':self.record['sustained_accepted'],
                'errors':len(self.errors),'elapsed_seconds':self.record['elapsed_seconds']}),flush=True)
        return self.record['passed']


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--quick',action='store_true');args=parser.parse_args()
    result=asyncio.run(Acceptance(args.quick).run())
    raise SystemExit(0 if result else 1)
