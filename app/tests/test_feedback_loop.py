import asyncio
import logging
import time
import unittest
import json
import httpx
from types import SimpleNamespace

from bot.config import Config
from bot.session import GuildSession, Question
from bot.voice.transcription_sink import Utterance
from bot.voice.transcription_sink import TranscriptionSink
from bot.controls import voice_control
from bot.services.ollama_service import OllamaService
from bot.services.audio_service import AudioService
from unittest.mock import Mock


def session():
    return GuildSession(1, Config(), None, None, None, logging.getLogger(), logging.getLogger())


class EchoTests(unittest.TestCase):
    def test_stop_commands_with_natural_fillers_but_not_quotes_or_negation(self):
        for phrase in ('Серёга, блядь, заткнись уже!', 'Заткнись, блять!',
                       'Серёга, можешь помолчать?', 'Ну, замолчи пожалуйста уже'):
            self.assertEqual(voice_control(phrase), 'mute', phrase)
        for phrase in ('Серёга, не молчи', 'Он сказал заткнись уже', 'Почему ты сказал заткнись?'):
            self.assertNotEqual(voice_control(phrase), 'mute', phrase)

    def test_fast_stop_still_receives_audio_after_three_seconds_of_open_microphone(self):
        calls = []
        sink = TranscriptionSink(Config(), asyncio.Queue(), logging.getLogger(),
                                 control_callback=lambda *args: calls.append(args))
        class AlwaysSpeech:
            def is_speech(self, *args): return True
        user = SimpleNamespace(id=1, bot=False)
        import struct
        pcm = struct.pack('<h', 500) * 1920
        sink.feed(user, pcm, 100)
        sink.speakers[1].vad = AlwaysSpeech()
        for frame in range(1, 260):
            sink.feed(user, pcm, 100 + frame * .02)
        self.assertTrue(any(at > 104 for _, _, at in calls))
        sink.cleanup()

    def test_non_speech_never_reaches_whisper(self):
        service = AudioService(logging.getLogger(), Config())
        service.model = Mock()
        service.model.transcribe.return_value = ([SimpleNamespace(text='Привет!', avg_logprob=-.3, no_speech_prob=.1)], None)
        try:
            self.assertEqual(service.full_transcript(bytes(32000)), '')
            service.model.transcribe.assert_not_called()
        finally:
            service.close()

    def test_short_greeting_loopback_is_rejected_during_playback(self):
        state = session()
        now = time.monotonic()
        state.playing_since = now - 1
        state.recent_spoken = [('привет', now - 1)]
        utterance = SimpleNamespace(started=now - .8, ended=now)
        self.assertTrue(state.is_echo('Привет!', utterance))

    def test_fragment_of_spoken_sentence_is_rejected_as_echo(self):
        state = session()
        now = time.monotonic()
        state.playing_since = now - 2
        state.recent_spoken = [('приветнудаяздесьчтослучилось', now - 2)]
        self.assertTrue(state.is_echo('Привет, ну да.', SimpleNamespace(started=now - 1, ended=now)))

    def test_greeting_after_playback_is_a_real_new_turn(self):
        state = session()
        now = time.monotonic()
        state.last_play_end = now - 3
        state.recent_spoken = [('привет', now - 5)]
        self.assertFalse(state.is_echo('Привет!', SimpleNamespace(started=now - 1, ended=now)))


class LoopAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_strict_mode_requires_name_even_in_active_followup(self):
        class STT:
            last_ms = {}
            text = 'Серёга, привет!'
            async def transcribe(self, *args, **kwargs): return self.text
        state = GuildSession(1, Config(conversation_mode='strict',followup_enabled=False), STT(), None, None,
                             logging.getLogger(), logging.getLogger())
        captured = []
        state.enqueue = captured.append
        worker = asyncio.create_task(state.recognize())
        try:
            for text in ('Серёга, сколько два плюс два?', 'А три плюс три?',
                         'Серёга, а три плюс три?'):
                state.stt.text = text
                now = time.monotonic()
                state.audio_queue.put_nowait(Utterance(SimpleNamespace(id=1, display_name='Тест'), b'a', now, now, now, 0))
                await state.audio_queue.join()
                state.conversation.replied_to(1)
            self.assertEqual(len(captured), 2)
            self.assertFalse(state.participant_mode)
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
            await state.close()

    async def test_greeting_loop_after_pause_and_backchannels_do_not_start_new_replies(self):
        class STT:
            last_ms = {}
            text = 'Серёга, привет!'
            async def transcribe(self, *args, **kwargs): return self.text
        state = session()
        state.stt = STT()
        captured = []
        state.enqueue = captured.append
        worker = asyncio.create_task(state.recognize())
        async def feed(text, uid=1):
            state.stt.text = text
            now = time.monotonic()
            state.audio_queue.put_nowait(Utterance(SimpleNamespace(id=uid, display_name='Тест'), b'a', now, now, now, 0))
            await state.audio_queue.join()
        try:
            await feed('Серёга, привет!')
            state.conversation.history.append({'role': 'assistant', 'content': 'Привет, я тут.'})
            state.conversation.replied_to(1)
            await feed('Привет, привет.')
            await feed('Да, да, да.')
            await feed('Угу.')
            self.assertEqual(len(captured), 1)
            await feed('Серёга, почему небо голубое?')
            await feed('Из-за рассеяния света, получается?')
            self.assertEqual(len(captured), 3)
            # A different participant can greet him, and a one-word answer to
            # the bot's own question must still continue the conversation.
            await feed('Серёга, привет!', uid=2)
            state.conversation.history.append({'role': 'assistant', 'content': 'Ты играл в Ведьмака?'})
            await feed('Да.',uid=2)
            self.assertEqual(len(captured), 5)
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
            await state.close()

    async def test_model_repeating_a_sentence_is_not_spoken_in_a_loop(self):
        events = [{'message': {'content': text}, 'done': False}
                  for text in ('Привет! ', 'Привет! ', 'Привет! ')]
        events.append({'message': {'content': ''}, 'done': True})
        service = OllamaService(logging.getLogger(), Config())
        await service.client.aclose()
        service.client = httpx.AsyncClient(base_url='http://localhost', transport=httpx.MockTransport(
            lambda request: httpx.Response(200, text='\n'.join(json.dumps(row) for row in events))))
        try:
            spoken = [part async for part in service.stream_sentences([])]
            self.assertEqual(spoken, ['Привет!'])
        finally:
            await service.close()

    async def test_pending_voice_turns_from_same_speaker_do_not_pile_up(self):
        state = session()
        state.tasks = [SimpleNamespace()]  # Do not start consumers before examining the pending queue.
        try:
            for text in ('Привет', 'Да да', 'Серёга, почему небо голубое?'):
                state.enqueue(Question(1, 'Тест', text, time.monotonic()))
            self.assertEqual(state.questions.qsize(), 1)
            self.assertIn('почему небо', state.questions.get_nowait()[2].text)
            state.questions.task_done()
        finally:
            state.tasks.clear()

    async def test_personal_question_after_hush_does_not_reenable_unsolicited_speech(self):
        class STT:
            last_ms = {}
            async def transcribe(self, *args, **kwargs): return 'Серёга, сколько два плюс два?'
        state = session()
        state.stt = STT()
        await state.hush()
        state.voice_client = SimpleNamespace(is_connected=lambda: True,
            channel=SimpleNamespace(members=[SimpleNamespace(id=1, bot=False, display_name='Тест', name='test')]))
        captured = []
        state.enqueue = captured.append
        worker = asyncio.create_task(state.recognize())
        try:
            now = time.monotonic()
            state.audio_queue.put_nowait(Utterance(SimpleNamespace(id=1, display_name='Тест'), b'a', now, now, now, 0))
            await state.audio_queue.join()
            self.assertEqual(len(captured), 1)
            state.joined_at = now - 60
            state.last_human_at = now - 60
            self.assertFalse(state.initiative_allowed(now))
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
            state.voice_client = None
            await state.close()

    async def test_fast_stop_bypasses_busy_full_transcription(self):
        blocked = asyncio.Event()
        entered = asyncio.Event()
        class STT:
            last_ms = {}
            async def transcribe(self, *args, **kwargs):
                entered.set()
                await blocked.wait()
                return 'Длинная чужая фраза'
            async def recognize_control(self, pcm): return 'mute'
        class Voice:
            stopped = False
            def stop_playing(self): self.stopped = True
            def is_connected(self): return False
            def is_listening(self): return False
            async def disconnect(self, **kwargs): pass
        state = session()
        state.stt = STT()
        state.voice_client = Voice()
        state.start()
        try:
            now = time.monotonic()
            member = SimpleNamespace(id=1, display_name='Тест')
            state.audio_queue.put_nowait(Utterance(member, b'a', now, now, now, 0))
            await asyncio.wait_for(entered.wait(), 1)
            state.offer_control_audio(member, b'b', now)
            async with asyncio.timeout(.3):
                while not state.muted:
                    await asyncio.sleep(.005)
            self.assertTrue(state.voice_client.stopped)
            self.assertFalse(blocked.is_set())
        finally:
            blocked.set()
            await state.close()


if __name__ == '__main__':
    unittest.main()
