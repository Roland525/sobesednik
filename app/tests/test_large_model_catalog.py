import json
import threading
import unittest
from unittest.mock import patch

from desktop import models
from desktop import tuning


LARGE_MODELS=(
    'huihui_ai/qwen3-abliterated:14b',
    'huihui_ai/qwen3-abliterated:30b-a3b-instruct-2507-q4_K_M',
    'huihui_ai/qwen3-abliterated:32b-q4_K_M',
    'huihui_ai/qwen3.5-abliterated:27b-q4_K',
    'huihui_ai/qwen3.5-abliterated:35b-a3b-q4_K',
)


class LargeModelTests(unittest.TestCase):
    def test_large_catalog_has_explicit_local_variants_without_replacing_small_choices(self):
        names=dict(models.CATALOG)
        self.assertEqual(len(names),len(models.CATALOG))
        for name in LARGE_MODELS:
            self.assertIn(name,names);self.assertTrue(models.valid_model(name))
            self.assertNotEqual(models.model_title(name),name)
        for name in ('huihui_ai/qwen3-abliterated:4b','huihui_ai/qwen3-abliterated:8b'):
            self.assertIn(name,names)

    def test_moe_description_distinguishes_active_parameters_from_memory_and_speed(self):
        text=models.model_summary(LARGE_MODELS[1])
        self.assertIn('3,3',text);self.assertIn('без отдельного обдумывания',text)
        self.assertNotIn('Компактная',text)
        self.assertIn('3 млрд',models.model_summary(LARGE_MODELS[4]))
        self.assertIn('плотная',models.model_summary(LARGE_MODELS[2]).lower())

    def test_small_gpu_is_warned_but_download_stays_possible(self):
        text=models.model_hardware_hint(LARGE_MODELS[1],{'vram_total_gb':8})
        self.assertIn('24',text);self.assertIn('8',text);self.assertIn('CPU',text)
        self.assertIn('медлен',text)
        self.assertNotIn('запрещ',text)

    def test_large_gpu_suitability_is_an_estimate_not_a_latency_promise(self):
        for name,vram in ((LARGE_MODELS[0],16),(LARGE_MODELS[1],24),(LARGE_MODELS[4],32)):
            with self.subTest(name=name):
                text=models.model_hardware_hint(name,{'vram_total_gb':vram})
                self.assertIn('соответствует ориентиру',text)
                self.assertIn('провер',text)
                self.assertNotIn('секунд',text)

    def test_cpu_and_unknown_gpu_do_not_claim_gpu_speed(self):
        cpu=models.model_hardware_hint(LARGE_MODELS[1],{'vram_total_gb':48},'cpu')
        self.assertIn('только процессор',cpu.lower());self.assertIn('медлен',cpu)
        for value in (None,0,float('nan'),float('inf'),'unknown'):
            text=models.model_hardware_hint(LARGE_MODELS[1],{'vram_total_gb':value})
            self.assertIn('не определена',text)
            self.assertNotIn('соответствует ориентиру',text)
        self.assertTrue(models.model_hardware_hint('local:custom'))

    def test_all_new_variants_use_existing_streamed_local_download_path(self):
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def __iter__(self):return iter([json.dumps({'status':'success'}).encode()])
        for name in LARGE_MODELS:
            with self.subTest(name=name),patch('desktop.models.request',return_value=Response()) as request,\
                 patch('desktop.models.installed_models',return_value=[name]):
                models.pull_model('http://127.0.0.1:11434',name,lambda *args:None,threading.Event())
                self.assertEqual(request.call_args.args[2],{'model':name,'stream':True})

    def test_tuning_includes_selected_large_model_after_six_other_installations(self):
        names=[f'local:model-{n}' for n in range(8)]+[LARGE_MODELS[1]]
        candidates=tuning.tuning_candidates(names,LARGE_MODELS[1],'auto')
        self.assertEqual(len(candidates),6)
        self.assertEqual(candidates[0],{'model':LARGE_MODELS[1],'compute_mode':'auto'})
        self.assertEqual(len({item['model'].casefold() for item in candidates}),6)
        self.assertNotIn('remote:cloud',[c['model'] for c in tuning.tuning_candidates(
            ['remote:cloud',LARGE_MODELS[0],LARGE_MODELS[0].upper()],LARGE_MODELS[0],'auto')])
