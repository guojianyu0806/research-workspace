"""Small stdlib process-tree telemetry for owned local acceptance children."""
import ctypes
import os
from pathlib import Path
import subprocess


def processes():
    if os.name != 'nt':
        rows=[]
        for p in Path('/proc').glob('[0-9]*/stat'):
            try:
                text=p.read_text();fields=text[text.rfind(')')+2:].split()
                rows.append((int(p.parent.name),int(fields[1])))
            except (OSError,ValueError):pass
        return rows
    from ctypes import wintypes as w
    class Entry(ctypes.Structure):
        _fields_=[('dwSize',w.DWORD),('cntUsage',w.DWORD),('th32ProcessID',w.DWORD),
            ('th32DefaultHeapID',ctypes.c_size_t),('th32ModuleID',w.DWORD),('cntThreads',w.DWORD),
            ('th32ParentProcessID',w.DWORD),('pcPriClassBase',w.LONG),('dwFlags',w.DWORD),('szExeFile',w.WCHAR*260)]
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateToolhelp32Snapshot.restype=w.HANDLE
    kernel.Process32FirstW.argtypes=[w.HANDLE,ctypes.POINTER(Entry)]
    kernel.Process32NextW.argtypes=[w.HANDLE,ctypes.POINTER(Entry)]
    kernel.CloseHandle.argtypes=[w.HANDLE]
    handle=kernel.CreateToolhelp32Snapshot(2,0)
    if handle==ctypes.c_void_p(-1).value:raise OSError(ctypes.get_last_error())
    entry=Entry();entry.dwSize=ctypes.sizeof(Entry);rows=[]
    try:
        ok=kernel.Process32FirstW(handle,ctypes.byref(entry))
        while ok:
            rows.append((int(entry.th32ProcessID),int(entry.th32ParentProcessID)))
            ok=kernel.Process32NextW(handle,ctypes.byref(entry))
    finally:kernel.CloseHandle(handle)
    return rows


def descendants(root, rows=None):
    rows=processes() if rows is None else rows
    found={root}
    while True:
        previous=len(found)
        found.update(pid for pid,parent in rows if parent in found)
        if len(found)==previous:return found


def alive(pid):
    if os.name!='nt':
        try:
            stat=(Path('/proc')/str(pid)/'stat').read_text()
            return stat[stat.rfind(')')+2:].split()[0]!='Z'
        except OSError:return False
    from ctypes import wintypes as w
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes=[w.DWORD,w.BOOL,w.DWORD];kernel.OpenProcess.restype=w.HANDLE
    kernel.GetExitCodeProcess.argtypes=[w.HANDLE,ctypes.POINTER(w.DWORD)]
    kernel.CloseHandle.argtypes=[w.HANDLE]
    handle=kernel.OpenProcess(0x1000,False,pid)
    if not handle:return False
    value=w.DWORD()
    try:return bool(kernel.GetExitCodeProcess(handle,ctypes.byref(value))) and value.value==259
    finally:kernel.CloseHandle(handle)


def rss(pid):
    if os.name != 'nt':
        try:return int((Path('/proc')/str(pid)/'statm').read_text().split()[1])*os.sysconf('SC_PAGE_SIZE')
        except (OSError,ValueError,IndexError):return 0
    from ctypes import wintypes as w
    class Memory(ctypes.Structure):
        _fields_=[('cb',w.DWORD),('PageFaultCount',w.DWORD)]+[(name,ctypes.c_size_t) for name in
            ['PeakWorkingSetSize','WorkingSetSize','QuotaPeakPagedPoolUsage','QuotaPagedPoolUsage',
             'QuotaPeakNonPagedPoolUsage','QuotaNonPagedPoolUsage','PagefileUsage','PeakPagefileUsage','PrivateUsage']]
    kernel=ctypes.WinDLL('kernel32',use_last_error=True);psapi=ctypes.WinDLL('psapi')
    kernel.OpenProcess.argtypes=[w.DWORD,w.BOOL,w.DWORD];kernel.OpenProcess.restype=w.HANDLE
    kernel.CloseHandle.argtypes=[w.HANDLE]
    psapi.GetProcessMemoryInfo.argtypes=[w.HANDLE,ctypes.POINTER(Memory),w.DWORD]
    handle=kernel.OpenProcess(0x1000|0x10,False,pid)
    if not handle:return 0
    value=Memory();value.cb=ctypes.sizeof(value)
    try:
        return int(value.WorkingSetSize) if psapi.GetProcessMemoryInfo(handle,ctypes.byref(value),ctypes.sizeof(value)) else 0
    finally:kernel.CloseHandle(handle)


def snapshot(root):
    values={str(pid):rss(pid) for pid in descendants(root)}
    return {'root_pid':root,'rss_bytes':sum(values.values()),'processes':values}


def stop_owned_tree(process):
    """Only accepts a retained Popen handle, never arbitrary discovered PIDs."""
    if process.poll() is not None:return
    if os.name=='nt':
        subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW,timeout=10,check=False)
    else:
        process.terminate()
    try:process.wait(timeout=5)
    except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
