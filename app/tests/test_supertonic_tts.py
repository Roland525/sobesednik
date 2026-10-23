import logging
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import numpy as np

from bot.config import Config
from bot.services.tts_service import TTSService


class SupertonicTests(unittest.TestCase):
    def test_selected_voice_reaches_backend_and_outputs_pcm48_stereo(self):
        config = Config(tts_engine='supertonic', tts_volume=.5, tts_speed=1.06)
        service = TTSService(logging.getLogger('test'), config)
        try:
            service.voice = Mock()
            service.voice.synthesize.return_value = (np.full(4410, .25, dtype=np.float32), 44100)
            pcm = service.synthesize('Привет, 2 раза!')
            service.voice.synthesize.assert_called_once_with('Привет, два раза!', speed=1.06, steps=8)
            samples = np.frombuffer(pcm, dtype='<i2').reshape(-1, 2)
            self.assertEqual(len(samples), 4800)
            np.testing.assert_array_equal(samples[:, 0], samples[:, 1])
            self.assertAlmostEqual(float(np.median(samples)), 4095, delta=5)
        finally:
            service.close()

    def test_invalid_audio_does_not_reach_discord(self):
        service = TTSService(logging.getLogger('test'), Config(tts_engine='supertonic'))
        try:
            service.voice = Mock()
            for audio in (np.array([]), np.array([np.nan]), np.array([np.inf])):
                service.voice.synthesize.return_value = (audio, 44100)
                with self.assertRaises(RuntimeError):
                    service.synthesize('Привет.')
        finally:
            service.close()

    def test_config_limits_engine_voice_and_cpu_work(self):
        Config(tts_engine='supertonic', tts_supertonic_voice='M3').validate()
        for values in ({'tts_engine': 'cloud'}, {'tts_supertonic_voice': '../secret'},
                       {'tts_supertonic_steps': 0}, {'tts_supertonic_steps': True},
                       {'tts_supertonic_steps': 8.5}):
            with self.assertRaises(ValueError):
                Config(**values).validate()

    def test_missing_model_fails_locally_without_download(self):
        from bot.services.supertonic_voice import SupertonicVoice
        with tempfile.TemporaryDirectory() as folder:
            with patch('urllib.request.urlopen', side_effect=AssertionError('Network forbidden')):
                with self.assertRaisesRegex(RuntimeError, 'Supertonic'):
                    SupertonicVoice.load(Path(folder), 'M3')

    def test_model_duration_trims_padding_before_pcm_conversion(self):
        from bot.services.supertonic_voice import SupertonicVoice
        engine = Mock(sample_rate=44100)
        engine.return_value = (np.ones((1, 5000), dtype=np.float32), np.array([.1]))
        voice = SupertonicVoice(engine, Mock())
        audio, rate = voice.synthesize('Привет.', speed=1.06, steps=8)
        self.assertEqual(rate, 44100)
        self.assertEqual(len(audio), 4410)

    def test_long_reply_preserves_last_syllables_when_chunks_have_padding(self):
        from bot.vendor.supertonic_helper import TextToSpeech
        from bot.services.supertonic_voice import SupertonicVoice
        engine = TextToSpeech.__new__(TextToSpeech)
        engine.sample_rate = 44100
        chunks = [np.concatenate([np.full(44100, value), np.zeros(1980)])[None, :]
                  for value in (.1, .2, .3)]
        engine._infer = Mock(side_effect=[(audio, np.array([1.0])) for audio in chunks])
        style = Mock(ttl=np.zeros((1, 50, 256)))
        text = '. '.join(['а' * 200, 'б' * 200, 'в' * 200]) + '.'
        voice = SupertonicVoice(engine, style)
        audio, rate = voice.synthesize(text, speed=1.06, steps=8)
        self.assertEqual(engine._infer.call_count, 3)
        self.assertAlmostEqual(len(audio) / rate, 3.6, places=4)
        np.testing.assert_allclose(audio[-44100:], .3)


if __name__ == '__main__':
    unittest.main()
