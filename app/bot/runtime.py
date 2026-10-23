import os
from pathlib import Path
import sys

_dll_handles = []


def prepare_runtime():
    """Register project CUDA DLLs before CTranslate2; PyTorch is unnecessary."""
    if sys.platform == 'win32':
        site = Path(sys.prefix) / 'Lib/site-packages'
        for folder in (site / 'nvidia').glob('*/bin'):
            _dll_handles.append(os.add_dll_directory(str(folder)))
            os.environ['PATH'] = str(folder) + os.pathsep + os.environ.get('PATH', '')
    os.environ.setdefault('HF_HUB_OFFLINE', '1')
    os.environ.setdefault('HF_HUB_DISABLE_TELEMETRY', '1')
    os.environ.setdefault('ANONYMIZED_TELEMETRY', 'False')


class AlreadyRunningError(RuntimeError):
    pass


class InstanceLock:
    """OS-held lock: releases on crashes without trusting a stale PID file."""
    def __init__(self, path):
        self.path = Path(path)
        self.file = None

    def __enter__(self):
        self.file = self.path.open('a+b')
        try:
            if os.fstat(self.file.fileno()).st_size == 0:
                self.file.write(b'0')
                self.file.flush()
            self.file.seek(0)
            if sys.platform == 'win32':
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise AlreadyRunningError('Собеседник уже запущен. Второй экземпляр не запущен.') from None
        return self

    def __exit__(self, *args):
        if self.file:
            self.file.close()
