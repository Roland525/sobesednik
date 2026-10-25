import io
import json
from contextlib import ExitStack
from pathlib import Path
import tempfile
import stat
import subprocess
import sys
import unittest
from unittest.mock import patch
from zipfile import ZipFile, ZipInfo, BadZipFile

from tools import download_models


VOSK_FILES = ('am/final.mdl', 'conf/mfcc.conf', 'conf/model.conf',
              'graph/disambig_tid.int', 'graph/Gr.fst', 'graph/HCLr.fst',
              'graph/phones/word_boundary.int', 'ivector/final.dubm',
              'ivector/final.ie', 'ivector/final.mat', 'ivector/global_cmvn.stats',
              'ivector/online_cmvn.conf', 'ivector/splice.conf')


def fixture_zip(files):
    stream = io.BytesIO()
    with ZipFile(stream, 'w') as archive:
        for name, data in files.items(): archive.writestr(name, data)
    return stream.getvalue()


def legacy_vosk(root):
    for name in VOSK_FILES:
        path = root/'models/vosk-model-small-ru-0.22'/name
        path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b'fixture')


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
            legacy_vosk(root)
            self.assertFalse((root/'reports').exists())
            def download(url,path):
                path = Path(path)
                path.parent.mkdir(parents=True,exist_ok=True)
                if path.suffix == '.zip':
                    with ZipFile(path,'w') as archive:
                        archive.writestr('ollama.exe',b'non-executable test fixture')
                        archive.writestr('lib/ollama/ggml-base.dll',b'non-executable DLL fixture')
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


