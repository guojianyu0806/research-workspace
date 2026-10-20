"""Real isolated worker trees must exit and release locks, not just launchers."""
import asyncio
import os
from pathlib import Path
import subprocess
import sys
import time
import json
import signal

import pytest

from research_workspace.worker_process import stop_worker,start_worker_process
from scripts.local_process_stats import descendants
from scripts.retained_process_tree import OwnedTree


def test_service_worker_startup_uses_null_stdin_and_stops_actual_tree(tmp_path,monkeypatch):
    from research_workspace import api
    from research_workspace.worker import WorkerLock
    for name in ('RESEARCH_MODEL_BASE_URL','RESEARCH_MODEL','RESEARCH_MODEL_API_KEY'):
        monkeypatch.delenv(name,raising=False)
    original=subprocess.Popen;launched=[];trees=[]
    def launch(command,**kwargs):
        assert kwargs['stdin']==subprocess.DEVNULL
        proc=original(command,**kwargs);launched.append(proc);trees.append(OwnedTree(proc));return proc
    monkeypatch.setattr(api.subprocess,'Popen',launch)
    # taskkill is a different subprocess.run path. Do not intercept its stdio
    # with worker-specific assertions (also retain Popen context manager).
    def worker_launch(command,**kwargs):
        if command[0]=='taskkill':return original(command,**kwargs)
        return launch(command,**kwargs)
    monkeypatch.setattr(api.subprocess,'Popen',worker_launch)
    app=api.create_app(tmp_path,start_worker=True)
    async def run():
        async with app.router.lifespan_context(app):
            assert len(launched)==2
            deadline=time.monotonic()+5
            while time.monotonic()<deadline:
                for tree in trees:tree.refresh()
                if sum(len(t.observed) for t in trees)>=4 or os.name!='nt':break
                await asyncio.sleep(.05)
            assert all(p.poll() is None for p in launched)
    try:
        asyncio.run(run())
        reports=[tree.report() for tree in trees]
        (tmp_path/'observed-shutdown.json').write_text(json.dumps(reports,indent=2))
        assert not any(p['running'] for tree in reports for p in tree['observed']),reports
        with WorkerLock(tmp_path),WorkerLock(tmp_path/'.activity'):
            pass
    finally:
        for tree in trees:tree.close()


def test_stop_already_exited_worker_is_noop():
    proc=subprocess.Popen([sys.executable,'-c','pass'],stdin=subprocess.DEVNULL)
    proc.wait(timeout=10)
    stop_worker(proc)


@pytest.mark.skipif(os.name!='nt',reason='Windows process objects and launcher lifecycle')
def test_stop_exited_managed_launcher_reaps_original_child_before_returning(tmp_path):
    child_ready=tmp_path/'child.json';release=tmp_path/'release'
    child_code="import os,sys,time;from pathlib import Path;Path(sys.argv[1]).write_text(str(os.getpid()));time.sleep(120)"
    code="""
import subprocess,sys,time
from pathlib import Path
subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2]],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
while not Path(sys.argv[3]).exists():time.sleep(.02)
"""
    proc=start_worker_process([sys.executable,'-c',code,child_code,str(child_ready),str(release)],
                          stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    tree=OwnedTree(proc)
    try:
        deadline=time.monotonic()+15
        while not child_ready.exists() and time.monotonic()<deadline:
            tree.refresh();time.sleep(.02)
        assert child_ready.exists();tree.refresh()
        assert int(child_ready.read_text()) in tree.observed
        release.write_text('exit')
        proc.wait(timeout=10)
        assert tree.living(), 'Original child must still be running after launcher exits'
        stop_worker(proc)
        report=tree.report();(tmp_path/'orphan-reaped.json').write_text(json.dumps(report,indent=2))
        assert not any(p['running'] for p in report['observed']),report
    finally:
        tree.close()


def test_process_tree_discovery_is_bounded_and_transitive():
    assert descendants(10,[(10,1),(11,10),(12,11),(20,1)])=={10,11,12}


def test_service_crash_stops_real_workers_and_releases_locks(tmp_path):
    from research_workspace.worker import WorkerLock
    ready=tmp_path/'owner.json'; workspace=tmp_path/'workspace'
    code='''
import asyncio,json,os,sys
from pathlib import Path
from research_workspace.api import create_app
async def main():
 app=create_app(Path(sys.argv[1]),start_worker=True)
 async with app.router.lifespan_context(app):
  Path(sys.argv[2]).write_text(json.dumps({'pid':os.getpid()}))
  await asyncio.sleep(120)
asyncio.run(main())
'''
    env=os.environ.copy();env['RESEARCH_DATA_DIR']=str(workspace)
    for name in ('RESEARCH_MODEL_BASE_URL','RESEARCH_MODEL','RESEARCH_MODEL_API_KEY'):
        env.pop(name,None)
    with (tmp_path/'owner.log').open('wb') as log:
        owner=subprocess.Popen([sys.executable,'-c',code,str(workspace),str(ready)],
            env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=log)
        tree=OwnedTree(owner)
        try:
            deadline=time.monotonic()+25
            while time.monotonic()<deadline:
                tree.refresh()
                locked=[]
                for directory in (workspace,workspace/'.activity'):
                    try:
                        with WorkerLock(directory):pass
                        locked.append(False)
                    except RuntimeError:locked.append(True)
                if ready.exists() and all(locked):break
                assert owner.poll() is None
                time.sleep(.1)
            else:pytest.fail('Real API workers did not acquire both locks')
            pid=json.loads(ready.read_text())['pid']
            assert pid in tree.observed and len(tree.observed)>=3
            # Kill only the actual API, deliberately bypassing graceful shutdown
            # and taskkill /T. The workers must observe their owner disappearing.
            os.kill(pid,signal.SIGTERM)
            owner.wait(timeout=10)
            deadline=time.monotonic()+10
            while tree.living() and time.monotonic()<deadline:
                time.sleep(.1)
            report=tree.report()
            (tmp_path/'observed-processes.json').write_text(json.dumps(report,indent=2))
            assert not tree.living(), report
            with WorkerLock(workspace),WorkerLock(workspace/'.activity'):pass
        finally:
            stop_worker(owner)
            tree.close()
