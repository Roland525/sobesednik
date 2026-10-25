import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from bot import config
from desktop.models import model_title, model_detail


class ComputeControlsTests(unittest.TestCase):
    def test_modes_apply_explicit_thread_and_offload_settings(self):
        with patch('bot.config.os.cpu_count', return_value=24):
            self.assertEqual(config.compute_settings('auto'), {
                'ollama_num_gpu': -1, 'ollama_num_thread': 4,
                'whisper_cpu_threads': 4, 'tts_cpu_threads': 4})
            self.assertEqual(config.compute_settings('cpu')['ollama_num_thread'], 8)
            self.assertEqual(config.compute_settings('cpu')['ollama_num_gpu'], 0)
        with patch('bot.config.os.cpu_count', return_value=2):
            self.assertEqual(config.compute_settings('cpu')['ollama_num_thread'], 2)
        for invalid in ('gpu', '', 70, None):
            with self.assertRaises(ValueError): config.compute_settings(invalid)

    def test_legacy_migration_is_nonmutating_and_explicit_mode_wins(self):
        for raw, expected in (({}, 'auto'), ({'resource_level': 0}, 'cpu'),
                              ({'resource_level': 20}, 'auto'),
                              ({'ollama_num_gpu': 0}, 'cpu'),
                              ({'compute_mode': 'auto', 'resource_level': 0}, 'auto')):
            with self.subTest(raw=raw):
                before = dict(raw)
                actual = config.normalize_compute(raw)
                self.assertEqual(raw, before)
                self.assertEqual(actual['compute_mode'], expected)
                self.assertEqual(actual['ollama_num_gpu'], 0 if expected == 'cpu' else -1)

    def test_config_load_keeps_voice_model_and_private_configuration(self):
        raw = {'resource_level': 0, 'ollama_model': 'sobesednik-learned:custom',
               'tts_supertonic_voice': 'M5', 'tts_volume': 1.13, 'tts_speed': 1.08,
               'whisper_model': 'models/custom-stt', 'future_setting': 'keep',
               'ollama_num_thread': 31, 'whisper_cpu_threads': 12, 'tts_cpu_threads': 15}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'config.json'
            path.write_text(json.dumps(raw), encoding='utf-8')
            before = path.read_bytes()
            with patch.dict('os.environ', {}, clear=True): cfg = config.Config.load(path)
            self.assertEqual(path.read_bytes(), before)
        self.assertEqual(cfg.compute_mode, 'cpu')
        self.assertEqual(cfg.ollama_num_gpu, 0)
        for key in ('ollama_model', 'tts_supertonic_voice', 'tts_volume', 'tts_speed', 'whisper_model'):
            self.assertEqual(getattr(cfg, key), raw[key])
        self.assertEqual(cfg.whisper_cpu_threads, 4)
        self.assertEqual(cfg.tts_cpu_threads, 4)

    def test_invalid_mode_is_rejected_before_runtime(self):
        with self.assertRaises(ValueError): config.Config(compute_mode='magic').validate()
        with self.assertRaises(ValueError): config.normalize_compute({'compute_mode': 'magic'})

    def test_known_models_describe_purpose_without_an_intelligence_score(self):
        self.assertEqual(model_title('huihui_ai/qwen3-abliterated:4b'), 'Быстрые ответы · Qwen 3 4B')
        self.assertEqual(model_title('huihui_ai/qwen3-abliterated:8b'), 'Универсальная · Qwen 3 8B')
        self.assertEqual(model_title('huihui_ai/qwen3.5-abliterated:9B'), 'Дополнительная · Qwen 3.5 9B')
        detail = model_detail('huihui_ai/qwen3-abliterated:4b', 2*1024**3)
        self.assertIn('2.00 ГиБ', detail)
        self.assertIn('памят', detail)
        self.assertIn('меньше', detail.casefold())
        self.assertNotIn('умнее', detail)
        self.assertTrue(model_title('sobesednik-learned:custom'))
        self.assertTrue(model_detail('private/local:v1'))
