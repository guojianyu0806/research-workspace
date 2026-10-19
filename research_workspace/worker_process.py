"""Shutdown only a background worker owned by this service.

On Windows the virtualenv launcher can own another Python process. Terminating
only its launcher leaves the real worker holding workspace locks and old config.
"""
import os
import subprocess


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
