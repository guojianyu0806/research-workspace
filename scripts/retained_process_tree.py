"""Retain identities of acceptance-owned processes, rather than polling bare PIDs.

Windows can recycle a PID while another test starts subprocesses. Keeping the
original process handle also makes failure cleanup target that object only.
"""
import os
from pathlib import Path
import signal

from scripts.local_process_stats import alive, processes


class ObservedProcess:
    def __init__(self, pid):
        self.pid, self.handle = pid, None
        if os.name == 'nt':
            import ctypes
            from ctypes import wintypes as w
            self.ctypes, self.w = ctypes, w
            self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            self.kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
            self.kernel.OpenProcess.restype = w.HANDLE
            self.kernel.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
            self.kernel.WaitForSingleObject.restype = w.DWORD
            self.kernel.TerminateProcess.argtypes = [w.HANDLE, w.UINT]
            self.kernel.CloseHandle.argtypes = [w.HANDLE]
            self.kernel.GetProcessTimes.argtypes = [w.HANDLE, *[ctypes.POINTER(w.FILETIME)]*4]
            self.handle = self.kernel.OpenProcess(0x1000 | 0x100000 | 0x1, False, pid)
            if not self.handle:
                error = ctypes.get_last_error()
                if error in (87, 1168):
                    raise ProcessLookupError(pid)
                raise OSError(error, 'Could not retain an owned process identity')
            times = [w.FILETIME() for _ in range(4)]
            if not self.kernel.GetProcessTimes(self.handle, *[ctypes.byref(t) for t in times]):
                self.close()
                raise OSError(ctypes.get_last_error())
            self.created = times[0].dwLowDateTime | times[0].dwHighDateTime << 32
        else:
            self.created = self._linux_start()

    def _linux_start(self):
        value = (Path('/proc')/str(self.pid)/'stat').read_text()
        return int(value[value.rfind(')')+2:].split()[19])

    def running(self):
        if os.name == 'nt':
            result = self.kernel.WaitForSingleObject(self.handle, 0)
            if result not in (0, 258):
                raise OSError(self.ctypes.get_last_error())
            return result == 258
        try:
            return self._linux_start() == self.created and alive(self.pid)
        except (OSError, ValueError):
            return False

    def terminate(self):
        if not self.running():
            return
        if os.name == 'nt':
            if not self.kernel.TerminateProcess(self.handle, 15):
                raise OSError(self.ctypes.get_last_error())
        else:
            os.kill(self.pid, signal.SIGTERM)

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


class OwnedTree:
    def __init__(self, process):
        self.root = process.pid
        self.observed = {self.root: ObservedProcess(self.root)}
        self.excluded_older = []

    def refresh(self):
        rows = processes()
        added = True
        while added:
            added = False
            for pid, parent in rows:
                if parent not in self.observed or pid in self.observed:
                    continue
                try:
                    child = ObservedProcess(pid)
                except (ProcessLookupError, FileNotFoundError):
                    continue
                if child.created < self.observed[parent].created:
                    # Parent IDs in an OS snapshot may refer to a former process
                    # with the same PID. Such a process is not ours to terminate.
                    self.excluded_older.append(dict(pid=pid, parent=parent))
                    child.close()
                    continue
                self.observed[pid] = child
                added = True

    def living(self):
        return [p.pid for p in self.observed.values() if p.running()]

    def report(self):
        return dict(observed=[dict(pid=p.pid, created=p.created, running=p.running())
                              for p in self.observed.values()], excluded_older=self.excluded_older)

    def close(self):
        try:
            for p in reversed(list(self.observed.values())):
                p.terminate()
        finally:
            for p in self.observed.values():
                p.close()
