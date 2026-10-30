"""Prepare a NEW public-paper citation graph workspace without credentials.

Retains original IDs and original PDF SHA-256 values from the real 22-paper
case. Copies only four document tables plus public papers/imports metadata.
No model, discovery network, worker, account export or whole-database copy.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
CASE = ROOT/'artifacts/real-graph/20261016T154133822925Z'
SOURCE = CASE/'library'
LIBRARY_TABLES = ('documents', 'parse_runs', 'pages', 'blocks')
PUBLIC_METADATA_TABLES = ('papers', 'imports')
FORBIDDEN = re.compile(r'\bsk-[A-Za-z0-9_-]{12,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def connect_readonly(path):
    db = sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    db.execute('BEGIN')
    return db


def read_rows(db, tables):
    result = {}
    for table in tables:
        result[table] = [dict(r) for r in db.execute('SELECT * FROM '+table)]
        for index, row in enumerate(result[table]):
            for field, value in row.items():
                if isinstance(value, str) and FORBIDDEN.search(value):
                    raise ValueError(f'Credential-like content rejected at {table}[{index}].{field}; value not displayed')
    return result


def original_path(directory, filename):
    if Path(filename).name != filename or any(c in filename for c in '/\\:'):
        raise ValueError('Unsafe PDF original name')
    path = (directory/filename).resolve()
    if not path.is_relative_to(directory.resolve()) or not path.is_relative_to(ROOT):
        raise ValueError('Original must remain in the named project directory')
    return path


def insert_rows(db, table, rows):
    allowed = {r['name'] for r in db.execute('PRAGMA table_info('+table+')')}
    for row in rows:
        columns = [c for c in row if c in allowed]
        db.execute(f'INSERT INTO {table} ({",".join(columns)}) VALUES ({",".join("?" for _ in columns)})',
                   [row[c] for c in columns])


def prepare(output):
    freeze = json.loads((CASE/'results.json').read_text(encoding='utf-8'))
    expected = {r['document_id']: r for r in freeze['documents']}
    imported = json.loads((CASE/'actual-discovery-import.json').read_text(encoding='utf-8'))['document']
    expected[imported['id']] = {'document_id': imported['id'], 'filename': imported['filename'], 'sha256': imported['sha256']}
    if len(expected) != 22:
        raise ValueError('The frozen real public-paper case must contain exactly 22 documents')
    src = connect_readonly(SOURCE/'library.sqlite3')
    catalog = connect_readonly(SOURCE/'discovery.sqlite3')
    try:
        library_rows = read_rows(src, LIBRARY_TABLES)
        public_rows = read_rows(catalog, PUBLIC_METADATA_TABLES)
        if {r['id'] for r in library_rows['documents']} != set(expected):
            raise ValueError('Document membership differs from the frozen public-paper case')
        doc_ids = set(expected)
        run_ids = {r['id'] for r in library_rows['parse_runs']}
        if len(run_ids) != 22 or any(r['status'] != 'completed' or r['document_id'] not in doc_ids
                                      for r in library_rows['parse_runs']):
            raise ValueError('Expected exactly 22 completed source parses')
        if any(r['run_id'] not in run_ids for table in ('pages', 'blocks') for r in library_rows[table]):
            raise ValueError('A page or block is outside the retained parses')
        for row in library_rows['documents']:
            frozen = expected[row['id']]
            path = original_path(SOURCE/'originals', row['storage_name'])
            if row['sha256'] != frozen['sha256'] or row['filename'] != frozen['filename'] or sha(path) != row['sha256']:
                raise ValueError('Public PDF does not match its frozen source record: '+row['filename'])
        paper_ids = {r['id'] for r in public_rows['papers']}
        for row in public_rows['papers']:
            record = json.loads(row['record'])
            if not record.get('metadata_verified') or not str(record.get('source_url', '')).startswith('https://arxiv.org/abs/'):
                raise ValueError('Only existing verified public arXiv metadata is retained')
        if any(r['paper_id'] not in paper_ids or r['document_id'] not in doc_ids for r in public_rows['imports']):
            raise ValueError('A retained public import does not point into the public case')
    finally:
        src.rollback(); src.close(); catalog.rollback(); catalog.close()

    for key in ['RESEARCH_MODEL_API_KEY', 'RESEARCH_MODEL_BASE_URL', 'RESEARCH_MODEL']:
        os.environ[key] = ''
    os.environ['RESEARCH_DATA_DIR'] = str(output)
    os.environ['RESEARCH_OCR_ENABLED'] = '0'
    os.environ['RESEARCH_SEMANTIC_ENABLED'] = '0'
    from research_workspace.library import Library
    output.mkdir(parents=True, exist_ok=False)
    library = Library(output)
    with library._connection() as db:
        db.execute('BEGIN IMMEDIATE')
        for table, rows in library_rows.items():
            insert_rows(db, table, rows)
        if list(db.execute('PRAGMA foreign_key_check')):
            raise ValueError('Retained document source references are not complete')
    # The graph reads only these two discovery tables. Do not import subscriptions,
    # searches, preferences, account schemas, model calls or any whole database.
    with sqlite3.connect(output/'discovery.sqlite3') as db:
        db.row_factory = sqlite3.Row
        db.executescript('''
          CREATE TABLE papers(id TEXT PRIMARY KEY,canonical_id TEXT NOT NULL,
            version INTEGER NOT NULL,record TEXT NOT NULL,first_seen_at TEXT NOT NULL,retrieved_at TEXT NOT NULL);
          CREATE TABLE imports(paper_id TEXT PRIMARY KEY REFERENCES papers(id),document_id TEXT NOT NULL,
            downloaded_at TEXT NOT NULL,source_url TEXT NOT NULL);
        ''')
        for table, rows in public_rows.items():
            insert_rows(db, table, rows)
    for row in library_rows['documents']:
        original = original_path(SOURCE/'originals', row['storage_name'])
        target = original_path(library.files_dir, row['storage_name'])
        with target.open('xb') as stream, original.open('rb') as source:
            shutil.copyfileobj(source, stream)
        if sha(target) != row['sha256']:
            raise ValueError('Copied original PDF hash mismatch')

    manifest = {'created_at': datetime.now(timezone.utc).isoformat(),
                'scope': 'Independent 22-public-paper historical dataset rendered by current code; no model calls and not the five-paper historical-QA workspace.',
                'source_case': CASE.relative_to(ROOT).as_posix(),
                'source_freeze_sha256': sha(CASE/'results.json'),
                'source_actual_import_sha256': sha(CASE/'actual-discovery-import.json'),
                'source_graph_sha256': sha(CASE/'graph-refined-20261017.json'),
                'counts': {t: len(r) for t, r in {**library_rows, **public_rows}.items()},
                'paper_hashes': [{'document_id': r['id'], 'filename': r['filename'], 'sha256': r['sha256']}
                                 for r in library_rows['documents']],
                'excluded': ['accounts/sessions', 'model_calls', 'model configuration', 'preferences',
                             'searches', 'subscriptions', 'whole source database copies'],
                'new_model_calls': 0, 'network_calls': 0, 'workers_started': False}
    # Required by the existing safe offline launcher; this is a separate graph-only
    # dataset and contains no historical QA tasks.
    (output/'submission-demo-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    return library, manifest


def verify(library, manifest):
    from fastapi.testclient import TestClient
    from research_workspace.api import create_app
    with TestClient(create_app(library.data_dir, start_worker=False), base_url='http://127.0.0.1') as client:
        response = client.get('/api/graph')
        if response.status_code != 200:
            raise ValueError('Current graph API did not return HTTP200')
        graph = response.json()
        health = client.get('/api/health').json()
        capabilities = client.get('/api/capabilities').json()
        if capabilities['llm'] or health['worker_running'] or health['activity_worker_running']:
            raise ValueError('The graph-only test must have no model and no workers')
        anchors = 0
        with library._connection() as db:
            def audit(value):
                nonlocal anchors
                if isinstance(value, dict):
                    if all(key in value for key in ('document_id', 'run_id', 'block_id', 'page', 'text')):
                        row = db.execute('''SELECT blocks.text,blocks.page,parse_runs.document_id
                          FROM blocks JOIN parse_runs ON parse_runs.id=blocks.run_id
                          WHERE blocks.id=? AND blocks.run_id=?''', (value['block_id'], value['run_id'])).fetchone()
                        if not row or row['text'] != value['text'] or row['page'] != value['page'] or row['document_id'] != value['document_id']:
                            raise ValueError('A graph source anchor does not match the retained original block')
                        anchors += 1
                    for child in value.values():
                        audit(child)
                elif isinstance(value, list):
                    for child in value:
                        audit(child)
            audit(graph)
        historical = json.loads((CASE/'graph-refined-20261017.json').read_text(encoding='utf-8'))
        before = {(edge['source'], edge['target']) for edge in historical['edges']}
        now = {(edge['source'], edge['target']) for edge in graph['edges']}
        counts = {'nodes': len(graph['nodes']), 'confirmed_edges': len(graph['edges']),
                  'unresolved': len(graph.get('unresolved', [])), 'source_anchors_checked': anchors,
                  'historical_confirmed_edge_membership_equal': now == before,
                  'historical_edges_removed': len(before-now), 'current_edges_added': len(now-before)}
        manifest['verification'] = {'graph_http': 200, 'model_configured': False, 'workers_running': False,
                                    'transport': 'In-process ASGI only; no listening port or external requests', **counts}
        (library.data_dir/'graph-current-render.json').write_text(json.dumps(graph, ensure_ascii=False, indent=2), encoding='utf-8')
        (library.data_dir/'submission-demo-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT/'artifacts/builtin-recording-20261030') or output.exists():
        raise ValueError('Choose a NEW graph directory within artifacts/builtin-recording-20261030; overwrite forbidden')
    library, manifest = prepare(output)
    counts = verify(library, manifest)
    print(json.dumps({'output': str(output), **counts}, ensure_ascii=False))


if __name__ == '__main__':
    main()