class ComponentRecoveryTests(unittest.TestCase):
    def setup_fixture(self, root, *, vosk=None, ollama=None):
        vosk = vosk if vosk is not None else fixture_zip({
            'vosk-model-small-ru-0.22/'+name: b'complete-model' for name in VOSK_FILES})
        ollama = ollama if ollama is not None else fixture_zip({
            'ollama.exe': b'new-exe-fixture', 'lib/ollama/ggml-base.dll': b'new-dll-fixture'})
        release = {'tag_name': 'v-fixture-new', 'assets': [{
            'name': 'ollama-windows-amd64.zip',
            'browser_download_url': 'https://example.test/v-fixture-new/ollama.zip'}]}
        def response(request, **kwargs):
            url = request.full_url if hasattr(request, 'full_url') else request
            if url.endswith('/releases/latest'): return io.BytesIO(json.dumps(release).encode())
            if 'vosk-model' in url: return io.BytesIO(vosk)
            if url.endswith('/ollama.zip'): return io.BytesIO(ollama)
            return io.BytesIO(b'piper-fixture')
        stack = ExitStack()
        stack.enter_context(patch.object(download_models, 'ROOT', root))
        stack.enter_context(patch('huggingface_hub.snapshot_download'))
        stack.enter_context(patch('urllib.request.urlopen', side_effect=response))
        stack.enter_context(patch('sys.argv', ['download_models.py', '--ollama', '--tts-engine', 'piper']))
        return stack

    def test_retry_repairs_vosk_directory_left_by_interrupted_extraction(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); model = root/'models/vosk-model-small-ru-0.22'
            (model/'am').mkdir(parents=True); (model/'am/final.mdl').write_bytes(b'partial')
            with self.setup_fixture(root): download_models.main()
            self.assertTrue((model/'graph/HCLr.fst').is_file(), 'Retry skipped the incomplete model')
            self.assertEqual((model/'graph/HCLr.fst').read_bytes(), b'complete-model')

    def test_retry_repairs_ollama_executable_without_its_runtime_libraries(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); legacy_vosk(root)
            target = root/'tools/ollama'; target.mkdir(parents=True)
            (target/'ollama.exe').write_bytes(b'incomplete-old-exe')
            with self.setup_fixture(root): download_models.main()
            self.assertTrue((target/'lib/ollama/ggml-base.dll').is_file(), 'Retry skipped the incomplete runtime')
            self.assertEqual((target/'lib/ollama/ggml-base.dll').read_bytes(), b'new-dll-fixture')
            self.assertEqual((target/'ollama.exe').read_bytes(), b'new-exe-fixture')

    def test_retry_does_not_extract_cached_ollama_zip_from_a_different_release(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); legacy_vosk(root); (root/'tools').mkdir()
            (root/'tools/ollama-windows-amd64.zip').write_bytes(fixture_zip({
                'ollama.exe': b'stale-exe', 'lib/ollama/ggml-base.dll': b'stale-dll'}))
            with self.setup_fixture(root): download_models.main()
            self.assertEqual((root/'tools/ollama/ollama.exe').read_bytes(), b'new-exe-fixture')

    def test_untrusted_zip_cannot_write_outside_the_component(self):
        bad_archives = [fixture_zip({'../escaped': b'bad'}),
                        fixture_zip({'vosk-model-small-ru-0.22/../escaped': b'bad'}),
                        fixture_zip({'vosk-model-small-ru-0.22/C:/escaped': b'bad'}),
                        fixture_zip({'vosk-model-small-ru-0.22/..\\escaped': b'bad'})]
        for data in bad_archives:
            with self.subTest(), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                with self.setup_fixture(root, vosk=data), self.assertRaises(ValueError):
                    download_models.main()
                self.assertFalse((root/'models/escaped').exists())
                self.assertFalse((root/'models/vosk-model-small-ru-0.22').exists())

    def test_completed_installation_is_preserved_and_missing_files_are_repaired(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with self.setup_fixture(root): download_models.main()
            executable = root/'tools/ollama/ollama.exe'; stamp = executable.stat().st_mtime_ns
            with self.setup_fixture(root): download_models.main()
            self.assertEqual(executable.stat().st_mtime_ns, stamp)
            (root/'tools/ollama/lib/ollama/ggml-base.dll').unlink()
            with self.setup_fixture(root): download_models.main()
            self.assertTrue((root/'tools/ollama/lib/ollama/ggml-base.dll').is_file(), 'Deleted runtime was not repaired')
            self.assertEqual((root/'tools/ollama/lib/ollama/ggml-base.dll').read_bytes(), b'new-dll-fixture')

    def test_failed_extraction_keeps_previous_component_and_retry_finishes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); legacy_vosk(root)
            target = root/'tools/ollama'; target.mkdir(parents=True)
            (target/'ollama.exe').write_bytes(b'old-exe')
            # Cache the small downloads so the simulated disk failure hits extraction.
            for name in ['ru_RU-denis-medium.onnx', 'ru_RU-denis-medium.onnx.json', 'piper-MODEL_CARD']:
                (root/'models'/name).write_bytes(b'cached')
            archive = root/'tools/ollama-windows-amd64.zip'
            archive.write_bytes(fixture_zip({'ollama.exe': b'new-exe-fixture',
                                           'lib/ollama/ggml-base.dll': b'new-dll-fixture'}))
            def interrupted(source, destination, *args):
                destination.write(source.read(2))
                raise OSError('simulated disk failure')
            with patch('shutil.copyfileobj', side_effect=interrupted), self.assertRaises(OSError):
                download_models.extract_component(archive, target,
                                                 ('ollama.exe', 'lib/ollama/ggml-base.dll'))
            self.assertEqual((target/'ollama.exe').read_bytes(), b'old-exe')
            # A stage left by a killed process must not be mistaken for a completed install.
            stale = target.with_name(target.name+'.stage-killed'); stale.mkdir()
            (stale/'ollama.exe').write_bytes(b'partial')
            with self.setup_fixture(root): download_models.main()
            self.assertEqual((target/'lib/ollama/ggml-base.dll').read_bytes(), b'new-dll-fixture')

    def test_failed_promotion_restores_previous_component(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); target = root/'component'; target.mkdir()
            (target/'engine.exe').write_bytes(b'old-engine')
            archive = root/'component.zip'; archive.write_bytes(fixture_zip({'engine.exe': b'new-engine'}))
            rename = Path.rename
            def fail_promotion(source, destination):
                if '.stage-' in source.name: raise PermissionError('simulated locked target')
                return rename(source, destination)
            with patch.object(Path, 'rename', fail_promotion), self.assertRaises(PermissionError):
                download_models.extract_component(archive, target, ('engine.exe',))
            self.assertEqual((target/'engine.exe').read_bytes(), b'old-engine')
            download_models.extract_component(archive, target, ('engine.exe',))
            self.assertEqual((target/'engine.exe').read_bytes(), b'new-engine')

    def test_zip_crc_failure_discards_corrupt_cache_without_publishing(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); archive = root/'component.zip'
            payload = fixture_zip({'engine.exe': b'unique-engine-fixture'})
            archive.write_bytes(payload.replace(b'unique-engine-fixture', b'broken-engine-fixture'))
            with self.assertRaises(BadZipFile):
                download_models.extract_component(archive, root/'component', ('engine.exe',))
            self.assertFalse(archive.exists())
            self.assertFalse((root/'component').exists())

    def test_zip_rejects_symlinks_and_windows_case_aliases(self):
        stream = io.BytesIO()
        with ZipFile(stream, 'w') as archive:
            link = ZipInfo('engine.exe'); link.create_system = 3
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(link, '../outside')
        for data in [stream.getvalue(), fixture_zip({'engine.exe': b'first', 'ENGINE.EXE': b'second'})]:
            with self.subTest(), tempfile.TemporaryDirectory() as folder:
                root = Path(folder); archive = root/'component.zip'; archive.write_bytes(data)
                with self.assertRaises(ValueError):
                    download_models.extract_component(archive, root/'component', ('engine.exe',))
                self.assertFalse((root/'component').exists())

    def test_invalid_completion_marker_does_not_make_incomplete_files_ready(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder); (target/'engine.exe').write_bytes(b'engine')
            (target/'.component-complete.json').write_text('{broken', encoding='utf-8')
            self.assertFalse(download_models.component_ready(target, ('engine.exe',)))
            (target/'.component-complete.json').write_text(json.dumps({'files': {
                'engine.exe': 6, '../outside': 1}}), encoding='utf-8')
            self.assertFalse(download_models.component_ready(target, ('engine.exe',)))

    def test_legacy_installation_matches_every_retained_zip_file(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); target = root/'component'; target.mkdir()
            (target/'engine.exe').write_bytes(b'engine')
            archive = root/'component.zip'; archive.write_bytes(fixture_zip({
                'engine.exe': b'engine', 'runtime.dll': b'runtime'}))
            self.assertFalse(download_models.component_ready(target, ('engine.exe',), archive))
            (target/'runtime.dll').write_bytes(b'runtime')
            self.assertTrue(download_models.component_ready(target, ('engine.exe',), archive))
