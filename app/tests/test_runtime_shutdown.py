import io
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock,patch

from desktop.runtime import Runtime


class RuntimeShutdownTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.root=Path(self.folder.name)
        self.runtime=Runtime(self.root)

    def tearDown(self):
        # Always release test handles after the assertion, including on red runs.
        for name in ('server_log','log_handle'):
            handle=getattr(self.runtime,name)
            if handle:handle.close()
        self.folder.cleanup()

    def owned_server(self):
        server=Mock();server.poll.return_value=None
        self.runtime.server=server;self.runtime.server_log=io.StringIO()
        return server,self.runtime.server_log

    def test_stop_releases_owned_server_even_when_voice_is_already_stopped(self):
        server,log=self.owned_server()
        with patch.object(self.runtime,'running',return_value=False):self.runtime.stop()
        server.close.assert_called_once();self.assertTrue(log.closed)
        self.assertIsNone(self.runtime.server);self.assertIsNone(self.runtime.server_log)

    def test_stop_waits_for_voice_exit_before_releasing_its_server(self):
        server,log=self.owned_server();bot=Mock();bot.poll.return_value=0
        self.runtime.process=bot;order=[]
        bot.close.side_effect=lambda:order.append('voice')
        server.close.side_effect=lambda:order.append('server')
        with patch.object(self.runtime,'running',return_value=True):self.runtime.stop()
        self.assertEqual(order,['voice','server']);self.assertTrue(log.closed)

    def test_repeated_stop_does_not_repeat_server_termination(self):
        server,_=self.owned_server()
        with patch.object(self.runtime,'running',return_value=False):
            self.runtime.stop();self.runtime.stop()
        server.close.assert_called_once()

    def test_reusing_external_server_does_not_take_ownership_or_send_shutdown_requests(self):
        with patch('desktop.runtime.urllib.request.urlopen',return_value=io.BytesIO(b'{"models":[]}')) as request:
            self.runtime.ensure_ollama()
            with patch.object(self.runtime,'running',return_value=False):self.runtime.stop()
        self.assertIsNone(self.runtime.server)
        self.assertEqual(request.call_count,1)
        self.assertEqual(request.call_args.args[0],'http://127.0.0.1:11434/api/tags')

    def test_cancelled_server_start_closes_its_owned_tree_and_log(self):
        exe=self.root/'tools/ollama/ollama.exe';exe.parent.mkdir(parents=True);exe.touch()
        server=Mock();cancel=threading.Event();cancel.set()
        with patch('desktop.runtime.urllib.request.urlopen',side_effect=OSError('not ready')),\
             patch('desktop.runtime.OwnedProcess',return_value=server):
            with self.assertRaises(InterruptedError):self.runtime.ensure_ollama(cancel)
        server.close.assert_called_once()
        self.assertIsNone(self.runtime.server);self.assertIsNone(self.runtime.server_log)

    def test_start_failure_releases_new_server_and_startup_log(self):
        server,log=self.owned_server()
        with patch.object(self.runtime,'running',return_value=False),\
             patch.object(self.runtime,'installed',return_value=True),\
             patch.object(self.runtime,'ensure_ollama'),\
             patch('desktop.runtime.load_token',return_value='synthetic'),\
             patch('desktop.runtime.OwnedProcess',side_effect=OSError('spawn failure')):
            with self.assertRaisesRegex(OSError,'spawn failure'):self.runtime.start()
        server.close.assert_called_once();self.assertTrue(log.closed)
        self.assertIsNone(self.runtime.server);self.assertIsNone(self.runtime.log_handle)

    def test_startup_log_open_failure_also_releases_server(self):
        server,log=self.owned_server();original=Path.open
        def opening(path,*args,**kwargs):
            if path.name=='desktop-startup.log':raise PermissionError('synthetic log lock')
            return original(path,*args,**kwargs)
        with patch.object(self.runtime,'running',return_value=False),\
             patch.object(self.runtime,'installed',return_value=True),\
             patch.object(self.runtime,'ensure_ollama'),\
             patch('desktop.runtime.load_token',return_value='synthetic'),patch.object(Path,'open',opening):
            with self.assertRaisesRegex(PermissionError,'synthetic log lock'):self.runtime.start()
        server.close.assert_called_once();self.assertTrue(log.closed)
        self.assertIsNone(self.runtime.server)

    def test_close_releases_owned_server_even_if_voice_shutdown_reports_error(self):
        server,log=self.owned_server()
        with patch.object(self.runtime,'stop',side_effect=RuntimeError('voice exit unconfirmed')):
            with self.assertRaisesRegex(RuntimeError,'unconfirmed'):self.runtime.close()
        server.close.assert_called_once();self.assertTrue(log.closed)

    def test_stop_closes_owned_process_after_missing_heartbeat_without_touching_external_server(self):
        bot=Mock();self.runtime.process=bot
        with patch.object(self.runtime,'running',return_value=False):self.runtime.stop()
        bot.close.assert_called_once();self.assertIsNone(self.runtime.process)

    def test_temporary_server_releases_idle_owned_server_after_operation_error(self):
        server,log=self.owned_server()
        with patch.object(self.runtime,'ensure_ollama'),patch.object(self.runtime,'running',return_value=False):
            with self.assertRaisesRegex(ValueError,'synthetic failure'):
                with self.runtime.temporary_server():raise ValueError('synthetic failure')
        server.close.assert_called_once();self.assertTrue(log.closed)

    def test_temporary_operation_does_not_close_server_used_by_running_bot(self):
        server,_=self.owned_server()
        with patch.object(self.runtime,'ensure_ollama'),patch.object(self.runtime,'running',return_value=True):
            with self.runtime.temporary_server():pass
        server.close.assert_not_called()

    def test_model_install_failure_releases_temporary_server(self):
        import json
        from desktop.install import Installer
        for relative in ('runtime/python.exe','runtime/.complete','.venv/Scripts/python.exe'):
            path=self.root/relative;path.parent.mkdir(parents=True,exist_ok=True);path.touch()
        (self.root/'config.json').write_text(json.dumps({'ollama_model':'synthetic:model'}))
        server=Mock()
        def ensure(*args):self.runtime.server=server
        with patch.object(self.runtime,'running',return_value=False),\
             patch.object(self.runtime,'ensure_ollama',side_effect=ensure),\
             patch('desktop.install.install_payload'),patch('desktop.install.run_step'),\
             patch('desktop.install.shutil.disk_usage') as disk,\
             patch('desktop.install.urllib.request.urlopen',side_effect=OSError('synthetic download failure')):
            disk.return_value.free=100*1024**3
            with self.assertRaisesRegex(OSError,'synthetic download failure'):
                Installer(self.root,self.root/'payload.zip',lambda *args:None,runtime=self.runtime).run()
        server.close.assert_called_once();self.assertIsNone(self.runtime.server)
