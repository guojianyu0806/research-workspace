"""Standard app/workers on isolated data, with explicit fixture-only controls."""
import argparse
import contextlib
import faulthandler
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import threading
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from freeze_local_acceptance import main_snapshot
from local_process_stats import alive, descendants, rss, stop_owned_tree

parser=argparse.ArgumentParser();parser.add_argument('--data-dir',required=True,type=Path)
args=parser.parse_args();args.data_dir.mkdir(parents=True,exist_ok=False)
faulthandler.enable()
if os.getenv('LOCAL_CONCURRENCY_DIAGNOSTICS')=='1':
    faulthandler.dump_traceback_later(30,repeat=True)
before=main_snapshot()
for key in ('RESEARCH_MODEL_BASE_URL','RESEARCH_MODEL','RESEARCH_MODEL_API_KEY'):os.environ.pop(key,None)
os.environ['RESEARCH_DATA_DIR']=str(args.data_dir.resolve())
os.environ['RESEARCH_SEMANTIC_ENABLED']='auto'
os.environ['RESEARCH_OCR_TESSDATA']=str(ROOT/'data/ocr/tessdata')
os.environ['RESEARCH_OCR_LANG']='eng+chi_sim'

# All child interpreters inherit the guard before importing the app. Log only
# destination and process ID; no HTTP body, credential or model config values.
guard=args.data_dir.parent/'network-guard';guard.mkdir()
(guard/'sitecustomize.py').write_text('''import socket,os,json
_connect=socket.socket.connect
def guarded(self,address):
    if self.family in (socket.AF_INET,socket.AF_INET6) and isinstance(address,tuple) and address[0] not in ('127.0.0.1','::1','localhost'):
        with open(os.environ['LOCAL_NETWORK_VIOLATIONS'],'a',encoding='utf-8') as stream:stream.write(json.dumps({'pid':os.getpid(),'host':str(address[0]),'port':address[1]})+'\\n')
        raise OSError('External networking disabled during local acceptance')
    return _connect(self,address)
socket.socket.connect=guarded
''',encoding='utf-8')
os.environ['LOCAL_NETWORK_VIOLATIONS']=str(args.data_dir.parent/'network-violations.jsonl')
os.environ['PYTHONPATH']=str(guard)+os.pathsep+str(ROOT)
sys.path.insert(0,str(guard))
import sitecustomize  # applies to current fixture app, too

import uvicorn
from fastapi import Depends, HTTPException, Request
from research_workspace import api
from research_workspace.advanced_api import library_for
from research_workspace.jobs import JobStore
from research_workspace.library import Library

real_popen=subprocess.Popen
workers=[]


class ObservedWorker:
    def __init__(self,command,kwargs):
        self.command,self.kwargs=command,kwargs
        self.events=[];self.process=real_popen(command,**kwargs)
        self.events.append({'action':'start','pid':self.process.pid,'at':time.time()})
    def __getattr__(self,name):return getattr(self.process,name)
    def restart(self):
        old=self.process.pid;stop_owned_tree(self.process)
        self.events.append({'action':'stop_tree','pid':old,'at':time.time()})
        self.process=real_popen(self.command,**self.kwargs)
        self.events.append({'action':'restart','pid':self.process.pid,'at':time.time()})
    def stop_tree(self):stop_owned_tree(self.process)
    def state(self):return {'kind':'activity' if 'research_workspace.activity_worker' in self.command else 'parse',
        'pid':self.process.pid,'running':self.poll() is None,'events':self.events}


def observed_popen(command,*positional,**kwargs):
    if any(value in command for value in ('research_workspace.worker','research_workspace.activity_worker')):
        assert not positional
        value=ObservedWorker(command,kwargs);workers.append(value);return value
    return real_popen(command,*positional,**kwargs)


# This wrapper observes startup and allows controlled termination/restart of the
# very same real workers. It does not replace extraction, HTTP or task logic.
api.subprocess.Popen=observed_popen
app=api.create_app(args.data_dir,start_worker=True)


@app.get('/__fixture__/state')
def fixture_state():
    return {'workers':[w.state() for w in workers], 'model_configured':False}


