"""Isolated real API with immutable saved translation and synthetic long jobs."""
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import socket
import sqlite3
import sys
import threading
import time

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.freeze_local_acceptance import main_snapshot,sha256
from scripts.freeze_long_reading import DEFAULT,synthetic_blocks,canonical


def main():
    import hashlib
    output=ROOT/'artifacts/long-reader'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output.mkdir(parents=True,exist_ok=False)
    before=main_snapshot();manifest=json.loads((DEFAULT/'manifest.json').read_text(encoding='utf-8'))
    names=['research_workspace/jobs.py','research_workspace/translation_pages.py','research_workspace/advanced_api.py',
           'research_workspace/static/index.html','scripts/long_reader_fixture.py','scripts/long_reader_smoke.cjs',
           'scripts/freeze_long_reading.py','tests/fixtures/long_reading/20261020-v1/manifest.json',
           *[str(p.relative_to(ROOT)).replace('\\','/') for p in (ROOT/'research_workspace/static').glob('*.js')],
           *[str(p.relative_to(ROOT)).replace('\\','/') for p in (ROOT/'research_workspace/static').glob('*.css')]]
    hashes={name:sha256(ROOT/name) for name in names}
    for name in ('RESEARCH_MODEL_BASE_URL','RESEARCH_MODEL','RESEARCH_MODEL_API_KEY'):os.environ.pop(name,None)
    os.environ['RESEARCH_SEMANTIC_ENABLED']='off'
    from research_workspace.api import create_app
    from research_workspace.library import Library
    from research_workspace.jobs import JobStore
    import uvicorn
    app=create_app(output/'workspace',start_worker=False)
    _,user=app.state.accounts.register('LongReaderAlice','synthetic-long-reader-123')
    app.state.accounts.register('LongReaderBob','synthetic-long-reader-456')
    store=JobStore(Library(app.state.accounts.directory(user)))
    saved_path=ROOT/manifest['saved_translation']['path'];assert sha256(saved_path)==manifest['saved_translation']['sha256']
    cases=[dict(id='saved-107',value=json.loads(saved_path.read_text(encoding='utf-8')))]
    for case in manifest['cases']:
        blocks=synthetic_blocks(case['count'],case['variant'])
        assert hashlib.sha256(canonical(blocks)).hexdigest()==case['canonical_sha256']
        cases.append(dict(id=case['id'],value=dict(document_id='synthetic-long-document',run_id='synthetic-long-run-v1',
            target_language='zh',status='completed',total_blocks=len(blocks),missing_pages=[],blocks=blocks)))
    jobs=[]
    for case in cases:
        value=case['value'];job=store.create('translate',{k:value[k] for k in ('document_id','run_id','target_language')})
        store.finish(job['id'],'completed',result=value)
        jobs.append(dict(id=case['id'],job_id=job['id'],total=len(value['blocks']),block_ids=[b['block_id'] for b in value['blocks']]))
    sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(128)
    server=uvicorn.Server(uvicorn.Config(app,log_level='error'))
    thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True);thread.start()
    try:
        deadline=time.monotonic()+15
        while not server.started:
            if time.monotonic()>deadline:raise RuntimeError('Fixture startup timeout')
            time.sleep(.02)
        # IDs only; source bodies must be read through the product's paged API.
        print(json.dumps(dict(output=str(output),origin=f'http://127.0.0.1:{sock.getsockname()[1]}',jobs=jobs)),flush=True)
        for line in sys.stdin:
            if line.strip()=='stop':break
    finally:
        server.should_exit=True;thread.join(timeout=10)
        calls=0
        for path in (output/'workspace').rglob('library.sqlite3'):
            with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:
                if db.execute("SELECT 1 FROM sqlite_master WHERE name='model_calls'").fetchone():calls+=db.execute('SELECT COUNT(*) FROM model_calls').fetchone()[0]
        completion=dict(stopped=not thread.is_alive(),main_unchanged=before==main_snapshot(),model_calls=calls,
            runtime_unchanged=hashes=={name:sha256(ROOT/name) for name in names},runtime_sha256=hashes)
        (output/'completion.json').write_text(json.dumps(completion,ensure_ascii=False,indent=2),encoding='utf-8')
        assert completion['stopped'] and completion['main_unchanged'] and completion['runtime_unchanged'] and calls==0


if __name__=='__main__':main()
