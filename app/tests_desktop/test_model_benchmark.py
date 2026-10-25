import json
import threading
import unittest
from unittest.mock import patch

from desktop.model_benchmark import CASES,generate,passes,run_benchmark,report_key,format_report


class Response:
    def __init__(self,items):self.items=items
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def __iter__(self):return iter(json.dumps(item).encode() for item in self.items)
    def read(self):return json.dumps({'models':[]}).encode()


class BenchmarkTests(unittest.TestCase):
    def test_rubric_does_not_accept_substring_or_explanation(self):
        self.assertTrue(passes(' «Синий». ','синий'))
        self.assertFalse(passes('Не синий','синий'))
        self.assertFalse(passes('42, потому что 17+25=42','42'))

    def test_stream_requires_done_and_reports_first_sentence(self):
        stream=[{'message':{'content':'Готово.'}},{'message':{'content':''},'done':True}]
        with patch('desktop.model_benchmark.request',return_value=Response(stream)),patch('desktop.model_benchmark.time.monotonic',side_effect=[0,1,2,3,4]):
            answer,first,total=generate('http://127.0.0.1:11434','local','prompt',{},threading.Event())
        self.assertEqual(answer,'Готово.');self.assertEqual(first,2);self.assertEqual(total,4)
        with patch('desktop.model_benchmark.request',return_value=Response(stream[:1])):
            with self.assertRaisesRegex(RuntimeError,'незавершённый'):generate('local','model','prompt',{},threading.Event())

    def test_cancelled_benchmark_never_calls_model(self):
        cancel=threading.Event();cancel.set()
        with patch('desktop.model_benchmark.request') as request:
            with self.assertRaises(InterruptedError):generate('local','model','prompt',{},cancel)
        request.assert_not_called()

    def test_suite_uses_only_fixed_prompts_and_reports_settings(self):
        prompts={prompt:expected for _,prompt,expected in CASES}
        def generated(host,model,prompt,options,cancel):
            self.assertIn(prompt,prompts);return prompts[prompt],1.5,2.0
        with patch('desktop.model_benchmark.installed_models',return_value=['local:4b']),\
             patch('desktop.model_benchmark.generate',side_effect=generated) as generate_call,\
             patch('desktop.model_benchmark.request',return_value=Response([])):
            report=run_benchmark({'ollama_model':'local:4b','resource_level':50},threading.Event())
        self.assertEqual(generate_call.call_count,8)
        self.assertEqual(report['passed'],8);self.assertEqual(report['total'],8)
        self.assertEqual(report['median_first_sentence_seconds'],1.5)
        self.assertEqual(report['resource_level'],50);self.assertFalse(report['think'])

    def test_v2_report_records_actual_compute_options_not_stale_resource_level(self):
        with patch('desktop.model_benchmark.installed_models',return_value=['local:4b']), \
             patch('desktop.model_benchmark.generate',return_value=('42',1,2)) as generate_call, \
             patch('desktop.model_benchmark.request',return_value=Response([])), \
             patch('bot.config.os.cpu_count',return_value=24):
            report=run_benchmark({'ollama_model':'local:4b','compute_mode':'cpu',
                                  'resource_level':100,'ollama_num_gpu':-1,
                                  'ollama_num_thread':30},threading.Event())
        self.assertEqual(report['schema'],2)
        self.assertEqual(report['suite'],'synthetic-exact-v2')
        self.assertEqual(report['compute_mode'],'cpu')
        self.assertEqual(report['options']['num_gpu'],0)
        self.assertEqual(report['options']['num_thread'],8)
        for call in generate_call.call_args_list:
            self.assertEqual(call.args[3],report['options'])
        self.assertEqual(report['resource_settings']['tts_cpu_threads'],4)
        self.assertIn('Только процессор',format_report(report))
        self.assertNotIn('/100',format_report(report))

    def test_v2_keys_separate_modes_and_reject_legacy_numeric_budget(self):
        self.assertNotEqual(report_key('local:4b','auto'),report_key('local:4b','cpu'))
        self.assertNotEqual(report_key('local:4b','auto'),report_key('other:4b','auto'))
        with self.assertRaises(ValueError):report_key('local:4b',70)

    def test_legacy_cpu_selection_is_migrated_before_benchmark(self):
        with patch('desktop.model_benchmark.installed_models',return_value=['local:4b']), \
             patch('desktop.model_benchmark.generate',return_value=('42',1,2)), \
             patch('desktop.model_benchmark.request',return_value=Response([])):
            report=run_benchmark({'ollama_model':'local:4b','resource_level':0},threading.Event())
        self.assertEqual(report['compute_mode'],'cpu')
        self.assertEqual(report['options']['num_gpu'],0)


if __name__=='__main__':unittest.main()
