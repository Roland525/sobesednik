import io
import json
from pathlib import Path
import tempfile
import subprocess
import sys
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from tools import download_models


class FreshSetupTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == 'win32', 'Windows PowerShell install path')
    def test_path_python_version_detection_under_windows_powershell(self):
        script = (Path(download_models.ROOT)/'tools/setup.ps1').read_text(encoding='utf-8')
        probe = next(line.strip() for line in script.splitlines() if '$version = & $found.Source -c' in line)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'probe.ps1'
            executable = sys.executable.replace("'", "''")
            path.write_text("$found = @{Source='"+executable+"'}\n"+probe+"\nif ($LASTEXITCODE) { exit $LASTEXITCODE }\nWrite-Output $version",encoding='utf-8')
            result = subprocess.run(['powershell','-NoProfile','-ExecutionPolicy','Bypass','-File',str(path)],
                                    capture_output=True,text=True,timeout=15)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(result.stdout.strip(),f'{sys.version_info.major}.{sys.version_info.minor}')

    def test_portable_ollama_download_initializes_missing_report_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/'models/vosk-model-small-ru-0.22').mkdir(parents=True)
            self.assertFalse((root/'reports').exists())
            def download(url,path):
                path = Path(path)
                path.parent.mkdir(parents=True,exist_ok=True)
                if path.suffix == '.zip':
                    with ZipFile(path,'w') as archive:
                        archive.writestr('ollama.exe',b'non-executable test fixture')
                else:
                    path.write_bytes(b'test fixture')
            response = io.BytesIO(json.dumps({'tag_name':'test-release','assets':[
                {'name':'ollama-windows-amd64.zip','browser_download_url':'https://example.test/fixture.zip'}]}).encode())
            with patch.object(download_models,'ROOT',root), \
                 patch.object(download_models,'download',side_effect=download), \
                 patch('huggingface_hub.snapshot_download'), \
                 patch('urllib.request.urlopen',return_value=response), \
                 patch('sys.argv',['download_models.py','--ollama','--tts-engine','piper']):
                download_models.main()
            self.assertTrue((root/'reports/ollama-release.json').is_file())
            self.assertTrue((root/'tools/ollama/ollama.exe').is_file())
