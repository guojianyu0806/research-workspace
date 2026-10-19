"""Shutdown only a background worker owned by this service.

On Windows the virtualenv launcher can own another Python process. Terminating
only its launcher leaves the real worker holding workspace locks and old config.
"""
import os
import subprocess
import threading
import time


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
    if process is None or process.poll() is not None:
        return
    if os.name=='nt':
        result=subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],
            stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,timeout=10,check=False)
        if result.returncode and process.poll() is None:
            raise RuntimeError('后台worker进程树未能停止，请保留当前配置并重试。')
    else:
        process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
