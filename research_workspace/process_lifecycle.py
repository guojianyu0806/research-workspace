"""Windows child ownership by retained process objects and lifetime intervals.

Only processes descended from a retained Popen root may be terminated. A PID
reused after an original parent's exit cannot acquire that parent's ownership.
"""
import ctypes
from ctypes import wintypes as w
import time


class ProcessEntry(ctypes.Structure):
    _fields_=[('dwSize',w.DWORD),('cntUsage',w.DWORD),('th32ProcessID',w.DWORD),
        ('th32DefaultHeapID',ctypes.c_size_t),('th32ModuleID',w.DWORD),('cntThreads',w.DWORD),
        ('th32ParentProcessID',w.DWORD),('pcPriClassBase',w.LONG),('dwFlags',w.DWORD),('szExeFile',w.WCHAR*260)]


class WindowsProcessTree:
    def __init__(self,process):
        self.kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        k=self.kernel
        k.OpenProcess.argtypes=[w.DWORD,w.BOOL,w.DWORD];k.OpenProcess.restype=w.HANDLE
        k.CloseHandle.argtypes=[w.HANDLE]
        k.GetProcessTimes.argtypes=[w.HANDLE,*[ctypes.POINTER(w.FILETIME)]*4]
        k.WaitForSingleObject.argtypes=[w.HANDLE,w.DWORD];k.WaitForSingleObject.restype=w.DWORD
        k.TerminateProcess.argtypes=[w.HANDLE,w.UINT]
        k.CreateToolhelp32Snapshot.restype=w.HANDLE
        k.Process32FirstW.argtypes=[w.HANDLE,ctypes.POINTER(ProcessEntry)]
        k.Process32NextW.argtypes=[w.HANDLE,ctypes.POINTER(ProcessEntry)]
        # Popen retains its original Windows process object even after exit.
        self.root=process.pid;self.handles={self.root:int(process._handle)}
        self.owned_handles=set();self.excluded=[]

    def times(self,handle):
        values=[w.FILETIME() for _ in range(4)]
        if not self.kernel.GetProcessTimes(handle,*[ctypes.byref(v) for v in values]):
            raise OSError(ctypes.get_last_error(),'Cannot read owned process lifetime')
        return tuple(v.dwLowDateTime | v.dwHighDateTime<<32 for v in values[:2])

    def snapshot(self):
        handle=self.kernel.CreateToolhelp32Snapshot(2,0)
        if handle==ctypes.c_void_p(-1).value:raise OSError(ctypes.get_last_error())
        entry=ProcessEntry();entry.dwSize=ctypes.sizeof(entry);rows=[]
        try:
            ok=self.kernel.Process32FirstW(handle,ctypes.byref(entry))
            if not ok:raise OSError(ctypes.get_last_error(),'Process snapshot could not be read')
            while ok:
                rows.append((int(entry.th32ProcessID),int(entry.th32ParentProcessID)))
                ok=self.kernel.Process32NextW(handle,ctypes.byref(entry))
            if ctypes.get_last_error()!=18:raise OSError(ctypes.get_last_error(),'Process snapshot ended unexpectedly')
        finally:self.kernel.CloseHandle(handle)
        return rows

    def refresh(self):
        rows=self.snapshot();added=True
        while added:
            added=False
            for pid,parent in rows:
                if pid in self.handles or parent not in self.handles:continue
                handle=self.kernel.OpenProcess(0x100000|0x1000|0x1,False,pid)
                if not handle:
                    error=ctypes.get_last_error()
                    if error in (87,1168):continue
                    raise OSError(error,'Cannot retain a descendant process')
                try:
                    created,_=self.times(handle);parent_created,parent_exited=self.times(self.handles[parent])
                    if created<parent_created or (parent_exited and created>parent_exited):
                        self.excluded.append(pid);continue
                    self.handles[pid]=handle;self.owned_handles.add(pid);handle=None;added=True
                finally:
                    if handle:self.kernel.CloseHandle(handle)

    def living(self):
        result=[]
        for pid,handle in self.handles.items():
            status=self.kernel.WaitForSingleObject(handle,0)
            if status==258:result.append(pid)
            elif status!=0:raise OSError(ctypes.get_last_error(),'Cannot observe owned process exit')
        return result

    def stop(self,timeout=5):
        deadline=time.monotonic()+timeout
        self.refresh()
        while True:
            # Stop execution before the final scan, so worker-created children
            # appearing during the first snapshot cannot escape observation.
            pending=[]
            for pid in self.living():
                if not self.kernel.TerminateProcess(self.handles[pid],15):
                    error=ctypes.get_last_error()
                    if error!=5:raise OSError(error,'Cannot terminate an owned process')
                    # Access denied also occurs during process termination.
                    # It is accepted only if that same object subsequently exits.
                pending.append(pid)
            for pid in pending:
                remaining=max(0,int((deadline-time.monotonic())*1000))
                status=self.kernel.WaitForSingleObject(self.handles[pid],remaining)
                if status==258:raise RuntimeError('后台worker进程尚未完成退出，未确认关闭成功。')
                if status!=0:raise OSError(ctypes.get_last_error(),'Owned process exit wait failed')
            old_count=len(self.handles);self.refresh()
            if not self.living() and len(self.handles)==old_count:return
            if time.monotonic()>=deadline:raise RuntimeError('后台worker子进程退出超时。')

    def close(self):
        for pid in self.owned_handles:self.kernel.CloseHandle(self.handles[pid])
        self.owned_handles.clear()
