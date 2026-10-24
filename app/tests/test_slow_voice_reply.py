import asyncio
import logging
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock

from bot.config import Config
from bot.session import GuildSession, Question
from bot.voice.transcription_sink import Utterance


class SlowVoiceReplyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.stt = NS(transcribe=AsyncMock(return_value='Собеседник, ты тут?'),
                      last_ms={'stt_ms':11000})
        self.session = GuildSession(1, Config(), self.stt, NS(), None,
                                    logging.getLogger(), logging.getLogger())
        self.session.speak_stream = AsyncMock(return_value='Я тут.')

    async def asyncTearDown(self):
        await self.session.close()

    async def test_successful_slow_recognition_reaches_voice_response(self):
        # Eleven seconds of native recognition, represented without sleeping.
        ended = time.monotonic()-11
        self.session.start()
        await self.session.audio_queue.put(Utterance(NS(id=1, display_name='Tester'),
                                            b'a', ended-2, ended, ended, 0))
        await asyncio.wait_for(self.session.audio_queue.join(), 2)
        await asyncio.wait_for(self.session.questions.join(), 2)
        self.session.speak_stream.assert_awaited_once()
        self.assertEqual(self.session.journal.snapshot()[-1]['status'], 'answered')

    async def test_old_audio_and_reply_backlogs_are_still_rejected_with_reason(self):
        for age, queued in ((61, 0), (15, 9)):
            question = Question(1, 'Tester', 'Ты тут?', time.monotonic()-age)
            question.enqueued_at = time.monotonic()-queued
            question.turn_id = self.session.journal.heard(1, 'Tester', 'Ты тут?', 'named')
            self.session.questions.put_nowait((0, 1, question))
            worker = asyncio.create_task(self.session.respond())
            try:
                await asyncio.wait_for(self.session.questions.join(), 2)
            finally:
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)
            self.assertEqual(self.session.journal.snapshot()[-1]['reason'], 'stale_audio')
        self.session.speak_stream.assert_not_awaited()