@app.post('/__fixture__/activity/{action}')
def activity_control(action:str):
    worker=next(w for w in workers if 'research_workspace.activity_worker' in w.command)
    if action=='stop':worker.stop_tree()
    elif action=='restart':worker.restart()
    else:raise HTTPException(400,'Unknown fixture action')
    return worker.state()


@app.post('/__fixture__/history')
def history(document_id:str,request:Request,library:Library=Depends(library_for)):
    document=library.get_document(document_id)
    page=library.get_page(document['id'],1)
    block=next(b for b in page['blocks'] if 'Stable historical source value = 42.' in b['text'])
    quote='Stable historical source value = 42.'
    start=block['text'].index(quote)
    evidence={'document_id':document['id'],'run_id':page['run_id'],'page':1,'block_id':block['id'],
        'bbox':block['bbox'],'char_start':start,'char_end':start+len(quote),'text':quote}
    marker=next(b['text'] for b in page['blocks'] if 'PRIVATE_ACCOUNT_' in b['text'])
    store=JobStore(library)
    from uuid import uuid4
    from research_workspace.library import _now
    job_id=uuid4().hex
    # Atomic fixture insertion: never publish a queued research request to the
    # actual worker or pretend that this provenance fixture was a model answer.
    with library._connection() as db:
        db.execute('INSERT INTO research_jobs VALUES(?,?,?,?,?,?,?,?,?,?,?)',
            (job_id,'qa','failed',json.dumps({'question':marker,'fixture_only':True}),None,
             'Synthetic provenance fixture; no model transport. '+marker,1,1,0,_now(),_now()))
        db.execute('INSERT INTO research_job_items VALUES(?,?,?)',
            (job_id,'fixed-source',json.dumps({'fixture_only':True,'marker':marker,'evidence':evidence})))
    return {'job_id':job_id,'evidence':evidence,'checkpoint':store.get(job_id)['completed_items']}


@app.post('/__fixture__/recovery')
def recovery(library:Library=Depends(library_for)):
    assert next(w for w in workers if 'research_workspace.activity_worker' in w.command).poll() is not None
    store=JobStore(library);job=store.create('qa',{'question':'Fixture interrupted state','fixture_only':True})
    store.save_item(job['id'],'retained-checkpoint',{'fixture_only':True,'text':'Already saved; no provider call.'},2)
    with library._connection() as db:db.execute("UPDATE research_jobs SET status='running' WHERE id=?",(job['id'],))
    return {'job_id':job['id'],'checkpoint':store.get(job['id'])['completed_items']}


sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(128)
server=uvicorn.Server(uvicorn.Config(app,log_level='error',timeout_keep_alive=5))
thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True);thread.start()
deadline=time.monotonic()+20
while not server.started:
    if time.monotonic()>deadline:raise RuntimeError('Fixture startup timed out')
    time.sleep(.025)
print(json.dumps({'origin':f'http://127.0.0.1:{sock.getsockname()[1]}','pid':os.getpid(),
    'workspace':str(args.data_dir),'workers':[w.state() for w in workers]}),flush=True)
try:
    for line in sys.stdin:
        if line.strip()=='stop':break
finally:
    faulthandler.cancel_dump_traceback_later()
    owned={pid for w in workers for pid in descendants(w.process.pid)}
    server.should_exit=True;thread.join(timeout=15)
    # Inspect the application's actual shutdown before fallback fixture cleanup.
    orphaned={pid:rss(pid) for pid in owned if alive(pid)}
    for w in workers:w.stop_tree()
    # A dead launcher may have left real worker descendants. Only previously
    # observed owned children are considered; no global Python process sweep.
    if os.name=='nt':
        for pid in orphaned:
            subprocess.run(['taskkill','/PID',str(pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,check=False)
    final={'main_unchanged':before==main_snapshot(),'workers':[w.state() for w in workers],
        'shutdown_orphans_before_fallback':orphaned,'server_stopped':not thread.is_alive()}
    (args.data_dir.parent/'fixture-final.json').write_text(json.dumps(final,indent=2),encoding='utf-8')
