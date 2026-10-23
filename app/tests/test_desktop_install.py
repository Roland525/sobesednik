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


class RetryInstallationTests(unittest.TestCase):
    def test_retry_closes_owned_server_and_reuses_existing_python(self):
        from desktop.install import Installer
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); (root/'runtime').mkdir(); (root/'runtime/python.exe').touch()
            (root/'runtime/.complete').touch(); (root/'.venv/Scripts').mkdir(parents=True)
            python=root/'.venv/Scripts/python.exe'; python.write_bytes(b'existing launcher')
            runtime=Mock(); runtime.running.return_value=False; runtime.python=python
            commands=[]
            def step(command,*args):
                runtime.close_server.assert_called_once()
                commands.append(command)
                if 'install' in command: raise RuntimeError('dependency checkpoint')
            with patch('desktop.install.install_payload'), patch('desktop.install.run_step',side_effect=step), patch('desktop.install.shutil.disk_usage') as disk:
                disk.return_value.free=100*1024**3
                with self.assertRaisesRegex(RuntimeError,'dependency checkpoint'):
                    Installer(root,root/'payload.zip',lambda *args:None,runtime=runtime).run()
            self.assertEqual(python.read_bytes(),b'existing launcher')
            self.assertEqual(commands[0][0],python)
            self.assertIn('-c',commands[0])
            self.assertFalse(any('venv' in c for c in commands))

    def test_close_server_leaves_bot_process_untouched(self):
        from desktop.runtime import Runtime
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as folder:
            runtime=Runtime(folder); bot=Mock(); server=Mock(); log=Mock()
            runtime.process=bot; runtime.server=server; runtime.server_log=log
            runtime.close_server()
            server.close.assert_called_once(); log.close.assert_called_once()
            bot.close.assert_not_called()
            self.assertIsNone(runtime.server)
            self.assertIsNone(runtime.server_log)
