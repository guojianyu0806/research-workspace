"""Shutdown only a background worker owned by this service.

On Windows the virtualenv launcher can own another Python process. Terminating
only its launcher leaves the real worker holding workspace locks and old config.
"""
import os
import subprocess
import threading
import time


def start_worker_process(command,**kwargs):
    """Retain worker ancestry while launchers are still alive on Windows."""
    process=subprocess.Popen(command,**kwargs)
    if os.name!='nt':return process
    from .process_lifecycle import WindowsProcessTree
    tree=WindowsProcessTree(process);lock=threading.RLock();stop=threading.Event()
    process._workspace_tree=tree;process._workspace_tree_lock=lock;process._workspace_watch_stop=stop
    process._workspace_watch_error=None
    with lock:tree.refresh()
    def observe():
        while not stop.wait(.02):
            try:
                with lock:tree.refresh()
                process._workspace_watch_error=None
            except OSError as error:
                process._workspace_watch_error=error
    thread=threading.Thread(target=observe,name='worker-tree-observer',daemon=True)
    process._workspace_watch_thread=thread;thread.start()
    return process


def guard_parent(parent_pid):
    """Exit this worker if its owning API dies, preserving committed checkpoints.

    Windows retains a synchronization handle, so a recycled PID cannot keep an
    orphan alive. Manual CLI workers without an owner retain their old lifetime.
    """
    if parent_pid is None:
        return
    if parent_pid <= 0 or parent_pid == os.getpid():
        raise ValueError('后台worker的父进程编号无效。')
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes as w
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        kernel.OpenProcess.restype = w.HANDLE
        kernel.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
        kernel.WaitForSingleObject.restype = w.DWORD
        kernel.CloseHandle.argtypes = [w.HANDLE]
        handle = kernel.OpenProcess(0x00100000, False, parent_pid)
        if not handle:
            raise RuntimeError('无法观察所属服务进程，worker未启动。')

        def watch():
            result = kernel.WaitForSingleObject(handle, 0xFFFFFFFF)
            kernel.CloseHandle(handle)
            # A failed wait must not leave an unowned worker either.
            os._exit(0 if result == 0 else 2)
    else:
        def watch():
            while True:
                try:
                    os.kill(parent_pid, 0)
                except ProcessLookupError:
                    os._exit(0)
                time.sleep(.5)
    threading.Thread(target=watch, name='worker-owner-watch', daemon=True).start()


def stop_worker(process):
    if process is None:
        return
    if os.name=='nt':
        from .process_lifecycle import WindowsProcessTree
        tree=getattr(process,'_workspace_tree',None)
        if tree is not None:
            event=getattr(process,'_workspace_watch_stop',None)
            if event is not None:
                event.set();process._workspace_watch_thread.join(timeout=5)
                if process._workspace_watch_thread.is_alive():raise RuntimeError('后台worker进程观察未结束，未确认关闭成功。')
        else:tree=WindowsProcessTree(process)
        try:tree.stop(timeout=5)
        except Exception:
            # Preserve original ownership for an explicit retry; do not discard
            # identities while some original process might still be running.
            process._workspace_tree=tree
            raise
        else:
            tree.close();process._workspace_tree=None
    else:
        if process.poll() is not None:return
        process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
