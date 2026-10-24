import copy
import contextlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

from bot.learning_cycle import MaintenancePolicy, quality_gate
from bot.config import Config


def report():
    return {'cases': [{'case': str(i), 'narrow_check': True, 'elapsed_ms': 1800,
                       'timings': {'llm_first_sentence_ms': 1500}} for i in range(15)]}


class LearningCycleTests(unittest.TestCase):
    def test_prepared_lora_base_cannot_replace_other_selected_model_families(self):
        from bot.learning_cycle import compatible_training_model
        self.assertTrue(compatible_training_model('huihui_ai/qwen3-abliterated:8b'))
        self.assertTrue(compatible_training_model('sobesednik-learned-20261024-123456:latest'))
        for model in ['huihui_ai/qwen3-abliterated:4b','huihui_ai/qwen3.5-abliterated:9B','sobesednik-learned-untrusted:latest']:
            self.assertFalse(compatible_training_model(model))
    def test_automatic_training_requires_capture_and_is_opt_in(self):
        self.assertFalse(Config().learning_auto_enabled)
        with self.assertRaises(ValueError):
            Config(learning_auto_enabled=True, learning_capture_enabled=False).validate()
        with self.assertRaises(ValueError):
            Config(ollama_model=123).validate()

    def test_only_idle_new_data_after_cooldown_are_ready(self):
        policy = MaintenancePolicy()
        args = dict(now=100000, idle_since=98000, humans=False, count=40, last_count=0, last_attempt=0)
        self.assertTrue(policy.ready(**args))
        for key, value in [('humans', True), ('idle_since', 99990), ('count', 31),
                           ('last_count', 9), ('last_attempt', 99000)]:
            self.assertFalse(policy.ready(**{**args, key: value}), key)
        self.assertFalse(policy.ready(**{**args, 'last_count': 50}))

    def test_gate_requires_complete_matching_cases_and_no_regression(self):
        baseline = report()
        trained = {'heldout_loss_before': 2., 'heldout_loss_after': 1., 'lora_B_norm': 1.}
        self.assertEqual(quality_gate(baseline, report(), trained), [])
        bad = report()
        bad['cases'][0]['narrow_check'] = False
        self.assertTrue(quality_gate(baseline, bad, trained))
        for candidate in ({'cases': []}, {'cases': report()['cases'][:-1]}, {'failure': {}, **report()}):
            self.assertTrue(quality_gate(baseline, candidate, trained))
        duplicate = report()
        duplicate['cases'][-1]['case'] = '0'
        self.assertTrue(quality_gate(baseline, duplicate, trained))

    def test_gate_rejects_slow_nonfinite_or_unchanged_training(self):
        trained = {'heldout_loss_before': 2., 'heldout_loss_after': 1., 'lora_B_norm': 1.}
        slow = report()
        for case in slow['cases']:
            case['timings']['llm_first_sentence_ms'] = 3000
        self.assertTrue(quality_gate(report(), slow, trained))
        for key, value in [('heldout_loss_after', 3.), ('heldout_loss_after', float('nan')),
                           ('lora_B_norm', 0.)]:
            self.assertTrue(quality_gate(report(), report(), {**trained, key: value}))
        broken = copy.deepcopy(report())
        broken['cases'][0]['elapsed_ms'] = float('nan')
        self.assertTrue(quality_gate(report(), broken, trained))


class MaintenanceIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_concurrent_config_uses_snapshot_without_overwriting_edits(self):
        import main as entry
        calls = []
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config_path = root / 'config.json'
            config_path.write_text('{"bot_name":"Test"}')
            def voice(command, **kwargs):
                if not calls:
                    Path(command[command.index('--maintenance-result')+1]).write_text('{"guild":1,"channel":22}')
                else:
                    self.assertIn('--runtime-config', command)
                    saved = Path(command[command.index('--runtime-config')+1]).read_text()
                    self.assertEqual(json.loads(saved)['bot_name'], 'Test')
                calls.append(command)
                return 0
            def worker(*args):
                config_path.write_text('{unfinished edit')
            with patch.object(entry, 'ROOT', root), \
                 patch.object(entry, 'InstanceLock', return_value=contextlib.nullcontext()), \
                 patch.object(entry.Config, 'load', side_effect=ValueError('invalid edit')), \
                 patch('tools.process_tree.run_owned', side_effect=voice), \
                 patch('tools.run_learning_cycle.execute', side_effect=worker):
                entry.supervise(NS(resume_channel=22, voice_smoke=False), NS(warning=lambda *a: None))
            self.assertEqual(config_path.read_text(), '{unfinished edit')

    async def test_supervisor_waits_for_voice_exit_and_restores_after_worker_timeout(self):
        import main as entry
        events = []
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            def voice(command, **kwargs):
                if not events:
                    path = Path(command[command.index('--maintenance-result') + 1])
                    path.write_text(json.dumps({'guild': 1, 'channel': 22}))
                events.append('voice_exited')
                return 0
            def worker(*args):
                self.assertEqual(events[-1], 'voice_exited')
                events.append('worker_timeout')
                raise subprocess.TimeoutExpired('worker', 1)
            with patch.object(entry, 'ROOT', root), \
                 patch.object(entry, 'InstanceLock', return_value=contextlib.nullcontext()), \
                 patch('tools.process_tree.run_owned', side_effect=voice), \
                 patch('tools.run_learning_cycle.execute', side_effect=worker):
                args = NS(resume_channel=22, voice_smoke=False)
                self.assertEqual(entry.supervise(args, NS(warning=lambda *a: None)), 0)
            self.assertEqual(events, ['voice_exited', 'worker_timeout', 'voice_exited'])

    async def test_humans_prevent_maintenance_and_empty_channel_requests_once(self):
        import asyncio
        from bot.bot import DiscordBot
        human = NS(bot=False)
        channel = NS(members=[human])
        vc = NS(channel=channel)
        fake = NS(config=Config(learning_capture_enabled=True, learning_auto_enabled=True),
                  learning=NS(db=NS(execute=lambda *a: NS(fetchone=lambda: (40,)))),
                  guilds=[NS(id=1, voice_client=vc)], learning_idle_since=0,
                  learning_request=None, shutting_down=False,
                  voice_transitions=set(), guild_locks={},
                  logger=NS(info=lambda *a: None))
        closed = []
        async def close():
            closed.append(True)
        fake.close = close
        with patch('tools.run_learning_cycle.prerequisites', return_value=True), \
             patch('tools.run_learning_cycle.disk_ready', return_value=True), \
             patch('tools.run_learning_cycle.snapshot', return_value=(40, [{}]*16)), \
             patch('pathlib.Path.exists', return_value=False), \
             patch('bot.bot.time.time', return_value=100000):
            DiscordBot.maybe_learning_maintenance(fake)
            self.assertIsNone(fake.learning_request)
            channel.members = []
            fake.learning_idle_since = 98000
            DiscordBot.maybe_learning_maintenance(fake)
            await asyncio.sleep(0)
            self.assertEqual(fake.learning_request, {'guild': 1, 'channel': 0})
            self.assertEqual(closed, [True])
            DiscordBot.maybe_learning_maintenance(fake)
            await asyncio.sleep(0)
            self.assertEqual(len(closed), 1)
