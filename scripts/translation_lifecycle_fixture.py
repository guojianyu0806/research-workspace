"""Owned local server and explicit stdin-only lifecycle changes in test data."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.freeze_local_acceptance import main_snapshot, sha256
from scripts.freeze_translation_lifecycle import DEFAULT, records


def source_snapshot(directory):
    value = main_snapshot(directory)
    return {'tables': {k: v for k, v in value['tables'].items() if k in {'documents', 'parse_runs', 'pages', 'blocks'}},
            'originals': value['originals']}


def main():
    output = ROOT/'artifacts/translation-lifecycle'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output.mkdir(parents=True, exist_ok=False)
    before = main_snapshot()
    paths = [*sorted((ROOT/'research_workspace').rglob('*.py')),
             *sorted((ROOT/'research_workspace/static').glob('*.js')),
             *sorted((ROOT/'research_workspace/static').glob('*.css')),
             ROOT/'research_workspace/static/index.html', Path(__file__), ROOT/'scripts/freeze_translation_lifecycle.py',
             ROOT/'scripts/translation_lifecycle_smoke.cjs', DEFAULT/'manifest.json']
    hashes = {p.relative_to(ROOT).as_posix(): sha256(p) for p in paths}
    for name in ('RESEARCH_MODEL_BASE_URL', 'RESEARCH_MODEL', 'RESEARCH_MODEL_API_KEY'):
        os.environ.pop(name, None)
    os.environ['RESEARCH_SEMANTIC_ENABLED'] = '0'
    os.environ['RESEARCH_OCR_ENABLED'] = '0'
    from research_workspace.api import create_app
    from research_workspace.jobs import JobStore
    from research_workspace.library import Library
    import uvicorn
    manifest = json.loads((DEFAULT/'manifest.json').read_text(encoding='utf-8'))
    source = (ROOT/manifest['public_workspace']).resolve()
    assert source.is_relative_to(ROOT/'artifacts/multilingual-index') and main_snapshot(source) == manifest['raw_snapshot']
    app = create_app(output/'workspace', start_worker=False)
    _, alice = app.state.accounts.register('LifecycleAlice', 'synthetic-lifecycle-password-123')
    app.state.accounts.register('LifecycleBob', 'synthetic-lifecycle-password-456')
    owner = app.state.accounts.directory(alice)
    owner.mkdir(parents=True, exist_ok=True)
    assert not (owner/'library.sqlite3').exists()
    shutil.copy2(source/'library.sqlite3', owner/'library.sqlite3')
    shutil.copytree(source/'originals', owner/'originals')
    library = Library(owner)
    initial_sources = source_snapshot(owner)
    store = JobStore(library)
    original = records(owner, manifest)
    jobs = {}
    log = []
    def result(blocks):
        return {'document_id': manifest['document_id'], 'run_id': manifest['run_id'], 'target_language': 'zh',
                'status': 'completed', 'total_blocks': len(blocks), 'blocks': blocks, 'scope': 'synthetic_lifecycle_fixture'}
    for case in manifest['cases']:
        blocks = [dict(v) for v in original[slice(*case['slice'])]]
        if case['id'] == 'legacy':
            for block in blocks:
                block.pop('block_id')
        if case['id'] == 'duplicate':
            blocks[137]['block_id'] = blocks[10]['block_id']
        job = store.create('translate', {'document_id': manifest['document_id'], 'run_id': manifest['run_id'], 'target_language': 'zh'})
        for i, block in enumerate(blocks):
            store.save_item(job['id'], block.get('block_id', 'legacy-'+str(i)) if case['id'] == 'progress' else 'fixture-'+str(i), block,
                            240 if case['id'] == 'progress' else len(blocks))
        if case['id'] == 'progress':
            store.claim()
        else:
            store.finish(job['id'], 'completed', result=result(blocks))
        jobs[case['id']] = {'id': case['id'], 'job_id': job['id'], 'total': len(blocks),
                           'blocks': blocks, 'block_ids': [b.get('block_id') for b in blocks]}
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
                raise RuntimeError('Owned fixture server startup timeout')
            time.sleep(.02)
        print(json.dumps({'type': 'ready', 'output': str(output), 'origin': f'http://127.0.0.1:{sock.getsockname()[1]}',
            'jobs': [{k: v for k, v in job.items() if k != 'blocks'} for job in jobs.values()]}), flush=True)
        for line in sys.stdin:
            if line.strip() == 'stop':
                break
            command = json.loads(line)
            case = command['case']
            job = jobs[case]
            action = command['action']
            try:
                if action == 'prepend' and case in {'valid', 'legacy', 'duplicate'}:
                    prefix = [dict(v) for v in original[:3]]
                    if case == 'legacy':
                        for block in prefix:
                            block.pop('block_id')
                    job['blocks'] = prefix+job['blocks']
                    # Model the corresponding saved responses as well as the
                    # new assembled result; counters must describe one state.
                    # Existing 200 checkpoint payloads remain untouched.
                    for i, block in enumerate(prefix):
                        store.save_item(job['job_id'], 'fixture-prefix-'+str(i), block, len(job['blocks']))
                    store.finish(job['job_id'], 'completed', result=result(job['blocks']))
                elif action == 'append' and case == 'progress':
                    added = [dict(v) for v in original[88:168]]
                    for block in added:
                        store.save_item(job['job_id'], block['block_id'], block, 240)
                    job['blocks'].extend(added)
                elif action == 'replace' and case == 'progress':
                    block = dict(job['blocks'][42])
                    block['translated_text'] += '\n【受控替换；保留同一原文来源】'+'尺寸变化联动。'*100
                    store.save_item(job['job_id'], block['block_id'], block, 240)
                    job['blocks'] = job['blocks'][:42]+job['blocks'][43:]+[block]
                elif action == 'cancel_done' and case == 'progress':
                    # A simulated already-returned local response is saved even
                    # after cancellation. No provider or worker is invoked.
                    block = dict(original[168])
                    store.save_item(job['job_id'], block['block_id'], block, 240)
                    job['blocks'].append(block)
                    store.finish(job['job_id'], 'cancelled', error='本地生命周期夹具：已保留在途返回，未调用模型。')
                else:
                    raise ValueError('Unsupported isolated fixture action')
                body = store.get(job['job_id'])
                log.append({'case': case, 'action': action, 'status': body['status'], 'saved_blocks': len(job['blocks']),
                    'payload_sha256': hashlib.sha256(json.dumps(job['blocks'], ensure_ascii=False, sort_keys=True).encode()).hexdigest()})
                print(json.dumps({'type': 'ack', 'seq': command['seq'], 'ok': True, 'total': len(job['blocks']),
                    'block_ids': [b.get('block_id') for b in job['blocks']], 'status': body['status']}), flush=True)
            except Exception as error:
                print(json.dumps({'type': 'ack', 'seq': command['seq'], 'ok': False, 'error': type(error).__name__}), flush=True)
    finally:
        server.should_exit = True
        thread.join(10)
        calls = 0
        for database in (output/'workspace').rglob('library.sqlite3'):
            with sqlite3.connect(database.as_uri()+'?mode=ro', uri=True) as db:
                if db.execute("SELECT 1 FROM sqlite_master WHERE name='model_calls'").fetchone():
                    calls += db.execute('SELECT count(*) FROM model_calls').fetchone()[0]
        final = {'stopped': not thread.is_alive(), 'main_unchanged': before == main_snapshot(),
                 'original_source_rows_and_pdfs_unchanged': initial_sources == source_snapshot(owner),
                 'model_calls': calls, 'runtime_sha256': hashes,
                 'runtime_unchanged': all(sha256(ROOT/name) == value for name, value in hashes.items()),
                 'mutations': log, 'owner': owner.relative_to(output).as_posix()}
        with (output/'completion.json').open('x', encoding='utf-8') as stream:
            json.dump(final, stream, ensure_ascii=False, indent=2)
        assert final['stopped'] and final['main_unchanged'] and final['runtime_unchanged'] and final['original_source_rows_and_pdfs_unchanged'] and calls == 0


if __name__ == '__main__':
    main()
