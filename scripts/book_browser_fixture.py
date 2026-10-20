"""Fresh UI workspace copied from a frozen local book provenance checkpoint."""
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import time

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.freeze_local_acceptance import sha256,main_snapshot


def main(source):
    source=Path(source).resolve();assert source.is_relative_to(ROOT/'artifacts/book-reader')
    native=json.loads((source/'native-report.json').read_text(encoding='utf-8'))
    assert all(c['passed'] for c in native['checks']) and len(native['pages'])==541
    candidates=[]
    for p in (source/'workspace').rglob('library.sqlite3'):
        with sqlite3.connect(p.as_uri()+'?mode=ro',uri=True) as db:
            if db.execute('SELECT COUNT(*) FROM documents WHERE id=?',(native['document_id'],)).fetchone()[0]:candidates.append(p)
    assert len(candidates)==1
    source_db=candidates[0]
    output=ROOT/'artifacts/book-reader-ui'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ');output.mkdir(parents=True,exist_ok=False)
    before=main_snapshot();names=[str(p.relative_to(ROOT)).replace('\\','/') for p in [*(ROOT/'research_workspace').rglob('*.py'),*(ROOT/'research_workspace/static').glob('*.js'),*(ROOT/'research_workspace/static').glob('*.css')]]
    names+=['research_workspace/static/index.html','scripts/book_browser_fixture.py','scripts/book_browser_smoke.cjs']
    hashes={name:sha256(ROOT/name) for name in names}
    for name in ('RESEARCH_MODEL_BASE_URL','RESEARCH_MODEL','RESEARCH_MODEL_API_KEY'):os.environ.pop(name,None)
    os.environ['RESEARCH_SEMANTIC_ENABLED']='off';os.environ['RESEARCH_OCR_ENABLED']='off'
    from research_workspace.api import create_app
    from research_workspace.library import Library
    from research_workspace.worker_process import start_worker_process,stop_worker
    from research_workspace.jobs import JobStore
    import uvicorn
    app=create_app(output/'workspace',start_worker=False)
    _,user=app.state.accounts.register('BookUIAlice','local-book-ui-pass-123')
    app.state.accounts.register('BookUIBob','local-book-ui-pass-456')
    library=Library(app.state.accounts.directory(user))
    with sqlite3.connect(source_db.as_uri()+'?mode=ro',uri=True) as original,sqlite3.connect(library.database_path) as target:original.backup(target)
    for path in (source_db.parent/'originals').glob('*.pdf'):shutil.copy2(path,library.files_dir/path.name)
    def historical():
        with library._connection() as db:
            return {name:sha256_json([tuple(r) for r in db.execute('SELECT * FROM '+name+' WHERE '+('document_id=?' if name=='parse_runs' else 'run_id IN (SELECT id FROM parse_runs WHERE document_id=?)'),(native['document_id'],))]) for name in ('parse_runs','pages','blocks')}
    def sha256_json(value):
        import hashlib
        return hashlib.sha256(json.dumps(value,ensure_ascii=False).encode()).hexdigest()
    old=historical();manifest_path=ROOT/'tests/fixtures/book_reading/20261020-v1/manifest.json'
    manifest=json.loads(manifest_path.read_text(encoding='utf-8'));derived=[]
    for case in manifest['derived']:
        path=manifest_path.parent/case['path'];assert sha256(path)==case['sha256']
        doc=library.import_document(path.read_bytes(),path.name)['document'];derived.append(dict(id=doc['id'],path=path.name))
    small=ROOT/'tests/fixtures/local_acceptance/20261019-v1/shared.pdf'
    other=library.import_document(small.read_bytes(),small.name)['document']
    log=(output/'worker.log').open('ab')
    worker=start_worker_process([sys.executable,'-m','research_workspace.worker','--data-dir',str(library.data_dir),'--parent-pid',str(os.getpid()),'--poll-interval','.05'],
        cwd=ROOT,stdin=subprocess.DEVNULL,stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    deadline=time.monotonic()+60
    while any(d['current_run']['status'] in ('queued','running') for d in library.list_documents()):
        if time.monotonic()>deadline:raise RuntimeError('Derived reader sources did not finish')
        time.sleep(.05)
    stop_worker(worker);log.close()
    sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(128)
    server=uvicorn.Server(uvicorn.Config(app,log_level='error'));thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True);thread.start()
    try:
        deadline=time.monotonic()+15
        while not server.started:
            if time.monotonic()>deadline:raise RuntimeError('Reader API startup timeout')
            time.sleep(.02)
        print(json.dumps(dict(output=str(output),origin=f'http://127.0.0.1:{sock.getsockname()[1]}',document_id=native['document_id'],historical_run_id=native['historical_run_id'],current_run_id=native['current_run_id'],derived=derived,other_document_id=other['id'],source_native_report=str(source/'native-report.json'))),flush=True)
        for line in sys.stdin:
            if line.strip()=='stop':break
    finally:
        server.should_exit=True;thread.join(timeout=15)
        JobStore(library)
        with library._connection() as db:calls=db.execute('SELECT COUNT(*) FROM model_calls').fetchone()[0]
        result=dict(stopped=not thread.is_alive(),main_unchanged=before==main_snapshot(),book_history_unchanged=old==historical(),model_calls=calls,runtime_sha256=hashes,runtime_unchanged=hashes=={name:sha256(ROOT/name) for name in names})
        (output/'completion.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        assert result['stopped'] and result['main_unchanged'] and result['book_history_unchanged'] and result['runtime_unchanged'] and calls==0


if __name__=='__main__':main(sys.argv[1])
