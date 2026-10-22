import asyncio
import logging
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from bot.config import Config
from bot.services.audio_service import AudioService
from bot.services.ollama_service import OllamaService


class STTStallTests(unittest.IsolatedAsyncioTestCase):
    async def test_timeout_does_not_enqueue_more_native_decodes_and_recovers_when_old_work_finishes(self):
        config = Config()
        config.whisper_timeout = .03
        service = AudioService(logging.getLogger(), config)
        release = threading.Event()
        def blocked(pcm):
            release.wait(2)
            return 'Серёга, привет.'
        service.full_transcript = Mock(side_effect=blocked)
        try:
            self.assertEqual(await service.transcribe(b'a', active=True), '')
            self.assertEqual(service.last_ms['stt_timeout'], 1)
            self.assertFalse(service.decode_future.done())
            self.assertEqual(await service.transcribe(b'b', active=True), '')
            self.assertEqual(service.last_ms['stt_busy_rejected'], 1)
            self.assertEqual(service.full_transcript.call_count, 1)
            release.set()
            await service.decode_future
            self.assertEqual(await service.transcribe(b'c', active=True), 'Серёга, привет.')
        finally:
            release.set()
            service.close()

    async def test_cancellation_keeps_native_work_tracked(self):
        service = AudioService(logging.getLogger(), Config())
        release = threading.Event()
        service.full_transcript = lambda pcm: (release.wait(2) and 'Серёга') or ''
        try:
            task = asyncio.create_task(service.transcribe(b'a', active=True))
            while service.decode_future is None:
                await asyncio.sleep(.001)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            self.assertFalse(service.decode_future.cancelled())
            self.assertEqual(await service.transcribe(b'b', active=True), '')
            release.set()
            await service.decode_future
        finally:
            release.set()
            service.close()

    async def test_gpu_budget_is_passed_to_local_model(self):
        service = OllamaService(logging.getLogger(), Config(ollama_num_gpu=24))
        try:
            self.assertEqual(service.options['num_gpu'], 24)
        finally:
            await service.close()


class DecodeBudgetTests(unittest.TestCase):
    def test_short_and_long_audio_have_bounded_token_budgets(self):
        service = AudioService(logging.getLogger(), Config())
        service.model = Mock()
        service.model.transcribe.return_value = ([SimpleNamespace(text='Серёга, привет.', avg_logprob=-.1,
                                                                  no_speech_prob=.01, compression_ratio=1)], None)
        try:
            for seconds, budget in ((1, 48), (30, 224)):
                with patch('faster_whisper.vad.get_speech_timestamps', return_value=[{'start': 0, 'end': seconds * 16000}]):
                    self.assertEqual(service.full_transcript(bytes(seconds * 32000)), 'Серёга, привет.')
                self.assertEqual(service.model.transcribe.call_args.kwargs['max_new_tokens'], budget)
                self.assertEqual(service.model.transcribe.call_args.kwargs['no_repeat_ngram_size'], 3)
        finally:
            service.close()
