"""Isolated multi-account HTTP fixture; synthetic papers and no model config."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import sys
import threading
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
parser=argparse.ArgumentParser();parser.add_argument('--data-dir',required=True,type=Path);args=parser.parse_args()
args.data_dir.mkdir(parents=True,exist_ok=False)
for key in ('RESEARCH_MODEL_BASE_URL','RESEARCH_MODEL','RESEARCH_MODEL_API_KEY'):os.environ.pop(key,None)
os.environ['RESEARCH_SEMANTIC_ENABLED']='off'
os.environ['RESEARCH_DATA_DIR']=str(args.data_dir.resolve())
import httpx
import pymupdf
import uvicorn
from research_workspace.api import create_app
from research_workspace.collaboration import CollaborativeRecommendations
from research_workspace.discovery import DiscoveryService
from research_workspace.jobs import JobStore
from research_workspace.library import Library

app=create_app(args.data_dir,start_worker=False)
ids=['arxiv:2401.1000'+str(i)+'v1' for i in range(1,5)]
titles=['Public Alpha','Public Beta','Public Collaborative Candidate','Public Other Candidate']
atom=('<feed xmlns="http://www.w3.org/2005/Atom">'+''.join(
    f'<entry><id>https://arxiv.org/abs/{paper[6:]}</id><title>{title}</title><summary>Public protocol fixture.</summary></entry>'
    for paper,title in zip(ids,titles))+'</feed>').encode()
users=[]
for index,name in enumerate(['FixtureAlice','FixtureBob','FixtureCarol','FixtureDan','FixtureEve']):
    _,user=app.state.accounts.register(name,'fixture-only-password')
    library=Library(app.state.accounts.directory(user))
    discovery=DiscoveryService(library,httpx.Client(transport=httpx.MockTransport(lambda _:httpx.Response(200,content=atom))),request_interval=0)
    discovery.search('fixture public candidates')
    discovery.feedback(ids[0],1);discovery.feedback(ids[1],1)
    if index in (1,2,3):
        discovery.feedback(ids[2],1)
        CollaborativeRecommendations(app.state.accounts,user,library).set_enabled(True,discovery)
    users.append((user,library))

alice,library=users[0]
private='ALICE_PRIVATE_NEBULA_42'
with pymupdf.open() as pdf:
    pdf.new_page().insert_text((40,80),private)
    document=library.import_document(pdf.tobytes(),'alice-private-fixture.pdf')['document']
library.process_next_job();library.update_metadata(document['id'],title=private)
jobs=JobStore(library);task=jobs.create('qa',{'question':private,'document_ids':[document['id']]})
jobs.finish(task['id'],'failed',error='Synthetic private fixture, no model request.')
subscription=DiscoveryService(library).create_subscription('ALICE_PRIVATE_SUBSCRIPTION')
DiscoveryService(library).update_subscription(subscription['id'],enabled=False)


def snapshot():
    data={}
    for user,library in users:
        with library._connection() as db:
            tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            selected={table:db.execute('SELECT * FROM '+table+' ORDER BY rowid').fetchall() if table in tables else []
                for table in ['documents','parse_runs','blocks','research_jobs','research_job_items','model_calls']}
        serialized=json.dumps({table:[tuple(r) for r in rows] for table,rows in selected.items()},ensure_ascii=False,default=str)
        data[user['name']]={'sha256':hashlib.sha256(serialized.encode()).hexdigest(),
            'model_calls':len(selected.get('model_calls',[]))}
    return data


before=snapshot()
sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(128)
server=uvicorn.Server(uvicorn.Config(app,log_level='error'))
thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True);thread.start()
deadline=time.monotonic()+10
while not server.started:
    if time.monotonic()>deadline:raise RuntimeError('Fixture startup failed')
    time.sleep(.02)
print(json.dumps({'origin':f'http://127.0.0.1:{sock.getsockname()[1]}','private_document':document['id'],
    'private_task':task['id'],'private_marker':private,'papers':ids}),flush=True)
try:
    for line in sys.stdin:
        if line.strip()=='stop':break
finally:
    server.should_exit=True;thread.join(timeout=5)
    with sqlite3.connect(args.data_dir/'collaboration.sqlite3') as db:
        projection_dump='\n'.join(db.iterdump())
        columns=[row[1] for row in db.execute('PRAGMA table_info(signals)')]
    report={'original_records_unchanged':before==snapshot(),'model_calls':sum(row['model_calls'] for row in snapshot().values()),
        'shared_fields':columns,'private_marker_absent':private not in projection_dump,
        'private_ids_absent':document['id'] not in projection_dump and task['id'] not in projection_dump,
        'password_absent':'fixture-only-password' not in projection_dump}
    (args.data_dir.parent/'fixture-state.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
