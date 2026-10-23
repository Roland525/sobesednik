"""Own a Windows child tree before it can launch native/GPU descendants."""
import json
from pathlib import Path
import signal
import subprocess
import sys
import time


class ProcessCleanupError(RuntimeError):
    """Descendant exit is unconfirmed: do not allocate another GPU workload."""


class WindowsJob:
    def __init__(self):
        import ctypes as c
        from ctypes import wintypes as w
        class IO(c.Structure):
            _fields_ = [(name, c.c_ulonglong) for name in
                        ('ReadOperationCount', 'WriteOperationCount', 'OtherOperationCount',
                         'ReadTransferCount', 'WriteTransferCount', 'OtherTransferCount')]
        class Basic(c.Structure):
            _fields_ = [('ProcessTime', c.c_longlong), ('JobTime', c.c_longlong), ('Flags', w.DWORD),
                        ('MinWorkingSet', c.c_size_t), ('MaxWorkingSet', c.c_size_t),
                        ('ActiveProcessLimit', w.DWORD), ('Affinity', c.c_size_t),
                        ('PriorityClass', w.DWORD), ('SchedulingClass', w.DWORD)]
        class Extended(c.Structure):
            _fields_ = [('Basic', Basic), ('IO', IO), ('ProcessMemory', c.c_size_t),
                        ('JobMemory', c.c_size_t), ('PeakProcessMemory', c.c_size_t),
                        ('PeakJobMemory', c.c_size_t)]
        self.c = c
        self.api = c.WinDLL('kernel32', use_last_error=True)
        self.api.CreateJobObjectW.argtypes = [c.c_void_p, w.LPCWSTR]
        self.api.CreateJobObjectW.restype = w.HANDLE
        self.api.SetInformationJobObject.argtypes = [w.HANDLE, c.c_int, c.c_void_p, w.DWORD]
        self.api.SetInformationJobObject.restype = w.BOOL
        self.api.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        self.api.AssignProcessToJobObject.restype = w.BOOL
        self.api.CloseHandle.argtypes = [w.HANDLE]
        self.api.CloseHandle.restype = w.BOOL
        self.api.TerminateJobObject.argtypes = [w.HANDLE, w.UINT]
        self.api.TerminateJobObject.restype = w.BOOL
        self.api.QueryInformationJobObject.argtypes = [w.HANDLE, c.c_int, c.c_void_p, w.DWORD, c.c_void_p]
        self.api.QueryInformationJobObject.restype = w.BOOL
        class Accounting(c.Structure):
            _fields_ = [('Times', c.c_longlong * 4), ('PageFaults', w.DWORD), ('Total', w.DWORD),
                        ('Active', w.DWORD), ('Terminated', w.DWORD)]
        self.Accounting = Accounting
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise c.WinError(c.get_last_error())
        info = Extended()
        info.Basic.Flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.api.SetInformationJobObject(self.handle, 9, c.byref(info), c.sizeof(info)):
            self.close()
            raise c.WinError(c.get_last_error())

    def assign(self, process):
        if not self.api.AssignProcessToJobObject(self.handle, int(process._handle)):
            raise self.c.WinError(self.c.get_last_error())

    def close(self):
        if self.handle:
            try:
                if not self.api.TerminateJobObject(self.handle, 1):
                    raise self.c.WinError(self.c.get_last_error())
                deadline = time.monotonic() + 15
                while True:
                    info = self.Accounting()
                    if not self.api.QueryInformationJobObject(self.handle, 1, self.c.byref(info), self.c.sizeof(info), None):
                        raise self.c.WinError(self.c.get_last_error())
                    if info.Active == 0:
                        break
                    if time.monotonic() >= deadline:
                        raise ProcessCleanupError('Owned processes did not exit; refusing to start another GPU workload.')
                    time.sleep(.02)
            except OSError as error:
                raise ProcessCleanupError('Cannot confirm owned process exit; refusing GPU restart.') from error
            finally:
                self.api.CloseHandle(self.handle)
                self.handle = None


def run_owned(command, *, cwd=None, env=None, stdout=None, timeout=None):
    """Return exit code; timeout/parent death kills the full owned Windows tree.

    Bootstrap waits on stdin until assignment to the Job Object is complete;
    the real command cannot start descendants outside ownership during a race.
    """
    bootstrap = [sys.executable, str(Path(__file__).resolve()), '--bootstrap']
    windows = sys.platform == 'win32'
    job = WindowsJob() if windows else None
    child = None
    try:
        child = subprocess.Popen(bootstrap, stdin=subprocess.PIPE, stdout=stdout, stderr=subprocess.STDOUT,
                                 cwd=cwd, env=env, text=True, encoding='utf-8',
                                 creationflags=subprocess.CREATE_NO_WINDOW if windows else 0,
                                 start_new_session=not windows)
        if job:
            job.assign(child)
        child.stdin.write(json.dumps([str(value) for value in command]))
        child.stdin.close()
        try:
            return child.wait(timeout=timeout)
        except BaseException:
            if job:
                job.close()
            else:
                import os
                os.killpg(child.pid, signal.SIGKILL)
            child.wait()
            raise
    finally:
        if job:
            job.close()
        if child and child.poll() is None:
            child.kill()
            child.wait()


if __name__ == '__main__':
    if sys.argv[1:] != ['--bootstrap']:
        raise SystemExit('Internal process bootstrap only.')
    command = json.loads(sys.stdin.read())
    raise SystemExit(subprocess.call(command))
