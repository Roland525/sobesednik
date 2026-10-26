import asyncio
import json
import logging
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from bot.config import Config
from bot.memory import MemoryStore
from bot.session import GuildSession
from bot.voice.transcription_sink import Utterance
from desktop.storage import Profile


class MemoryDefaultTests(unittest.TestCase):
    def test_new_profiles_enable_memory_examples_and_idle_learning_by_default(self):
        with tempfile.TemporaryDirectory() as folder:
            settings=Profile(folder).settings()
            example=json.loads((Path(__file__).resolve().parents[1]/'config.example.json').read_text(encoding='utf-8'))
            for key in ('memory_enabled','memory_auto_enabled','listen_conversation','learning_capture_enabled','learning_auto_enabled'):
                self.assertIs(getattr(Config(),key),True,key)
                self.assertIs(settings[key],True,key)
                self.assertIs(example[key],True,key)

    def test_explicit_optout_and_legacy_disabled_capture_are_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'config.json'
            path.write_text(json.dumps({'memory_auto_enabled':False,'learning_auto_enabled':False}),encoding='utf-8')
            self.assertFalse(Config.load(path).learning_auto_enabled)
            self.assertFalse(Profile(folder).settings()['memory_auto_enabled'])
            path.write_text(json.dumps({'learning_capture_enabled':False}),encoding='utf-8')
            self.assertFalse(Config.load(path).learning_auto_enabled)
            self.assertFalse(Profile(folder).settings()['learning_auto_enabled'])


class UsefulAutomaticMemoryTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.path=Path(self.folder.name)/'memory.sqlite3'
        self.memory=MemoryStore(self.path)

    def tearDown(self):self.memory.close();self.folder.cleanup()

    def test_clear_preferences_profiles_and_agreements_save_immediately(self):
        for uid,text in enumerate(('Мы договорились созваниваться по понедельникам.',
            'Каждый вечер я читаю книги.','Я из Казани.','У меня видеокарта RTX 3060.',
            'Ребята, мне нравится играть в шахматы.'),1):
            with self.subTest(text=text):
                self.assertTrue(self.memory.observe_auto(1,uid,'Speaker',text))
                row=self.memory.db.execute('SELECT source,target FROM notes WHERE source=?',(uid,)).fetchone()
                self.assertIsNotNone(row);self.assertEqual((row['source'],row['target']),(uid,uid))
        self.memory.close();self.memory=MemoryStore(self.path)
        self.assertEqual(self.memory.db.execute('SELECT count(*) FROM notes').fetchone()[0],5)

    def test_opinion_is_kept_as_attributed_opinion_without_remember_command(self):
        for uid,text in enumerate(('Путин царь.','Габен — гений.','Дота лучшая игра.','Я считаю, что шахматы лучшая игра.'),1):
            with self.subTest(text=text):
                self.assertTrue(self.memory.observe_auto(1,uid,'Speaker',text))
                row=self.memory.db.execute('SELECT * FROM notes WHERE source=?',(uid,)).fetchone()
                self.assertEqual(row['kind'],'opinion');self.assertEqual(row['target'],uid)
                context=json.loads(self.memory.context(1,uid,''))
                self.assertEqual(context['notes'][0]['kind'],'opinion')
                self.assertEqual(context['notes'][0]['source_id'],uid)

    def test_changed_opinion_replaces_same_subject_and_keeps_revision(self):
        self.assertTrue(self.memory.observe_auto(1,2,'Speaker','Габен гений.'))
        self.assertTrue(self.memory.observe_auto(1,2,'Speaker','Габен не гений.'))
        rows=self.memory.db.execute('SELECT * FROM notes WHERE source=2').fetchall()
        self.assertEqual(len(rows),1);self.assertIn('не гений',rows[0]['content'])
        self.assertEqual(self.memory.db.execute('SELECT count(*) FROM note_history').fetchone()[0],1)

    def test_opinion_recall_survives_reopen_and_does_not_belong_to_other_speaker(self):
        self.memory.observe_auto(1,2,'Author','Путин царь.')
        self.memory.register(1,3,'Other')
        self.memory.close();self.memory=MemoryStore(self.path)
        reply=self.memory.handle(1,2,'Author','Что я говорил про Путина?').reply
        self.assertIn('мнение',reply);self.assertIn('Путин царь',reply)
        self.assertFalse(self.memory.handle(1,3,'Other','Что я говорил про Путина?').reply)
        self.assertNotIn('Путин',self.memory.handle(2,2,'Author','Что я говорил про Путина?').reply)

    def test_dangling_statements_reactions_and_unclear_subjects_still_do_not_save(self):
        for text in ('Он гений.','Это лучшая игра.','Кнопка топ.','Ну да, понятно.',
                     'Я считаю, что он прав.','Я из','У меня видеокарта','Мы договорились тогда'):
            with self.subTest(text=text):self.assertFalse(self.memory.observe_auto(1,2,'Speaker',text))
        self.assertEqual(self.memory.db.execute('SELECT count(*) FROM notes').fetchone()[0],0)


class ImmediateMemoryPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_busy_conversation_does_not_require_selector_pause_to_save_supported_knowledge(self):
        with tempfile.TemporaryDirectory() as folder:
            memory=MemoryStore(Path(folder)/'memory.sqlite3')
            stt=NS(last_ms={},transcribe=AsyncMock())
            llm=NS(select_memories=AsyncMock())
            session=GuildSession(1,Config(conversation_mode='strict'),stt,llm,None,
                logging.getLogger(),logging.getLogger(),memory)
            session.responding=True
            worker=asyncio.create_task(session.recognize())
            try:
                for text in ('Мы договорились созваниваться по понедельникам.','Шахматы лучшая игра.'):
                    stt.transcribe.return_value=text;now=time.monotonic()
                    session.audio_queue.put_nowait(Utterance(NS(id=2,display_name='Speaker',name='speaker'),b'a',now,now,now,0))
                    await asyncio.wait_for(session.audio_queue.join(),1)
                self.assertEqual(memory.db.execute('SELECT count(*) FROM notes').fetchone()[0],2)
                self.assertTrue(session.questions.empty());llm.select_memories.assert_not_awaited()
            finally:
                worker.cancel();await asyncio.gather(worker,return_exceptions=True)
                await session.close();memory.close()
