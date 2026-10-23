import hashlib
import io
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile


class DesktopInstallTests(unittest.TestCase):
    def test_missing_heartbeat_does_not_mean_supervisor_has_stopped(self):
        from desktop.runtime import Runtime
        from bot.runtime import InstanceLock
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); runtime=Runtime(root)
            with InstanceLock(root/'.serega-supervisor.lock'):
                self.assertTrue(runtime.running())
                with self.assertRaises(RuntimeError): runtime.stop(timeout=.01)
                self.assertTrue(runtime.running())

    def test_download_checksum_failure_never_commits_target(self):
        from desktop.install import download_file
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'python.zip'
            with patch('urllib.request.urlopen',return_value=io.BytesIO(b'wrong')):
                with self.assertRaises(ValueError):
                    download_file('https://example.test/file',path,'0'*64,lambda *args:None,threading.Event())
            self.assertFalse(path.exists())

    def test_cancelled_download_preserves_existing_good_file(self):
        from desktop.install import download_file,Cancelled
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'good'; path.write_bytes(b'good')
            event=threading.Event(); event.set()
            with self.assertRaises(Cancelled):
                download_file('https://example.test/file',path,hashlib.sha256(b'good').hexdigest(),lambda *args:None,event)
            self.assertEqual(path.read_bytes(),b'good')

    def test_runtime_archive_cannot_escape(self):
        from desktop.install import extract_zip
        with tempfile.TemporaryDirectory() as folder:
            archive=Path(folder)/'bad.zip'; target=Path(folder)/'runtime'
            with zipfile.ZipFile(archive,'w') as z: z.writestr('../escape','bad')
            with self.assertRaises(ValueError): extract_zip(archive,target)
            self.assertFalse((Path(folder)/'escape').exists())

    def test_bot_command_has_no_secret_and_requires_completed_setup(self):
        from desktop.runtime import Runtime
        with tempfile.TemporaryDirectory() as folder:
            runtime=Runtime(Path(folder))
            self.assertFalse(runtime.installed())
            command=runtime.bot_command()
            self.assertEqual(command[-1],'main.py')
            self.assertNotIn('DISCORD_TOKEN',' '.join(command))
