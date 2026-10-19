"""Real public PDFs in an isolated, model-free HTTP instance for UI acceptance."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.freeze_local_acceptance import main_snapshot, sha256
from scripts.freeze_affiliation_acceptance import DEFAULT, verify


def main():
    output = ROOT/'artifacts/front-matter-continuation'/(
        datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'-http')
    output.mkdir(parents=True, exist_ok=False)
    before = main_snapshot()
    files = ['research_workspace/front_matter.py', 'research_workspace/structure.py',
             'research_workspace/static/manage.js', 'scripts/affiliation_continuation_fixture.py',
             'scripts/affiliation_continuation_smoke.cjs']
    fingerprints = {name: sha256(ROOT/name) for name in files}
    provenance = dict(source_revision=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
                      source_sha256=fingerprints, manifest_sha256=sha256(DEFAULT/'manifest.json'))
    (output/'provenance.json').write_text(json.dumps(provenance, indent=2), encoding='utf-8')
    manifest = verify(DEFAULT)
    for key in ('RESEARCH_MODEL_BASE_URL', 'RESEARCH_MODEL', 'RESEARCH_MODEL_API_KEY'):
        os.environ.pop(key, None)
    os.environ['RESEARCH_DATA_DIR'] = str(output/'workspace')
    os.environ['RESEARCH_SEMANTIC_ENABLED'] = 'off'
    from research_workspace.api import create_app
    import uvicorn
    app = create_app(output/'workspace', start_worker=False)
    library = app.state.library
    documents, stored = [], []
    filenames = ('1308.0850v1.pdf', '1608.05859v1.pdf')
    for filename in filenames:
        source = next(s for s in manifest['sources'] if Path(s['path']).name == filename)
        path = ROOT/source['path']
        assert sha256(path) == source['sha256']
        doc = library.import_document(path.read_bytes(), filename)['document']
        library.process_next_job()
        library.update_metadata(doc['id'], authors=['Manually Confirmed Fixture Author'])
        old = library.get_document(doc['id'])['current_run']['id']
        old_structure = library.get_structure(doc['id'], old)
        library.create_run(doc['id'])
        library.process_next_job()
        current = library.get_document(doc['id'])['current_run']['id']
        assert old != current
        assert library.get_structure(doc['id'], old) == old_structure
        stored.append(old_structure)
        documents.append(dict(id=doc['id'], filename=filename, old_run=old, current_run=current,
                              expected='Department of Computer Science University of Toronto' if filename.startswith('1308')
                              else 'The Blavatnik School of Computer Science Tel Aviv University'))
    (output/'fixed-historical-structures.json').write_text(json.dumps(stored, ensure_ascii=False, indent=2), encoding='utf-8')
    local_before = main_snapshot(output/'workspace')
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    sock.listen(128)
    server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    try:
        deadline = time.monotonic()+15
        while not server.started:
            if time.monotonic() > deadline:
                raise RuntimeError('Fixture startup timeout')
            time.sleep(.02)
        print(json.dumps(dict(output=str(output), origin=f'http://127.0.0.1:{sock.getsockname()[1]}',
                              documents=documents)), flush=True)
        for line in sys.stdin:
            if line.strip() == 'stop':
                break
    finally:
        server.should_exit = True
        thread.join(timeout=8)
        assert not thread.is_alive(), 'Fixture server did not stop'
        assert before == main_snapshot(), 'Main selected records or originals changed'
        assert local_before == main_snapshot(output/'workspace'), 'Fixed fixture documents/history changed'
        assert fingerprints == {name: sha256(ROOT/name) for name in files}, 'Runtime files changed during acceptance'
        model_calls = 0
        database_checks = []
        for database in (output/'workspace').rglob('library.sqlite3'):
            with sqlite3.connect(database.as_uri()+'?mode=ro', uri=True) as db:
                exists = bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='model_calls'").fetchone())
                count = db.execute('SELECT COUNT(*) FROM model_calls').fetchone()[0] if exists else 0
                model_calls += count
                database_checks.append(dict(database=str(database.relative_to(output)),
                                            model_call_table_present=exists, count=count))
        assert model_calls == 0
        (output/'completion.json').write_text(json.dumps(dict(main_unchanged=True,
            fixture_history_unchanged=True, model_calls=model_calls, databases=database_checks, stopped=True)), encoding='utf-8')


if __name__ == '__main__':
    main()
