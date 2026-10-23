import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import zipfile


class DesktopStorageTests(unittest.TestCase):
    @unittest.skipUnless(__import__('os').name=='nt','Windows DPAPI')
    def test_token_is_encrypted_and_legacy_files_are_preserved(self):
        from desktop.credentials import save_token,load_token
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); token='example-token-for-dpapi-roundtrip-only'
            (root/'.env').write_text('DISCORD_TOKEN='+token)
            self.assertEqual(load_token(root),token)
            save_token(root,token)
            self.assertNotIn(token.encode(),(root/'discord-token.bin').read_bytes())
            self.assertEqual(load_token(root),token)
            self.assertTrue((root/'.env').exists())

    def test_settings_preserve_unknown_values_and_validate_before_writing(self):
        from desktop.storage import Profile
        with tempfile.TemporaryDirectory() as folder:
            p = Profile(Path(folder))
            p.root.joinpath('config.json').write_text(json.dumps({'future_setting': 3, 'tts_speed': 1.06}))
            p.save_settings({'tts_speed': 1.1})
            self.assertEqual(p.settings()['tts_speed'], 1.1)
            self.assertEqual(json.loads((p.root/'config.json').read_text())['future_setting'], 3)
            previous = (p.root/'config.json').read_bytes()
            with self.assertRaises(ValueError): p.save_settings({'tts_speed': 80})
            with self.assertRaises(ValueError): p.save_settings({'discord_token': 'not-in-json'})
            self.assertEqual((p.root/'config.json').read_bytes(), previous)

    def test_payload_hash_and_paths_checked_before_existing_files_change(self):
        from desktop.storage import install_payload
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)/'profile'; root.mkdir()
            (root/'PERSONA.md').write_text('private persona')
            archive = Path(folder)/'source.zip'
            data = {'main.py': b'pass\n', 'PERSONA.md': b'new default'}
            manifest = {'schema': 1, 'files': {k: hashlib.sha256(v).hexdigest() for k,v in data.items()}}
            with zipfile.ZipFile(archive,'w') as z:
                for k,v in data.items(): z.writestr(k,v)
                z.writestr('RELEASE_MANIFEST.json',json.dumps(manifest))
            install_payload(archive,root)
            self.assertEqual((root/'PERSONA.md').read_text(),'private persona')
            self.assertTrue((root/'main.py').exists())
            with zipfile.ZipFile(archive,'w') as z:
                z.writestr('../escaped.py','bad')
                z.writestr('RELEASE_MANIFEST.json',json.dumps(manifest))
            with self.assertRaises(ValueError): install_payload(archive,root)
            self.assertFalse((root.parent/'escaped.py').exists())

    def test_scoped_note_removal_and_forget_also_removes_training_examples(self):
        from desktop.storage import Profile
        from bot.memory import MemoryStore
        from bot.learning import LearningCorpus
        with tempfile.TemporaryDirectory() as folder:
            p=Profile(Path(folder)); memory=MemoryStore(p.root/'data/memory.sqlite3')
            memory.db.executemany('INSERT INTO people VALUES (?,?,?,?,?)',[(1,2,'A','a',0),(2,2,'B','b',0)])
            memory.db.execute('INSERT INTO aliases VALUES (?,?,?,?,?,?,?)',(1,2,2,'Pavel','pavel',1,1))
            memory.db.executemany('INSERT INTO notes VALUES (?,?,?,?,?,?,?,?)',
                [(1,2,'one',2,'','fact','first',1),(2,2,'two',2,'','fact','second',1)])
            memory.db.commit(); memory.close()
            learning=LearningCorpus(p.root/'data/learning.sqlite3')
            learning.add(1,2,2,'correction','a question','an answer',1); learning.close()
            rows=p.memory_rows('notes',1); self.assertEqual(rows[0]['content'],'first')
            self.assertIn('Pavel',p.memory_rows('people',1)[0]['remembered_names'])
            p.mutate_memory('memory_forget',{'guild':1,'uid':2})
            self.assertEqual(p.memory_rows('notes',1),[])
            self.assertEqual(len(p.memory_rows('notes',2)),1)
            self.assertEqual(p.memory_rows('examples',1),[])

    def test_persona_has_backup_and_log_token_redaction(self):
        from desktop.storage import Profile, redact
        with tempfile.TemporaryDirectory() as folder:
            p=Profile(Path(folder)); (p.root/'PERSONA.md').write_text('old')
            p.save_persona('new')
            self.assertEqual(p.persona(),'new')
            self.assertTrue(list((p.root/'backups').glob('PERSONA-*')))
            self.assertNotIn('sensitive-token',redact('error sensitive-token', 'sensitive-token'))
