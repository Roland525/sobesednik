import asyncio
import json
import logging
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock

import httpx

from bot.config import Config
from bot.conversation import addressed
from bot.services.audio_service import AudioService
from bot.services.ollama_service import OllamaService
from bot.session import GuildSession, Question
from bot.voice.transcription_sink import Utterance


class AddressTests(unittest.TestCase):
    def test_name_in_middle_or_end_keeps_whole_question(self):
        for text, expected in (
            ('Сколько будет два плюс два, Серёга?', 'Сколько будет два плюс два?'),
            ('Как думаешь, Серега, стоит попробовать?', 'Как думаешь, стоит попробовать?'),
            ('Объясни Серёга почему небо голубое', 'Объясни почему небо голубое'),
            ('Расскажи про космос серега', 'Расскажи про космос')):
            self.assertEqual(addressed(text), (True, expected))

    def test_wake_gate_finds_name_after_beginning_of_audio(self):
        service = AudioService(logging.getLogger(), Config())
        recognizer = SimpleNamespace(SetWords=lambda value: None, AcceptWaveform=lambda pcm: False,
            FinalResult=lambda: json.dumps({'text': '[unk] серёга',
                'result': [{'word': 'серёга', 'start': 2.8, 'end': 3.2, 'conf': .96}]}))
        try:
            service.wake_model = object()
            with patch('vosk.KaldiRecognizer', return_value=recognizer):
                self.assertTrue(service.wake_transcript(bytes(64000)))
        finally:
            service.close()


class RareRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_speech_during_tts_prevents_background_playback(self):
        state = GuildSession(1, Config(conversation_mode='rare'), None, None,
            SimpleNamespace(generate_audio=AsyncMock(return_value=bytes(3840))), logging.getLogger(), logging.getLogger())
        state.play_pcm = AsyncMock()
        question = Question(1, 'Тест', 'Можно обсудить защиту базы от радиации.', time.monotonic(), kind='ambient_say')
        state.last_human_at = question.enqueued_at + .1
        try:
            response = await state.speak_stream(question, state.conversation.messages(1, 'Тест', 'Обсуждаем базу на Луне'))
            self.assertEqual(response, '')
            state.play_pcm.assert_not_called()
        finally:
            await state.close()

    async def test_named_question_cancels_background_generation(self):
        entered, cancelled = asyncio.Event(), asyncio.Event()
        class LLM:
            async def participate(self, *args, **kwargs):
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()
        class STT:
            last_ms = {}
            async def transcribe(self, *args, **kwargs): return 'Сколько два плюс два, Серёга?'
        state = GuildSession(1, Config(conversation_mode='rare'), STT(), LLM(), None, logging.getLogger(), logging.getLogger())
        try:
            state.enqueue(Question(1, 'Тест', 'Мы обсуждаем достаточно длинную понятную тему про космос', time.monotonic(), kind='ambient'))
            await asyncio.wait_for(entered.wait(), 1)
            captured = []
            state.enqueue = captured.append
            now = time.monotonic()
            state.audio_queue.put_nowait(Utterance(SimpleNamespace(id=1, display_name='Тест'), b'a', now, now, now, 0))
            await asyncio.wait_for(state.audio_queue.join(), 1)
            self.assertTrue(cancelled.is_set())
            self.assertEqual(captured[0].kind, 'voice')
        finally:
            await state.close()

    async def test_unsolicited_cooldown_and_expired_context_block_new_reply(self):
        state = GuildSession(1, Config(conversation_mode='rare'), None, None, None, logging.getLogger(), logging.getLogger())
        now = time.monotonic()
        one = 'Для базы на Луне надо заранее продумать много разных вещей'
        two = 'Особенно интересно как можно защитить людей от опасной радиации'
        try:
            self.assertFalse(state.rare_candidate(one, now, []))
            self.assertTrue(state.rare_candidate(two, now + 1, []))
            state.last_unsolicited_at = now + 1
            self.assertFalse(state.rare_candidate(one, now + 90, []))
            self.assertFalse(state.rare_candidate(two, now + 100, []))
            # Once the cooldown ends, stale earlier speech cannot supply context.
            self.assertFalse(state.rare_candidate(one, now + 182, []))
            self.assertTrue(state.rare_candidate(two, now + 183, []))
        finally:
            await state.close()

    async def test_context_is_observed_and_related_followup_is_allowed(self):
        class STT:
            last_ms = {}
            text = ''
            async def transcribe(self, *args, **kwargs): return self.text
        config = Config()
        config.conversation_mode = 'rare'
        state = GuildSession(1, config, STT(), None, None, logging.getLogger(), logging.getLogger())
        captured = []
        state.enqueue = captured.append
        worker = asyncio.create_task(state.recognize())
        async def feed(text):
            state.stt.text = text
            now = time.monotonic()
            state.audio_queue.put_nowait(Utterance(SimpleNamespace(id=1, display_name='Тест'), b'a', now, now, now, 0))
            await state.audio_queue.join()
        try:
            await feed('Сколько будет два плюс два, Серёга?')
            state.conversation.replied_to(1)
            await feed('А три плюс три?')
            await feed('Мне нравится изучать космос и смотреть на звезды вечером.')
            await feed('Особенно интересно как люди могли бы жить на других планетах.')
            self.assertEqual([q.kind for q in captured], ['voice', 'voice', 'ambient'])
            self.assertIn('других планетах', captured[-1].text)
            await state.hush()
            await feed('Что думаешь об этом, Серёга?')
            await feed('Можно было бы начать с базы на Луне и выращивать там еду.')
            self.assertEqual([q.kind for q in captured], ['voice', 'voice', 'ambient', 'voice'])
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
            await state.close()

    async def test_rare_mode_has_no_idle_monologues(self):
        state = GuildSession(1, Config(conversation_mode='rare'), None, None, None, logging.getLogger(), logging.getLogger())
        now = time.monotonic()
        state.joined_at = state.last_human_at = now - 60
        state.voice_client = SimpleNamespace(is_connected=lambda: True,
            channel=SimpleNamespace(members=[SimpleNamespace(bot=False)]))
        try:
            self.assertFalse(state.initiative_allowed(now))
        finally:
            state.voice_client = None
            await state.close()


class RareQualityTests(unittest.IsolatedAsyncioTestCase):
    async def check_reply(self, *, confidence=.98, quote='Для лунной базы важно продумать защиту от радиации', verified=True):
        config = Config()
        service = OllamaService(logging.getLogger(), config)
        await service.client.aclose()
        outputs = [dict(reply=True, confidence=confidence, context_quote=quote,
                        text='Для такой базы стоит сначала обсудить защиту от радиации.'),
                   dict(supported=verified, useful=True, context_understood=True)]
        def handle(request):
            return httpx.Response(200, json={'message': {'content': json.dumps(outputs.pop(0))}})
        service.client = httpx.AsyncClient(base_url='http://localhost', transport=httpx.MockTransport(handle))
        try:
            messages = [{'role': 'system', 'content': 'Ты Серёга.'},
                        {'role': 'user', 'content': '[Иван]: Для лунной базы важно продумать защиту от радиации'}]
            return await service.participate(messages, {}, conservative=True)
        finally:
            await service.close()

    async def test_grounded_useful_reply_is_allowed(self):
        self.assertIn('защиту', await self.check_reply())

    async def test_low_confidence_or_invented_context_or_failed_check_stays_silent(self):
        self.assertEqual(await self.check_reply(confidence=.8), '')
        self.assertEqual(await self.check_reply(quote='Кто-то обсуждал новый патч и героев'), '')
        self.assertEqual(await self.check_reply(verified=False), '')
