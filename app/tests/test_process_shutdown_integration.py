"""Real CPU-only process trees; never starts Ollama, Discord or fixed ports."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from desktop.runtime import OwnedProcess,Runtime

ROOT=Path(__file__).resolve().parents[1]
SERVER="""import json,os,socket,sys,time
from pathlib import Path
s=socket.socket();s.bind(('127.0.0.1',0));s.listen()
Path(sys.argv[1]).write_text(json.dumps({'port':s.getsockname()[1],'pid':os.getpid()}))
time.sleep(120)
"""

def wait_ready(path):
    deadline=time.monotonic()+10
    while time.monotonic()<deadline:
        try:return json.loads(path.read_text())
        except (OSError,ValueError):time.sleep(.02)
    raise AssertionError('Synthetic server did not start')

def listening(port):
    with socket.socket() as client:
        client.settimeout(.1)
        return client.connect_ex(('127.0.0.1',port))==0


@unittest.skipUnless(os.name=='nt','Windows Job Object lifecycle')
class ProcessShutdownTests(unittest.TestCase):
    def test_stop_and_close_release_native_descendants_and_port_but_leave_other_server(self):
        with tempfile.TemporaryDirectory() as folder,socket.socket() as external:
            external.bind(('127.0.0.1',0));external.listen()
            ready=Path(folder)/'ready.json'
            # The owned command starts a grandchild like Ollama's model runner.
            launcher='import subprocess,sys,time;subprocess.Popen([sys.executable,"-c",'+repr(SERVER)+','+repr(str(ready))+']);time.sleep(120)'
            owned=OwnedProcess([sys.executable,'-c',launcher],ROOT,sys.executable,stdout=subprocess.DEVNULL)
            runtime=Runtime(folder);runtime.server=owned
            try:
                info=wait_ready(ready);self.assertTrue(listening(info['port']))
                with patch.object(runtime,'running',return_value=False):
                    runtime.stop();runtime.close()
                self.assertFalse(listening(info['port']))
                self.assertIsNotNone(owned.poll())
                self.assertTrue(listening(external.getsockname()[1]))
            finally:owned.close()

    def test_force_killing_owner_releases_child_server_without_touching_other_listener(self):
        with tempfile.TemporaryDirectory() as folder,socket.socket() as external:
            external.bind(('127.0.0.1',0));external.listen()
            ready=Path(folder)/'ready.json'
            base=getattr(sys,'_base_executable',sys.executable)
            code=('import sys,time;sys.path.insert(0,'+repr(str(ROOT))+');'
                  'from desktop.runtime import OwnedProcess;'
                  'owned=OwnedProcess([sys.executable,"-c",'+repr(SERVER)+','+repr(str(ready))+'],'
                  +repr(str(ROOT))+',sys.executable);time.sleep(120)')
            controller=subprocess.Popen([base,'-c',code],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW)
            try:
                info=wait_ready(ready);self.assertTrue(listening(info['port']))
                controller.kill();controller.wait(timeout=10)
                deadline=time.monotonic()+5
                while listening(info['port']) and time.monotonic()<deadline:time.sleep(.03)
                self.assertFalse(listening(info['port']))
                self.assertTrue(listening(external.getsockname()[1]))
            finally:
                if controller.poll() is None:controller.kill();controller.wait(timeout=10)
