"""Empty isolated HTTP workspace for frozen CSV API and browser acceptance."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import socket
import sqlite3
import sys
import threading
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.freeze_local_acceptance import main_snapshot,sha256
from scripts.freeze_csv_extremes import DEFAULT,verify


def main(*, input_directory=DEFAULT, manifest_loader=verify, family='csv-extremes', include_cases=True):
    output=ROOT/'artifacts'/family/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'-http')
    output.mkdir(parents=True,exist_ok=False)
    before=main_snapshot();manifest=manifest_loader()
    names=['research_workspace/visualization.py','research_workspace/chart_numeric.py','research_workspace/chart_layout.py',
           'research_workspace/radar.py','research_workspace/figure_layout.py','research_workspace/advanced_api.py',
           'research_workspace/static/research.js','research_workspace/static/research.css']
    hashes={name:sha256(ROOT/name) for name in names}
    for name in ('RESEARCH_MODEL_BASE_URL','RESEARCH_MODEL','RESEARCH_MODEL_API_KEY'):os.environ.pop(name,None)
    os.environ['RESEARCH_DATA_DIR']=str(output/'workspace');os.environ['RESEARCH_SEMANTIC_ENABLED']='off'
    from research_workspace.api import create_app
    import uvicorn
    app=create_app(output/'workspace',start_worker=False)
    sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(128)
    server=uvicorn.Server(uvicorn.Config(app,log_level='error'))
    thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True);thread.start()
    try:
        deadline=time.monotonic()+15
        while not server.started:
            if time.monotonic()>deadline:raise RuntimeError('Fixture startup timeout')
            time.sleep(.02)
        print(json.dumps(dict(output=str(output),origin=f'http://127.0.0.1:{sock.getsockname()[1]}',
                              fixture=str(input_directory),cases=manifest['cases'] if include_cases else None)),flush=True)
        for line in sys.stdin:
            if line.strip()=='stop':break
    finally:
        server.should_exit=True;thread.join(timeout=8)
        assert not thread.is_alive()
        assert before==main_snapshot()
        assert hashes=={name:sha256(ROOT/name) for name in names}
        model_calls=0
        for path in (output/'workspace').rglob('library.sqlite3'):
            with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:
                if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='model_calls'").fetchone():
                    model_calls+=db.execute('SELECT COUNT(*) FROM model_calls').fetchone()[0]
        assert model_calls==0
        (output/'completion.json').write_text(json.dumps(dict(main_unchanged=True,stopped=True,model_calls=0,
            runtime_sha256=hashes,manifest_sha256=sha256(input_directory/'manifest.json'))),encoding='utf-8')


if __name__=='__main__':main()
