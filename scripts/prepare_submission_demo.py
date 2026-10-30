"""Export selected public papers and unchanged historical jobs to a NEW workspace.

No model request, download, account export, whole-database copy or UI automation.
Use --verify for in-process ASGI checks only; it never opens port 8765.
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
SOURCE = ROOT / 'data/library.sqlite3'
ORIGINALS = ROOT / 'data/originals'
PAPERS = {
    '1706.03762v1.pdf': '0a9bff1be3575cf9fe26a4a3d3a9175bc791ecec90de8b9deba4cff75d2b9476',
    '1706.03762v7.pdf': 'bdfaa68d8984f0dc02beaca527b76f207d99b666d31d1da728ee0728182df697',
    '1810.04805v2.pdf': '5692a5514787a8c6727b4ff3b726a3385798bc68e12138d1d4af83947e2acf6e',
    '2005.11401v4.pdf': '23e3249e9a1e75418d82efecab0ea8c4d033b89c93742f63208d47ce01f21233',
    '1312.3005v1.pdf': '32c65d36793cf14888907ed454ae05e170b59c6772462a1034165458105db756',
}
# The accepted selection records retain distinct ACTUAL old source versions.
JOBS = {
    'bbbf1c9ce1a745fc970a7dc61da8feaa': ('qa', 'ce89267'),
    '87eb4f6ed44c4ee38367515ac403311e': ('qa', '1426497'),
    '658facf12c804abeae432f6bc482543a': ('compare', 'ae87754'),
    '0bf0fa90699f4ab49cd013efe68d5b8f': ('draft', 'ce89267'),
    'e28488b8ea6e4d8fbb29d8e072dff159': ('qa', 'ce89267'),
    'aac298845f7d49c384372ac0121a3627': ('translate', None),
}
TRANSLATION = 'aac298845f7d49c384372ac0121a3627'
ALLOWED_COLUMNS = {
    'documents': 'id filename title sha256 storage_name page_count created_at current_run_id authors archived_at version_family_id',
    'parse_runs': 'id document_id version parser_version status pages_done total_pages text_pages no_text_pages failed_pages error created_at started_at completed_at cancel_requested parent_run_id target_pages cursor_page scheduled_at',
    'pages': 'run_id page width height rotation status text error extraction_method notice',
    'blocks': 'id run_id page ordinal text search_text bbox',
    'research_jobs': 'id kind status payload result error progress total cancel_requested created_at updated_at',
    'research_job_items': 'job_id item_key payload',
    'research_job_events': 'id job_id stage message created_at',
}
SECRET = re.compile(r'\bsk-[A-Za-z0-9_-]{12,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----')
SECRET_FIELDS = {'api_key', 'api_secret', 'access_token', 'refresh_token', 'password', 'authorization', 'cookie'}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def scan(value, path: str) -> None:
    """Reject obvious credentials; report FIELD PATHS only, never matching values."""
    if isinstance(value, str):
        if SECRET.search(value):
            raise ValueError('Secret-like content rejected at ' + path)
    elif isinstance(value, dict):
        for key, item in value.items():
            if SECRET.search(str(key)):
                raise ValueError('Secret-like JSON key rejected at ' + path + '.[redacted-key]')
            if str(key).lower() in SECRET_FIELDS and item:
                raise ValueError('Credential field rejected at ' + path + '.' + str(key))
            scan(item, path + '.' + str(key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            scan(item, f'{path}[{index}]')


def rows(db, table, field, values):
    if not values:
        return []
    marks = ','.join('?' for _ in values)
    return [dict(r) for r in db.execute(f'SELECT * FROM {table} WHERE {field} IN ({marks})', list(values))]


def safe_path(base: Path, name: str) -> Path:
    if Path(name).name != name or any(c in name for c in '/\\:'):
        raise ValueError('Unsafe original filename')
    path = (base / name).resolve()
    if not path.is_relative_to(base.resolve()) or not path.is_relative_to(ROOT):
        raise ValueError('Original path escapes project')
    return path


def preflight():
    if not SOURCE.resolve().is_relative_to(ROOT) or not SOURCE.is_file():
        raise ValueError('Expected project-local source library is unavailable')
    db = sqlite3.connect(SOURCE.resolve().as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    try:
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')  # A consistent source snapshot, including its existing WAL.
        documents = rows(db, 'documents', 'filename', PAPERS)
        if len(documents) != len(PAPERS) or {r['filename'] for r in documents} != set(PAPERS):
            raise ValueError('The five fixed public papers are not uniquely present')
        for item in documents:
            original = safe_path(ORIGINALS, item['storage_name'])
            if not original.is_file() or sha(original) != item['sha256'] or item['sha256'] != PAPERS[item['filename']]:
                raise ValueError('Frozen public PDF hash mismatch: ' + item['filename'])
        runs = rows(db, 'parse_runs', 'document_id', [r['id'] for r in documents])
        runs = [r for r in runs if r['status'] == 'completed']
        run_ids = {r['id'] for r in runs}
        if any(r['current_run_id'] not in run_ids for r in documents):
            raise ValueError('A selected current run is incomplete or missing')
        jobs = rows(db, 'research_jobs', 'id', JOBS)
        if len(jobs) != len(JOBS):
            raise ValueError('A selected historical job is missing')
        selected = {'documents': documents, 'parse_runs': runs,
                    'pages': rows(db, 'pages', 'run_id', run_ids),
                    'blocks': rows(db, 'blocks', 'run_id', run_ids),
                    'research_jobs': jobs,
                    'research_job_items': rows(db, 'research_job_items', 'job_id', JOBS),
                    'research_job_events': rows(db, 'research_job_events', 'job_id', JOBS)}
        document_ids = {r['id'] for r in documents}
        for job in jobs:
            if job['status'] != 'completed' or job['kind'] != JOBS[job['id']][0] or not job['result']:
                raise ValueError('Selected job is not a completed historical result: ' + job['id'])
            def references(value):
                if isinstance(value, dict):
                    for key, item in value.items():
                        if key == 'document_id' and isinstance(item, str) and item and item not in document_ids:
                            raise ValueError('Historical document reference would be lost: ' + job['id'])
                        if key == 'run_id' and isinstance(item, str) and item and item not in run_ids:
                            raise ValueError('Historical run reference would be lost: ' + job['id'])
                        references(item)
                elif isinstance(value, list):
                    for item in value:
                        references(item)
            references(json.loads(job['payload']))
            references(json.loads(job['result']))
        # Scan every selected string before creating the target directory.
        for table, records in selected.items():
            for index, record in enumerate(records):
                for field, value in record.items():
                    location = f'{table}[{index}].{field}'
                    scan(json.loads(value) if field in {'payload', 'result'} and value else value, location)
        return selected
    finally:
        if db.in_transaction:
            db.rollback()
        db.close()


def verify(output: Path):
    # Environment is set before importing api.py's module-level local app.
    from fastapi.testclient import TestClient
    from research_workspace.api import create_app
    checks = []
    with TestClient(create_app(output, start_worker=False), base_url='http://127.0.0.1') as client:
        paths = ['/', '/api/health', '/api/documents', '/api/research/tasks', '/api/capabilities', '/api/model']
        paths += ['/api/research/tasks/' + job_id for job_id in JOBS]
        paths += [f'/api/research/tasks/{TRANSLATION}/translation-blocks?offset=0&limit=10&include_index=true']
        for path in paths:
            response = client.get(path)
            if response.status_code != 200:
                raise ValueError('Isolated in-process endpoint failed: ' + path)
            checks.append({'path': path, 'status': 200})
        health = client.get('/api/health').json()
        if health['worker_running'] or health['activity_worker_running']:
            raise ValueError('Verification must not start a worker')
        if client.get('/api/capabilities').json()['llm'] or client.get('/api/model').json()['configured']:
            raise ValueError('Verification must have no model configured')
        if {j['id'] for j in client.get('/api/research/tasks').json()['jobs']} != set(JOBS):
            raise ValueError('Unexpected task set in isolated workspace')
        page = client.get(paths[-1]).json()
        if page['total'] != 107 or len(page['blocks']) != 10:
            raise ValueError('Historical translation paging is incomplete')
    return {'passed': True, 'checks': checks, 'workers_started': False, 'llm_configured': False,
            'transport': 'in-process TestClient; no listening port or external request',
            'note': 'App startup may create EMPTY target account schemas; no source accounts are read or copied.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--verify', action='store_true')
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT) or output.exists():
        raise ValueError('Output must be a NEW directory inside this project; overwrite is forbidden')
    selected = preflight()
    # This short-lived script never reads an inherited key or applies model settings.
    os.environ['RESEARCH_MODEL_API_KEY'] = ''
    os.environ['RESEARCH_MODEL_BASE_URL'] = ''
    os.environ['RESEARCH_MODEL'] = ''
    os.environ['RESEARCH_SEMANTIC_ENABLED'] = '0'
    os.environ['RESEARCH_OCR_ENABLED'] = '0'
    os.environ['RESEARCH_DATA_DIR'] = str(output)
    from research_workspace.library import Library
    from research_workspace.jobs import JobStore
    output.mkdir(parents=True, exist_ok=False)
    library = Library(output)
    JobStore(library)  # Empty schema only; it does not generate tasks or call a model.
    with library._connection() as target:
        target.execute('BEGIN IMMEDIATE')
        for table, records in selected.items():
            available = {r['name'] for r in target.execute('PRAGMA table_info(' + table + ')')}
            for record in records:
                fields = [name for name in ALLOWED_COLUMNS[table].split() if name in record and name in available]
                target.execute(f'INSERT INTO {table} ({",".join(fields)}) VALUES ({",".join("?" for _ in fields)})',
                               [record[name] for name in fields])
        if list(target.execute('PRAGMA foreign_key_check')):
            raise ValueError('Isolated copied source references failed foreign-key validation')
        for job in selected['research_jobs']:
            row = target.execute('SELECT payload,result FROM research_jobs WHERE id=?', (job['id'],)).fetchone()
            if row['payload'] != job['payload'] or row['result'] != job['result']:
                raise ValueError('Historical job JSON must remain byte-for-byte unchanged')
    papers = []
    for item in selected['documents']:
        source = safe_path(ORIGINALS, item['storage_name'])
        destination = safe_path(library.files_dir, item['storage_name'])
        with destination.open('xb') as stream, source.open('rb') as original:
            shutil.copyfileobj(original, stream)
        if sha(destination) != item['sha256']:
            raise ValueError('Copied public PDF hash mismatch: ' + item['filename'])
        papers.append({key: item[key] for key in ('id', 'filename', 'sha256', 'page_count', 'current_run_id')})
    manifest = {'created_at': datetime.now(timezone.utc).isoformat(), 'source': 'data/library.sqlite3, mode=ro transaction',
                'scope': 'Historical actual public-paper outputs from distinct old code versions; NOT a new Pro run or a unified-version quality acceptance.',
                'counts': {table: len(records) for table, records in selected.items()}, 'papers': papers,
                'runs': [{key: r[key] for key in ('id', 'document_id', 'version', 'parser_version', 'status')}
                         for r in selected['parse_runs']],
                'jobs': [{'id': r['id'], 'kind': r['kind'], 'created_at': r['created_at'],
                          'historical_source_version': JOBS[r['id']][1],
                          'source_version_note': 'Translation exact source commit not asserted' if r['kind'] == 'translate' else 'Actual version retained by accepted case manifest',
                          'result_sha256': hashlib.sha256(r['result'].encode()).hexdigest()} for r in selected['research_jobs']],
                'excluded': ['source accounts/sessions', 'model_calls', 'credentials/config', 'streams', 'preferences', 'whole database', 'derived indexes', 'version-family relations'],
                'model_key_empty': True, 'external_calls': 0,
                'note': 'Do not retry or generate model jobs during an offline historical demonstration.'}
    if args.verify:
        manifest['verification'] = verify(output)
        with library._connection() as target:
            for job in selected['research_jobs']:
                row = target.execute('SELECT payload,result FROM research_jobs WHERE id=?', (job['id'],)).fetchone()
                if row['payload'] != job['payload'] or row['result'] != job['result']:
                    raise ValueError('Verification changed a historical job JSON: ' + job['id'])
    (output/'submission-demo-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'output': str(output), 'counts': manifest['counts'], 'verified': bool(args.verify)}, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, sqlite3.Error) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
