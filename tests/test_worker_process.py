"""Real isolated worker trees must exit and release locks, not just launchers."""
import asyncio
import os
from pathlib import Path
import subprocess
import sys
import time

from research_workspace.worker_process import stop_worker
from scripts.local_process_stats import alive, descendants


def test_service_worker_startup_uses_null_stdin_and_stops_actual_tree(tmp_path,monkeypatch):
    from research_workspace import api
    from research_workspace.worker import WorkerLock
    for name in ('RESEARCH_MODEL_BASE_URL','RESEARCH_MODEL','RESEARCH_MODEL_API_KEY'):
        monkeypatch.delenv(name,raising=False)
    original=subprocess.Popen;launched=[]
    def launch(command,**kwargs):
        assert kwargs['stdin']==subprocess.DEVNULL
        proc=original(command,**kwargs);launched.append(proc);return proc
    monkeypatch.setattr(api.subprocess,'Popen',launch)
    # taskkill is a different subprocess.run path. Do not intercept its stdio
    # with worker-specific assertions (also retain Popen context manager).
    def worker_launch(command,**kwargs):
        if command[0]=='taskkill':return original(command,**kwargs)
        return launch(command,**kwargs)
    monkeypatch.setattr(api.subprocess,'Popen',worker_launch)
    app=api.create_app(tmp_path,start_worker=True)
    owned=set()
    async def run():
        async with app.router.lifespan_context(app):
            assert len(launched)==2
            deadline=time.monotonic()+5
            while time.monotonic()<deadline:
                owned.update(pid for p in launched for pid in descendants(p.pid))
                if len(owned)>=4 or os.name!='nt':break
                await asyncio.sleep(.05)
            assert all(p.poll() is None for p in launched)
    asyncio.run(run())
    assert not any(alive(pid) for pid in owned)
    with WorkerLock(tmp_path),WorkerLock(tmp_path/'.activity'):
        pass


def test_stop_already_exited_worker_is_noop():
    proc=subprocess.Popen([sys.executable,'-c','pass'],stdin=subprocess.DEVNULL)
    proc.wait(timeout=10)
    stop_worker(proc)


def test_process_tree_discovery_is_bounded_and_transitive():
    assert descendants(10,[(10,1),(11,10),(12,11),(20,1)])=={10,11,12}
