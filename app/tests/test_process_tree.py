import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace as NS

from tools.process_tree import run_owned, WindowsJob


class ProcessTreeTests(unittest.TestCase):
    def test_uncertain_cleanup_is_not_a_recoverable_oserror(self):
        job = WindowsJob.__new__(WindowsJob)
        job.handle = 1
        job.api = NS(TerminateJobObject=lambda *a: False, CloseHandle=lambda *a: True)
        job.c = NS(WinError=lambda *a: OSError('termination failed'), get_last_error=lambda: 5)
        with self.assertRaises(RuntimeError):
            job.close()
        self.assertIsNone(job.handle)

    def test_normal_exit_code_and_timeout_kills_grandchildren(self):
        result = run_owned([sys.executable, '-c', 'raise SystemExit(7)'], timeout=10)
        self.assertEqual(result, 7)
        with tempfile.TemporaryDirectory() as folder:
            heartbeat = Path(folder) / 'heartbeat.txt'
            child = ("import time; from pathlib import Path; p=Path(" + repr(str(heartbeat)) + "); "
                     "\nwhile True:\n p.write_text(str(time.time())); time.sleep(.05)")
            launcher = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c'," + repr(child) + "]); time.sleep(30)"
            with self.assertRaises(subprocess.TimeoutExpired):
                run_owned([sys.executable, '-c', launcher], timeout=2)
            self.assertTrue(heartbeat.exists())
            stamp = heartbeat.read_text()
            time.sleep(.2)
            self.assertEqual(heartbeat.read_text(), stamp)
