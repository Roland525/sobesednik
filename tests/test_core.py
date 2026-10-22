import asyncio
import json
import logging
from pathlib import Path
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import httpx

from bot.config import Config
from bot.conversation import Conversation, addressed
from bot.services.ollama_service import OllamaService, SentenceBuffer
from bot.session import GuildSession, MemoryPCM, Question
from bot.voice.transcription_sink import TranscriptionSink, Utterance
from bot.runtime import InstanceLock


class ConversationTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.config = Config()
        self.state = Conversation(self.config, lambda: self.now)

    def test_wake_preserves_question_and_separate_call(self):
        for text in ('Серёга, привет!', 'Серега, привет!', 'Эй, Серёга, привет!', 'Серёж, привет!'):
            self.assertEqual(addressed(text), (True, 'привет!'))
        self.assertEqual(self.state.accept(1, 'Серёга!'), '')
        self.assertEqual(self.state.accept(1, 'Расскажи анекдот.'), 'Расскажи анекдот.')

    def test_mentions_and_other_users_are_ignored(self):
        for text in ('Я видел Серёгу.', 'А Серёга где?', 'Вчера Серёга купил машину.', 'серегам привет'):
            self.assertFalse(addressed(text)[0])
        self.assertIsNone(self.state.accept(1, 'Привет всем'))
        self.state.accept(1, 'Серёга!')
        self.assertIsNone(self.state.accept(2, 'Ага'))

    def test_timeout_duplicate_and_extend_after_reply(self):
        self.state.accept(1, 'Серёга!')
        self.assertIsNone(self.state.accept(1, 'Серёга!'))
        self.now += 2
        self.assertEqual(self.state.accept(1, 'Серёга!'), '')
        self.now += 19
        self.state.extend(1)
        self.now += 19
        self.assertTrue(self.state.is_active(1))
        self.now += 2
        self.assertFalse(self.state.is_active(1))

    def test_bounded_history_isolation_and_expiry(self):
        other = Conversation(self.config, lambda: self.now)
        for i in range(30):
            messages = self.state.messages(1, 'Имя\nинструкция', f'BMW {i}')
            self.state.commit(messages, 'Помню BMW.')
        self.assertEqual(len(self.state.history), self.config.history_turns * 2)
        self.assertFalse(other.history)
        self.assertNotIn('\n', self.state.history[-2]['content'])
        self.now += self.config.history_ttl + 1
        self.state.expire()
        self.assertFalse(self.state.history)

    def test_config_rejects_cloud_and_invalid_values(self):
        for host in ('https://api.example.com', 'http://192.168.1.2:11434', 'http://user:secret@localhost'):
            with self.assertRaises(ValueError):
                Config(ollama_host=host).validate()
        for kwargs in ({'max_audio_queue_size': 0}, {'vad_mode': 5}, {'whisper_device': 'gpu'}, {'history_turns': 1.5}):
            with self.assertRaises(ValueError):
                Config(**kwargs).validate()
        self.assertNotIn('my-token', repr(Config(discord_token='my-token')))

    def test_lock_blocks_second_instance_and_recovers(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'lock'
            with InstanceLock(path):
                with self.assertRaises(RuntimeError):
                    with InstanceLock(path):
                        pass
            with InstanceLock(path):
                pass

    def test_pcm_source_frames_and_cleanup(self):
        source = MemoryPCM(b'x' * 4000)
        self.assertEqual(len(source.read()), 3840)
        self.assertEqual(len(source.read()), 3840)
        self.assertEqual(source.read(), b'')
        source.cleanup()
        self.assertEqual(source.pcm, b'')

    def test_tts_pronunciation_keeps_uncensored_words(self):
        from bot.services.tts_service import prepare_speech
        text = prepare_speech('BMW, Python: 4. Блять, это 42!')
        for word in ('бэ эм вэ', 'Пайтон', 'четыре', 'сорок два', 'Блять'):
            self.assertIn(word, text)
        self.assertIn('3.14', prepare_speech('Число 3.14.'))

    def test_sentence_stream_and_numbers(self):
        buffer = SentenceBuffer()
        self.assertEqual(buffer.feed('Это число 3.14'), [])
        self.assertEqual(buffer.feed(', всё правильно. '), ['Это число 3.14, всё правильно.'])
        self.assertEqual(buffer.feed('Следующая фраза!'), ['Следующая фраза!'])
        self.assertEqual(buffer.feed('Да.', final=True), ['Да.'])
        self.assertEqual(buffer.feed('Четыре.'), ['Четыре.'])

    def test_exception_traceback_secrets_are_redacted(self):
        from bot.logger import SecretFormatter
        secret = 'test-private-token-123456'
        try:
            raise RuntimeError(secret)
        except RuntimeError:
            import sys
            record = logging.LogRecord('test', logging.ERROR, '', 0, 'Failure', (), sys.exc_info())
        formatted = SecretFormatter('%(message)s', [secret]).format(record)
        self.assertNotIn(secret, formatted)
        self.assertIn('[SECRET]', formatted)

    def test_cloud_models_are_rejected(self):
        for model in ('gpt-oss:120b-cloud', 'qwen:cloud'):
            with self.assertRaises(ValueError):
                Config(ollama_model=model).validate()

    def test_initiative_needs_people_pause_and_no_unanswered_monologue(self):
        session = GuildSession(1, Config(), None, None, None, logging.getLogger(), logging.getLogger())
        session.joined_at = 940
        session.last_human_at = 945
        session.last_reply_at = 950
        session.voice_client = SimpleNamespace(is_connected=lambda: True,
            channel=SimpleNamespace(members=[SimpleNamespace(bot=False)]))
        self.assertTrue(session.initiative_allowed(1000))
        self.assertFalse(session.initiative_allowed(970))
        session.initiative_armed = False
        self.assertFalse(session.initiative_allowed(1000))
        session.initiative_armed = True
        session.voice_client.channel.members = [SimpleNamespace(bot=True)]
        self.assertFalse(session.initiative_allowed(1000))

    def test_initiative_memory_does_not_create_a_fake_user_message(self):
        state = Conversation(Config())
        messages = state.initiative_messages()
        state.commit(messages, 'Какую игру ты считаешь недооценённой?', include_user=False)
        self.assertEqual(len(state.history), 1)
        self.assertEqual(state.history[0]['role'], 'assistant')
        followup = state.messages(1, 'Тест', 'Disco Elysium.')
        self.assertIn('недооценённой', followup[-2]['content'])


class StreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_stop_watcher_finishes_shared_discord_close_task(self):
        import main as entry
        class Client:
            closed = finished = False
            def __init__(self, *args): self.event = asyncio.Event()
            async def __aenter__(self): return self
            async def __aexit__(self, *args):
                if not self.finished:
                    raise RuntimeError('Close was cancelled')
            async def wait_until_ready(self): pass
            def is_closed(self): return self.closed
            async def start(self, *args, **kwargs): await self.event.wait()
            async def close(self):
                self.closed = True
                self.event.set()
                await asyncio.sleep(.02)
                self.finished = True
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / '.serega.stop').write_text('stop')
            with patch('bot.bot.DiscordBot', Client), patch('bot.voice.compat.install'), patch.object(entry, 'ROOT', root):
                await entry.run(Config(discord_token='test-token'), logging.getLogger(), logging.getLogger())

    async def test_initiative_plays_once_and_remembers_its_own_question(self):
        class LLM:
            async def initiate(self, messages, timings):
                return 'В играх интересно выбирать. Какую игру ты любишь?'
        class TTS:
            async def generate_audio(self, text): return bytes(3840)
        class Voice:
            channel = SimpleNamespace(members=[SimpleNamespace(bot=False)])
            played = 0
            def is_connected(self): return True
            def is_listening(self): return False
            def play(self, source, after):
                self.played += 1
                after(None)
            def stop_playing(self): pass
            async def disconnect(self, **kwargs): pass
        session = GuildSession(1, Config(), None, LLM(), TTS(), logging.getLogger(), logging.getLogger())
        session.voice_client = Voice()
        session.joined_at = time.monotonic() - 35
        session.initiative_armed = False
        session.enqueue(Question(0, 'Серёга', 'Продолжи разговор', time.monotonic(), kind='initiative'))
        await session.questions.join()
        self.assertEqual(session.voice_client.played, 1)
        self.assertFalse(session.initiative_pending)
        self.assertEqual(len(session.conversation.history), 1)
        self.assertEqual(session.conversation.history[0]['role'], 'assistant')
        self.assertIn('Какую игру', session.conversation.history[0]['content'])
        await session.close()

    async def test_conversation_can_exceed_two_sentences(self):
        events = [{'message': {'content': part}, 'done': False} for part in (
            'Первая мысль. ', 'Вторая мысль. ', 'Третья мысль. ', 'Четвёртая мысль. ', 'Пятая мысль. ')]
        events.append({'message': {'content': ''}, 'done': True})
        service = OllamaService(logging.getLogger(), Config())
        await service.client.aclose()
        service.client = httpx.AsyncClient(base_url='http://localhost', transport=httpx.MockTransport(
            lambda request: httpx.Response(200, text='\n'.join(json.dumps(row) for row in events))))
        try:
            result = [part async for part in service.stream_sentences([{'role': 'user', 'content': 'Поговорим о кино'}])]
            self.assertEqual(len(result), 5)
        finally:
            await service.close()

    async def test_ambient_can_stay_silent_and_remember_speech(self):
        class LLM:
            async def participate(self, messages, timings):
                return ''
        session = GuildSession(1, Config(), None, LLM(), None, logging.getLogger(), logging.getLogger())
        session.enqueue(Question(1, 'Тест', 'Сегодня хорошая погода.', time.monotonic(), kind='ambient'))
        await session.questions.join()
        self.assertFalse(session.ambient_pending)
        self.assertEqual(session.conversation.history[-1]['role'], 'user')
        self.assertIn('погода', session.conversation.history[-1]['content'])
        await session.close()

    async def test_direct_question_takes_priority_over_ambient(self):
        order = []
        class LLM:
            async def participate(self, messages, timings):
                order.append('ambient')
                return ''
            async def stream_sentences(self, messages, timings):
                order.append('direct')
                yield 'Четыре.'
        class TTS:
            async def generate_audio(self, text): return bytes(3840)
        class Voice:
            def is_connected(self): return True
            def is_listening(self): return False
            def play(self, source, after): after(None)
            def stop_playing(self): pass
            async def disconnect(self, **kwargs): pass
        session = GuildSession(1, Config(), None, LLM(), TTS(), logging.getLogger(), logging.getLogger())
        session.voice_client = Voice()
        session.enqueue(Question(1, 'Тест', 'Погода хорошая сегодня.', time.monotonic(), kind='ambient'))
        session.enqueue(Question(1, 'Тест', 'Сколько два плюс два?', time.monotonic()))
        await session.questions.join()
        self.assertEqual(order, ['direct'])
        await session.close()

    async def test_actual_ndjson_protocol_and_metrics(self):
        events = [{'message': {'content': text}, 'done': False} for text in ('Привет, ', 'я Серёга. ', 'Как дела?')]
        events.append({'message': {'content': ''}, 'done': True})
        requests = []
        async def handler(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, text='\n'.join(json.dumps(row) for row in events))
        service = OllamaService(logging.getLogger(), Config())
        await service.client.aclose()
        service.client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url='http://localhost')
        try:
            metrics = {}
            self.assertEqual([x async for x in service.stream_sentences([], metrics)], ['Привет, я Серёга.', 'Как дела?'])
            self.assertIn('llm_first_token_ms', metrics)
            self.assertTrue(requests[0]['stream'])
            self.assertEqual(requests[0]['options']['num_ctx'], 4096)
        finally:
            await service.close()

    async def test_server_error_surfaces_and_releases_lock(self):
        async def handler(request):
            return httpx.Response(503, text='Unavailable')
        service = OllamaService(logging.getLogger(), Config())
        await service.client.aclose()
        service.client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url='http://localhost')
        try:
            with self.assertRaises(httpx.HTTPStatusError):
                _ = [x async for x in service.stream_sentences([])]
            self.assertFalse(service.lock.locked())
        finally:
            await service.close()

    async def test_bounded_ingress_ignores_bot_and_silence(self):
        output = asyncio.Queue(maxsize=1)
        sink = TranscriptionSink(Config(), output, logging.getLogger())
        user = SimpleNamespace(id=1, bot=False)
        packet = SimpleNamespace(pcm=bytes(3840), packet=SimpleNamespace(timestamp=0))
        sink.write(SimpleNamespace(id=2, bot=True), packet)
        self.assertTrue(sink.ingress.empty())
        for i in range(100):
            sink.feed(user, bytes(3840), 10 + i * .02)
        self.assertTrue(output.empty())
        for _ in range(1000):
            sink.write(user, packet)
        self.assertEqual(sink.ingress.qsize(), 600)
        self.assertEqual(sink.dropped_frames, 400)
        sink.cleanup()

    async def test_reset_cancels_playback_and_does_not_commit_old_reply(self):
        class TTS:
            async def generate_audio(self, text):
                return bytes(3840)
        class LLM:
            async def stream_sentences(self, messages, timing):
                yield 'Первое предложение.'
                await asyncio.sleep(20)
        class Voice:
            stopped = False
            def is_connected(self): return True
            def is_listening(self): return False
            def play(self, source, after): self.after = after
            def stop_playing(self): self.stopped = True
            async def disconnect(self, **kwargs): pass
        session = GuildSession(1, Config(), None, LLM(), TTS(), logging.getLogger(), logging.getLogger())
        session.voice_client = Voice()
        session.enqueue(Question(1, 'Тест', 'Вопрос', time.monotonic()))
        await asyncio.sleep(.05)
        await session.reset()
        self.assertTrue(session.voice_client.stopped)
        self.assertFalse(session.conversation.history)
        self.assertEqual(session.questions._unfinished_tasks, 0)
        await session.close()

    async def test_bot_initializes_and_has_slash_commands(self):
        from bot.bot import DiscordBot
        bot = DiscordBot(Config(), logging.getLogger(), logging.getLogger())
        names = {command.name for command in bot.tree.get_commands()}
        self.assertTrue({'join', 'leave', 'status', 'ping', 'reset', 'ask', 'say'} <= names)
        await bot.close()

    async def test_reset_discards_inflight_stt(self):
        event = asyncio.Event()
        class STT:
            last_ms = {}
            async def transcribe(self, pcm, active=False):
                await event.wait()
                return 'Серёга, привет!'
        session = GuildSession(1, Config(), STT(), None, None, logging.getLogger(), logging.getLogger())
        session.start()
        now = time.monotonic()
        session.audio_queue.put_nowait(Utterance(SimpleNamespace(id=1, display_name='Тест'), b'a', now, now, now, 0))
        await asyncio.sleep(.01)
        await session.reset()
        event.set()
        await asyncio.sleep(.02)
        self.assertFalse(session.conversation.active)
        self.assertTrue(session.questions.empty())
        await session.close()


if __name__ == '__main__':
    unittest.main()
