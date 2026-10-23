import asyncio
import logging
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace as NS
import unittest

from bot.config import Config
from bot.learning import LearningCorpus
from bot.session import GuildSession, Question
from bot.memory import MemoryStore


class LearningSessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_remember_command_survives_later_speech_and_subject_label_is_a_note(self):
        with tempfile.TemporaryDirectory() as directory:
            memory = MemoryStore(Path(directory) / 'memory.sqlite3')
            session = GuildSession(1, Config(memory_enabled=True), None, None, None,
                                   logging.getLogger(), logging.getLogger(), memory=memory)
            session.start = lambda: None
            first = Question(10, 'Test', 'Запомни: Валера — это тестовый участник', time.monotonic())
            try:
                session.enqueue(first)
                session.enqueue(Question(10, 'Test', 'Ты запомнил?', time.monotonic()))
                self.assertEqual(session.questions.qsize(), 2)
                answer, _ = session.memory_turn(first)
                self.assertIn('Запомнил', answer)
                stored = memory.db.execute('SELECT content FROM notes').fetchone()[0]
                self.assertIn('тестовый участник', stored)
            finally:
                await session.close()
                memory.close()

    async def test_named_correction_after_hush_resumes_and_acknowledges(self):
        with tempfile.TemporaryDirectory() as directory:
            corpus = LearningCorpus(Path(directory) / 'learning.sqlite3')
            async def transcribe(pcm, active=False):
                return 'Серёга, правильный ответ: четыре.'
            stt = NS(transcribe=transcribe, last_ms={})
            session = GuildSession(1, Config(conversation_mode='rare', memory_enabled=False),
                                   stt, None, None, logging.getLogger(), logging.getLogger(), learning=corpus)
            corpus.bot_reply(1, 10, 'Сколько два плюс два?', 'Пять.')
            session.muted = session.quiet_mode = True
            spoken = []
            session.enqueue = spoken.append
            now = time.monotonic()
            await session.audio_queue.put(NS(member=NS(id=10, display_name='Test', name='test'),
                                             pcm=b'', ended=now, started=now-.3, queued=now, vad_ms=0))
            task = asyncio.create_task(session.recognize())
            try:
                await asyncio.wait_for(session.audio_queue.join(), 2)
                self.assertFalse(session.muted)
                self.assertFalse(session.quiet_mode)
                self.assertEqual(len(spoken), 1)
                self.assertIn('обучения', spoken[0].text)
                self.assertEqual(corpus.examples(1)[0]['messages'][-1]['content'], 'четыре.')
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                await session.close()
                corpus.close()

    async def test_forget_learning_works_with_memory_disabled(self):
        with tempfile.TemporaryDirectory() as directory:
            corpus = LearningCorpus(Path(directory) / 'learning.sqlite3')
            corpus.heard(1, 10, 'Почему небо голубое?', 100)
            corpus.heard(1, 20, 'Из-за рассеяния солнечного света.', 101)
            session = GuildSession(1, Config(memory_enabled=False), None, None, None,
                                   logging.getLogger(), logging.getLogger(), learning=corpus)
            try:
                answer, _ = session.memory_turn(Question(10, 'Test', 'Забудь всё обо мне', time.monotonic()))
                self.assertIn('Удалил', answer)
                self.assertEqual(corpus.examples(1), [])
            finally:
                await session.close()
                corpus.close()
