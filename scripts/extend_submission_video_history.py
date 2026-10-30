"""Extend ONLY the existing public, workers-off video workspace.

Snapshots the local demo first, rehearses the append in a NEW offline staging
directory, then appends the same immutable rows to the live 8776 demo. It never
starts a service, a worker, a model request or an account session.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from prepare_submission_demo import ALLOWED_COLUMNS, scan, safe_path

LIVE = ROOT / 'artifacts/submission-video-20261030/demo-data-v1'
GRAPH = ROOT / 'artifacts/builtin-recording-20261030/graph-data-v1'
OUTPUT = ROOT / 'artifacts/video-production-jianying-20261030-v1/history-extension-applied-v3'
JOBS = {
    '8a430bfa781d40e9a6dd6a39bcd27ac5': ('review', '460b9fe'),
    '86aa8a4a64bf4ccdbd8f4be39008cf54': ('future_work', '69754c3'),
    '766095e5caa84442866f264df6bbf316': ('future_filter', '69754c3'),
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_db(path):
    db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    db.execute('BEGIN')
    return db


def select(db, table, field, identifiers):
    if not identifiers:
        return []
    marks = ','.join('?' for _ in identifiers)
    return [dict(r) for r in db.execute(f'SELECT * FROM {table} WHERE {field} IN ({marks})', list(identifiers))]


def safe_records():
    source = read_db(ROOT/'data/library.sqlite3')
    live = read_db(LIVE/'library.sqlite3')
    graph = read_db(GRAPH/'library.sqlite3')
    try:
        existing = [dict(r) for r in live.execute('SELECT * FROM documents')]
        by_sha = {d['sha256']: d for d in existing}
        graph_docs = [dict(r) for r in graph.execute('SELECT * FROM documents')]
        additions = [d for d in graph_docs if d['sha256'] not in by_sha]
        if len(existing) != 5 or len(graph_docs) != 22 or len(additions) != 18:
            raise ValueError('Expected untouched five-paper live demo and frozen 22-paper graph source')
        runs = select(graph, 'parse_runs', 'document_id', [d['id'] for d in additions])
        if len(runs) != 18 or any(r['status'] != 'completed' for r in runs):
            raise ValueError('Expected 18 unchanged completed historical public parses')
        run_ids = [r['id'] for r in runs]
        selected = {
            'documents': additions, 'parse_runs': runs,
            'pages': select(graph, 'pages', 'run_id', run_ids),
            'blocks': select(graph, 'blocks', 'run_id', run_ids),
            'research_jobs': select(source, 'research_jobs', 'id', JOBS),
            'research_job_items': select(source, 'research_job_items', 'job_id', JOBS),
            'research_job_events': select(source, 'research_job_events', 'job_id', JOBS),
        }
        if len(selected['research_jobs']) != 3:
            raise ValueError('Missing fixed historical jobs')
        existing_runs = {r[0] for r in live.execute('SELECT id FROM parse_runs')}
        existing_ids = {d['id'] for d in existing}
        def references(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    if key == 'document_id' and isinstance(child, str) and child and child not in existing_ids:
                        raise ValueError('Historical job references an unretained document')
                    if key == 'run_id' and isinstance(child, str) and child and child not in existing_runs:
                        raise ValueError('Historical job references an unretained parse')
                    if key == 'document_ids' and not set(child) <= existing_ids:
                        raise ValueError('Historical job paper selection is outside the public demo')
                    references(child)
            elif isinstance(value, list):
                for child in value:
                    references(child)
        for job in selected['research_jobs']:
            if job['kind'] != JOBS[job['id']][0] or job['status'] != 'completed':
                raise ValueError('Source job kind/status changed')
            if live.execute('SELECT 1 FROM research_jobs WHERE id=?', (job['id'],)).fetchone():
                raise ValueError('Refuse replacing an existing live job')
            references(json.loads(job['payload']))
            references(json.loads(job['result']))
        for table, rows in selected.items():
            for index, row in enumerate(rows):
                for field, value in row.items():
                    scan(json.loads(value) if field in {'payload', 'result'} and value else value,
                         f'{table}[{index}].{field}')
        for doc in additions:
            if sha(safe_path(GRAPH/'originals', doc['storage_name'])) != doc['sha256']:
                raise ValueError('Public PDF differs from its frozen source SHA-256')
        mapping = [{
            'source_graph_document_id': d['id'], 'filename': d['filename'], 'sha256': d['sha256'],
            'target_document_id': by_sha.get(d['sha256'], d)['id'],
            'retained_existing_identical_pdf': d['sha256'] in by_sha,
        } for d in graph_docs]
        return selected, existing, mapping
    finally:
        for db in (source, live, graph):
            db.rollback()
            db.close()


def snapshot(source_dir, destination):
    """WAL-safe database snapshots plus exact public-file hashes, no raw WAL copy."""
    destination.mkdir(parents=True, exist_ok=False)
    entries = []
    for path in sorted(source_dir.rglob('*')):
        if not path.is_file() or path.name.endswith(('-wal', '-shm', '.lock')):
            continue
        rel = path.relative_to(source_dir)
        target = destination/rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix == '.sqlite3':
            db = read_db(path)
            try:
                tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                # Empty account/usage schemas belong to this demo; refuse to
                # copy any actual account credentials or model receipts.
                for forbidden in ('users', 'sessions', 'model_calls'):
                    if forbidden in tables and db.execute('SELECT COUNT(*) FROM '+forbidden).fetchone()[0]:
                        raise ValueError('Refuse snapshot containing nonempty '+forbidden)
                with sqlite3.connect(target) as backup:
                    db.backup(backup)
            finally:
                db.rollback()
                db.close()
        else:
            with target.open('xb') as out, path.open('rb') as incoming:
                shutil.copyfileobj(incoming, out)
        entries.append({'path': rel.as_posix(), 'bytes': target.stat().st_size, 'sha256': sha(target)})
    return entries


def append(directory, selected):
    created = []
    for doc in selected['documents']:
        target = safe_path(directory/'originals', doc['storage_name'])
        if target.exists():
            raise ValueError('Refuse overwriting an existing public original')
        with target.open('xb') as out, safe_path(GRAPH/'originals', doc['storage_name']).open('rb') as incoming:
            shutil.copyfileobj(incoming, out)
        if sha(target) != doc['sha256']:
            raise ValueError('Copied original SHA-256 mismatch')
        created.append(target.name)
    db = sqlite3.connect(directory/'library.sqlite3', timeout=30)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    try:
        db.execute('BEGIN IMMEDIATE')
        for table, records in selected.items():
            available = {r['name'] for r in db.execute('PRAGMA table_info('+table+')')}
            for row in records:
                fields = [n for n in ALLOWED_COLUMNS[table].split() if n in row and n in available]
                db.execute(f'INSERT INTO {table} ({",".join(fields)}) VALUES ({",".join("?" for _ in fields)})',
                           [row[n] for n in fields])
        if list(db.execute('PRAGMA foreign_key_check')):
            raise ValueError('Appended source references failed foreign-key validation')
        for job in selected['research_jobs']:
            saved = db.execute('SELECT payload,result FROM research_jobs WHERE id=?', (job['id'],)).fetchone()
            if saved['payload'] != job['payload'] or saved['result'] != job['result']:
                raise ValueError('Immutable historical job JSON changed')
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    return created


def verify(directory, existing, mapping, output):
    for key in ('RESEARCH_MODEL_API_KEY', 'RESEARCH_MODEL_BASE_URL', 'RESEARCH_MODEL'):
        os.environ[key] = ''
    os.environ['RESEARCH_DATA_DIR'] = str(directory)
    os.environ['RESEARCH_SEMANTIC_ENABLED'] = '0'
    os.environ['RESEARCH_OCR_ENABLED'] = '0'
    from fastapi.testclient import TestClient
    from research_workspace.api import create_app
    checks = []
    with TestClient(create_app(directory, start_worker=False), base_url='http://127.0.0.1') as client:
        for job_id in JOBS:
            response = client.get('/api/research/tasks/'+job_id)
            if response.status_code != 200:
                raise ValueError(f'Isolated job read failed: {job_id}, HTTP{response.status_code}')
            job = response.json()['job']
            assert job['id'] == job_id and job['status'] == 'completed'
            checks.append({'path': '/api/research/tasks/'+job_id, 'status': 200})
        graph = client.get('/api/graph').json()
        (output/'graph-default-23.json').write_text(json.dumps(graph, ensure_ascii=False, indent=2), encoding='utf-8')
        by_file = {n['filename']: n for n in graph['nodes'] if n.get('filename')}
        source = by_file['1808.04444v1.pdf']
        target = by_file['1602.02410v1.pdf']
        edges = [e for e in graph['edges'] if e['source'] == source['id'] and e['target'] == target['id']]
        assert len(edges) == 1, 'S16 edge must be real and unique'
        edge = edges[0]
        with sqlite3.connect(directory/'library.sqlite3') as db:
            db.row_factory = sqlite3.Row
            for doc in existing:
                row = dict(db.execute('SELECT * FROM documents WHERE id=?', (doc['id'],)).fetchone())
                assert row == doc, 'An existing document was changed'
            for item in edge['evidence']:
                row = db.execute('SELECT text,page,bbox FROM blocks WHERE id=? AND run_id=?', (item['block_id'], item['run_id'])).fetchone()
                assert row and row['text'] == item['text'] and row['page'] == item['page']
        source_page = next(e['page'] for e in edge['evidence'] if e['page'] == 10)
        response = client.get(f"/api/documents/{source['id']}/pages/{source_page}/image")
        # The actual API image path is /api/documents/{id}/pages/{page}/image;
        # preserve any failed route result instead of claiming success.
        image_status = response.status_code
        health = client.get('/api/health').json()
        assert not health['worker_running'] and not health['activity_worker_running']
        assert not client.get('/api/model').json()['configured']
        scoped = client.get('/api/graph', params=[('document_id', m['target_document_id']) for m in mapping]).json()
        (output/'graph-api-scope-22.json').write_text(json.dumps(scoped, ensure_ascii=False, indent=2), encoding='utf-8')
        return {'passed': True, 'jobs': checks, 'default_graph': {'nodes': len(graph['nodes']), 'confirmed_edges': len(graph['edges'])},
                'api_scope_22': {'nodes': len(scoped['nodes']), 'confirmed_edges': len(scoped['edges'])},
                's16': {'source_document': source, 'target_document': target, 'edge': edge, 'image_status': image_status},
                'workers_running': False, 'model_configured': False, 'transport': 'In-process ASGI only; no server started'}


def main():
    if not LIVE.is_relative_to(ROOT/'artifacts') or not (LIVE/'submission-demo-manifest.json').is_file():
        raise ValueError('Only the named existing public demo can be extended')
    if OUTPUT.exists():
        raise ValueError('New production output already exists; no overwrites allowed')
    selected, existing, mapping = safe_records()
    OUTPUT.mkdir(parents=True, exist_ok=False)
    before = snapshot(LIVE, OUTPUT/'backup-before')
    stage = OUTPUT/'staging'
    shutil.copytree(OUTPUT/'backup-before', stage)
    append(stage, selected)
    result = verify(stage, existing, mapping, OUTPUT)
    created = append(LIVE, selected)
    # Existing PDF and historical JSON identity checks in both target datasets.
    old_jobs = read_db(OUTPUT/'backup-before/library.sqlite3')
    live = read_db(LIVE/'library.sqlite3')
    try:
        for old in old_jobs.execute('SELECT * FROM research_jobs'):
            now = live.execute('SELECT * FROM research_jobs WHERE id=?', (old['id'],)).fetchone()
            assert dict(now) == dict(old)
    finally:
        old_jobs.rollback(); old_jobs.close(); live.rollback(); live.close()
    manifest = {'status': 'APPLIED_TO_PUBLIC_8776_DEMO_ONLY', 'live_directory': str(LIVE),
                'backup': str(OUTPUT/'backup-before'), 'before_file_manifest': before,
                'counts_appended': {t: len(r) for t, r in selected.items()}, 'created_originals': created,
                'source_graph_id_mapping': mapping, 'verification': result,
                'historical_jobs': [{'id': j['id'], 'kind': j['kind'], 'actual_source_version': JOBS[j['id']][1],
                                     'payload_sha256': hashlib.sha256(j['payload'].encode()).hexdigest(),
                                     'result_sha256': hashlib.sha256(j['result'].encode()).hexdigest()}
                                    for j in selected['research_jobs']],
                'existing_documents_modified': False, 'existing_historical_jobs_modified': False,
                'services_started_or_restarted': False, 'new_model_calls': 0, 'network_calls': 0,
                'note': 'Default UI is the current 23-public-paper collection, not the original 22 IDs. The API-only 22 scope excludes Transformer v1. Historical replay is not a new Pro acceptance.'}
    (OUTPUT/'report.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'report': str(OUTPUT/'report.json'), 'counts': manifest['counts_appended'],
                      'graph': result['default_graph'], 's16': result['s16']['edge'].get('id')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
