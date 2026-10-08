"""First-use SQLite schema races across API threads and real child processes."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import time

from fastapi.testclient import TestClient

from research_workspace.api import create_app
from research_workspace.discovery import DiscoveryService
from research_workspace.library import Library, _schema_lock


def test_concurrent_first_open_initializes_both_databases_once(tmp_path):
    start = threading.Barrier(8)

    def initialize(index):
        start.wait(timeout=5)
        for _ in range(3):
            library = Library(tmp_path)
            discovery = DiscoveryService(library)
            assert discovery.list_subscriptions() == []
            assert library.list_documents() == []
        with library._connection() as db:
            db.execute('INSERT INTO version_families VALUES(?,?,?)',(str(index),None,'fixture'))

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(initialize,range(8)))
    for name in ('library.sqlite3','discovery.sqlite3'):
        with sqlite3.connect(tmp_path/name) as db:
            assert db.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
            assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    with sqlite3.connect(tmp_path/'library.sqlite3') as db:
        assert db.execute('SELECT COUNT(*) FROM version_families').fetchone()[0] == 8


def test_concurrent_migration_adds_missing_columns_without_duplicate_alter(tmp_path):
    library = Library(tmp_path)
    with library._connection() as db:
        db.execute('ALTER TABLE parse_runs DROP COLUMN cursor_page')
        db.execute('ALTER TABLE parse_runs DROP COLUMN scheduled_at')
    start = threading.Barrier(6)

    def initialize(_):
        start.wait(timeout=5)
        reopened = Library(tmp_path)
        with reopened._connection() as db:
            columns = [row[1] for row in db.execute('PRAGMA table_info(parse_runs)')]
        assert columns.count('cursor_page') == columns.count('scheduled_at') == 1

    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(initialize,range(6)))


def test_real_processes_wait_for_schema_lock_then_initialize(tmp_path):
    code = '''
from pathlib import Path
import sys
from research_workspace.library import Library
from research_workspace.discovery import DiscoveryService
root=Path(sys.argv[1])
index=sys.argv[2]
(root/('ready-'+index)).touch()
for _ in range(3):
    library=Library(root)
    service=DiscoveryService(library)
    assert library.list_documents()==[]
    assert service.list_subscriptions()==[]
(root/('done-'+index)).touch()
'''
    processes = []
    root = Path(__file__).resolve().parents[1]
    try:
        # The parent lock forces a real cross-process overlap. The child must
        # wait rather than fail or initialize through another lock owner.
        with _schema_lock(tmp_path/'library.sqlite3'):
            for index in range(3):
                processes.append(subprocess.Popen([sys.executable,'-c',code,str(tmp_path),str(index)],
                    cwd=root,stdout=subprocess.PIPE,stderr=subprocess.PIPE))
            deadline = time.monotonic()+15
            while len(list(tmp_path.glob('ready-*'))) < 3 and time.monotonic()<deadline:
                time.sleep(.025)
            assert len(list(tmp_path.glob('ready-*'))) == 3
            assert not list(tmp_path.glob('done-*'))
            assert not (tmp_path/'library.sqlite3').exists()
        for child in processes:
            stdout,stderr = child.communicate(timeout=20)
            assert child.returncode == 0, stderr.decode(errors='replace')
        assert len(list(tmp_path.glob('done-*'))) == 3
        assert DiscoveryService(Library(tmp_path)).list_subscriptions() == []
    finally:
        for child in processes:
            if child.poll() is None:
                child.kill()
                child.communicate(timeout=5)


def test_new_account_parallel_routes_publish_one_fully_initialized_library(tmp_path,monkeypatch):
    from research_workspace import advanced_api

    app = create_app(tmp_path,start_worker=False)
    constructed = []
    original = advanced_api.Library

    def observed_library(directory):
        constructed.append(directory)
        time.sleep(.05)
        return original(directory)

    monkeypatch.setattr(advanced_api,'Library',observed_library)
    with TestClient(app,base_url='http://127.0.0.1:8765') as client:
        user = client.post('/api/account/register',json={
            'name':'Concurrent','password':'offline-fixture-password'}).json()['user']
        start = threading.Barrier(8)
        paths = ['/api/documents','/api/capabilities','/api/subscriptions','/api/recommendations']*2

        def request(path):
            start.wait(timeout=5)
            response = client.get(path)
            assert response.status_code == 200, response.text

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(request,paths))
    assert len(constructed) == 1
    assert app.state.libraries[user['id']].network_rate_path == tmp_path/'.arxiv-network.sqlite3'
