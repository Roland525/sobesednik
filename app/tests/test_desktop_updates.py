import hashlib,io,json,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import patch

BASE='https://github.com/Roland525/sobesednik/releases/download/v1.2.0/'
def release():
 return {'tag_name':'v1.2.0','draft':False,'prerelease':False,'html_url':'https://github.com/Roland525/sobesednik/releases/tag/v1.2.0',
  'assets':[{'name':'Sobesednik-Setup-1.2.0.exe','size':8,'browser_download_url':BASE+'Sobesednik-Setup-1.2.0.exe','digest':'sha256:'+hashlib.sha256(b'MZupdate').hexdigest()}]}

class UpdateTests(unittest.TestCase):
 def test_release_semver_not_lexical_and_no_downgrades(self):
  from desktop.updates import release_update
  self.assertIsNotNone(release_update(release(),'1.1.9'))
  self.assertIsNone(release_update(release(),'1.2.0'))
  self.assertIsNone(release_update(release(),'1.10.0'))
 def test_draft_prerelease_and_invalid_version_are_never_offered(self):
  from desktop.updates import release_update
  for changes in [{'draft':True},{'prerelease':True},{'tag_name':'v1.2.0-beta'},{'tag_name':'run arbitrary program'}]:
   value=release();value.update(changes);self.assertIsNone(release_update(value,'1.0.0'))
 def test_only_official_repo_windows_asset_can_be_executed(self):
  from desktop.updates import release_update
  for url in ['http://github.com/Roland525/sobesednik/x','https://evil.example/a.exe',
              'https://github.com/other/project/releases/download/v1.2.0/Sobesednik-Setup-1.2.0.exe']:
   value=release();value['assets'][0]['browser_download_url']=url
   with self.assertRaises(ValueError):release_update(value,'1.0.0')
 def test_legacy_checksum_asset_is_supported_but_missing_checksum_is_not(self):
  from desktop.updates import release_update
  value=release();value['assets'][0].pop('digest')
  with self.assertRaises(ValueError):release_update(value,'1.0.0')
  value['assets'].append({'name':'SHA256SUMS-1.2.0.txt','browser_download_url':BASE+'SHA256SUMS-1.2.0.txt'})
  checksum=hashlib.sha256(b'MZupdate').hexdigest()+'  Sobesednik-Setup-1.2.0.exe\n'
  with patch('desktop.updates.read_url',return_value=checksum.encode()):
   self.assertEqual(release_update(value,'1.0.0').sha256,hashlib.sha256(b'MZupdate').hexdigest())
 def test_download_verifies_before_returning_and_reuses_verified_file(self):
  from desktop.updates import release_update,download_update
  info=release_update(release(),'1.0.0')
  with tempfile.TemporaryDirectory() as folder,patch('desktop.updates.open_url',return_value=io.BytesIO(b'MZupdate')) as url:
   path=download_update(info,Path(folder),lambda *args:None,threading.Event())
   self.assertEqual(path.read_bytes(),b'MZupdate')
   self.assertEqual(download_update(info,Path(folder),lambda *args:None,threading.Event()),path)
   self.assertEqual(url.call_count,1)
 def test_hash_or_size_failure_and_cancel_never_leave_executable(self):
  from desktop.updates import release_update,download_update
  info=release_update(release(),'1.0.0')
  for data in [b'MZwrong!',b'MZx']:
   with tempfile.TemporaryDirectory() as folder,patch('desktop.updates.open_url',return_value=io.BytesIO(data)):
    with self.assertRaises(ValueError):download_update(info,Path(folder),lambda *args:None,threading.Event())
    self.assertEqual(list(Path(folder).rglob('*.exe')),[])
  with tempfile.TemporaryDirectory() as folder:
   cancel=threading.Event();cancel.set()
   with self.assertRaises(InterruptedError):download_update(info,Path(folder),lambda *args:None,cancel)
 def test_helper_waits_for_old_app_verifies_installer_and_checks_exit(self):
  from desktop.updates import helper_script
  script=helper_script(Path("C:/A user's data/update.json"))
  self.assertIn("A user''s data",script)
  self.assertLess(script.index('Wait-Process'),script.index('Get-FileHash'))
  self.assertIn('ExitCode',script);self.assertIn('/VERYSILENT',script)
  self.assertIn('--resume-after-update',script);self.assertIn('Start-Process',script)
 def test_launch_uses_argv_and_keeps_secrets_out_of_helper(self):
  from desktop.updates import release_update,download_update,start_installer
  info=release_update(release(),'1.0.0')
  with tempfile.TemporaryDirectory() as folder,patch('desktop.updates.open_url',return_value=io.BytesIO(b'MZupdate')):
   root=Path(folder);installer=download_update(info,root,lambda *args:None,threading.Event())
   with patch('desktop.updates.subprocess.Popen') as popen:
    start_installer(info,installer,root,root/'program/Sobesednik.exe',123,456)
   args=popen.call_args.args[0];self.assertIsInstance(args,list)
   script=Path(args[-1]).read_text(encoding='utf-8-sig')
   self.assertNotIn('DISCORD_TOKEN',script)
   config=json.loads(next(installer.parent.glob('handoff-*.json')).read_text(encoding='utf-8'))
   self.assertEqual(config['parent_pid'],123);self.assertEqual(config['resume_channel'],456)
