import asyncio
import json
import logging
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from bot.config import Config
from bot.conversation import Conversation, likely_followup
from bot.controls import voice_control
from bot.session import GuildSession
from bot.services.ollama_service import OllamaService
from bot.services.audio_service import AudioService
from bot.voice.transcription_sink import Utterance


class DialogueTests(unittest.IsolatedAsyncioTestCase):
    def test_backchannel_starting_with_how_is_not_a_followup_question(self):
        self.assertFalse(likely_followup('Как всегда.', [{'role': 'assistant', 'content': 'Что-то случилось?'}]))

    def test_answer_to_a_specific_bot_question_can_be_a_followup(self):
        self.assertTrue(likely_followup('Играю на керри', [{'role': 'assistant', 'content': 'Какая у тебя роль в Доте?'}]))
        self.assertFalse(likely_followup('Профессионально.', [{'role': 'assistant', 'content': 'Что-то случилось?'}]))

    async def test_background_speech_does_not_poison_personal_history(self):
        class STT:
            last_ms = {}
            async def transcribe(self, *args, **kwargs):
                return 'Паша сейчас обсуждает совсем другую тему про свою машину'
        state = GuildSession(1, Config(conversation_mode='rare'), STT(), None, None, logging.getLogger(), logging.getLogger())
        worker = asyncio.create_task(state.recognize())
        try:
            now = time.monotonic()
            state.audio_queue.put_nowait(Utterance(SimpleNamespace(id=2, display_name='Тест'), b'a', now, now, now, 0))
            await state.audio_queue.join()
            self.assertFalse(state.conversation.history)
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
            await state.close()

    async def test_followup_classifier_accepts_only_related_decision(self):
        service = OllamaService(logging.getLogger(), Config())
        await service.client.aclose()
        decisions = ['related', 'other']
        service.client = httpx.AsyncClient(base_url='http://localhost', transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={'message': {'content': json.dumps({'decision': decisions.pop(0)})}})))
        messages = [{'role': 'system', 'content': 'Test'},
                    {'role': 'assistant', 'content': 'Небо голубое из-за рассеяния света.'},
                    {'role': 'user', 'content': 'А на закате почему красное?'}]
        try:
            self.assertTrue(await service.accept_followup(messages))
            self.assertFalse(await service.accept_followup(messages))
        finally:
            await service.close()

    async def test_hush_discards_a_followup_decision_already_in_flight(self):
        entered, release = asyncio.Event(), asyncio.Event()
        class STT:
            last_ms = {}
            async def transcribe(self, *args, **kwargs): return 'А почему на закате красное?'
        class LLM:
            async def accept_followup(self, messages):
                entered.set()
                await release.wait()
                return True
        state = GuildSession(1, Config(conversation_mode='rare'), STT(), LLM(), None, logging.getLogger(), logging.getLogger())
        state.conversation.replied_to(1)
        queued = []
        state.enqueue = queued.append
        worker = asyncio.create_task(state.recognize())
        try:
            now = time.monotonic()
            state.audio_queue.put_nowait(Utterance(SimpleNamespace(id=1, display_name='Тест'), b'a', now, now, now, 0))
            await asyncio.wait_for(entered.wait(), 1)
            await state.hush()
            release.set()
            await asyncio.wait_for(state.audio_queue.join(), 1)
            self.assertEqual(queued, [])
        finally:
            release.set()
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
            await state.close()

    def test_prompt_echo_and_subtitle_hallucinations_are_not_questions(self):
        service = AudioService(logging.getLogger(), Config())
        try:
            for phrase in ('Русская речь.', 'Субтитры создавал DimaTorzok', 'Спасибо за просмотр!'):
                segment = SimpleNamespace(text=phrase, avg_logprob=-.1, no_speech_prob=.1)
                service.model = SimpleNamespace(transcribe=lambda *args, **kwargs: ([segment], None))
                with patch('faster_whisper.vad.get_speech_timestamps', return_value=[{'start': 0, 'end': 8000}]):
                    self.assertEqual(service.full_transcript(bytes(32000)), '', phrase)
        finally:
            service.close()

    async def test_hush_stops_current_turn_but_named_question_can_resume(self):
        class STT:
            last_ms = {}
            text = ''
            async def transcribe(self, *args, **kwargs): return self.text
        state = GuildSession(1, Config(conversation_mode='rare'), STT(), None, None, logging.getLogger(), logging.getLogger())
        await state.hush()
        queued = []
        state.enqueue = queued.append
        worker = asyncio.create_task(state.recognize())
        try:
            for text in ('Серёга, расскажи про космос', 'Серёга, говори'):
                state.stt.text = text
                now = time.monotonic()
                state.audio_queue.put_nowait(Utterance(SimpleNamespace(id=1, display_name='Тест'), b'a', now, now, now, 0))
                await state.audio_queue.join()
                if 'космос' in text:
                    self.assertFalse(state.muted)
                    self.assertFalse(state.quiet_mode)
                    self.assertGreater(state.last_unsolicited_at, 0)
                    self.assertEqual(queued[0].text, 'расскажи про космос')
            self.assertEqual(queued[-1].text, 'Слушаю.')
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
            await state.close()

    def test_observed_hush_mistranscription_is_still_a_stop_command(self):
        self.assertEqual(voice_control('Серёга завлибала!'), 'mute')

    async def test_active_window_does_not_answer_statements_between_people(self):
        class STT:
            last_ms = {}
            text = ''
            async def transcribe(self, *args, **kwargs): return self.text
        state = GuildSession(1, Config(conversation_mode='summon'), STT(), None, None, logging.getLogger(), logging.getLogger())
        queued = []
        state.enqueue = queued.append
        worker = asyncio.create_task(state.recognize())
        async def feed(text):
            state.stt.text = text
            now = time.monotonic()
            state.audio_queue.put_nowait(Utterance(SimpleNamespace(id=1, display_name='Тест'), b'a', now, now, now, 0))
            await state.audio_queue.join()
        try:
            await feed('Серёга, почему небо голубое?')
            state.conversation.history.append({'role': 'assistant', 'content': 'Из-за рассеяния солнечного света в атмосфере.'})
            state.conversation.replied_to(1)
            for text in ('Спокойно.', 'Вот так, брат.', 'И похвалом Рубрика по-братски.'):
                await feed(text)
            self.assertEqual(len(queued), 1)
            await feed('А почему на закате красное?')
            self.assertEqual(len(queued), 2)
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
            await state.close()
    async def test_name_alone_arms_followup_without_saying_chego(self):
        class STT:
            last_ms = {}
            text = 'Серёга!'
            async def transcribe(self, *args, **kwargs): return self.text
        state = GuildSession(1, Config(conversation_mode='summon'), STT(), None, None, logging.getLogger(), logging.getLogger())
        queued = []
        state.enqueue = queued.append
        worker = asyncio.create_task(state.recognize())
        async def feed(text):
            state.stt.text = text
            now = time.monotonic()
            state.audio_queue.put_nowait(Utterance(SimpleNamespace(id=1, display_name='Тест'), b'a', now, now, now, 0))
            await state.audio_queue.join()
        try:
            await feed('Серёга!')
            self.assertEqual(queued, [])
            await feed('Объясни почему небо голубое')
            self.assertEqual(len(queued), 1)
            self.assertIn('почему небо', queued[0].text)
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
            await state.close()

    async def test_self_call_is_not_sent_to_voice(self):
        service = OllamaService(logging.getLogger(), Config())
        await service.client.aclose()
        rows = [{'message': {'content': 'Серёга, ты тут? '}, 'done': False},
                {'message': {'content': 'Да, я тут.'}, 'done': True}]
        service.client = httpx.AsyncClient(base_url='http://localhost', transport=httpx.MockTransport(
            lambda request: httpx.Response(200, text='\n'.join(json.dumps(row) for row in rows))))
        try:
            answer = ' '.join([part async for part in service.stream_sentences([
                {'role': 'user', 'content': '[Собеседник]: Как дела?'}])])
            self.assertNotIn('Серёга, ты тут?', answer)
        finally:
            await service.close()

    def test_prompt_distinguishes_bot_from_people(self):
        prompt = Conversation(Config()).messages(1, 'Серёга', 'Ты тут?')
        self.assertIn('Участник', prompt[-1]['content'])
        self.assertIn('не обращайся к самому себе', prompt[0]['content'].lower())
