import unittest
import asyncio
from dataclasses import asdict
import importlib.util
import json,tempfile,threading
from pathlib import Path
import types
from unittest.mock import patch
from bot.tuning import choose_recommendation

class TuningTests(unittest.TestCase):
    @staticmethod
    def worker():
        spec=importlib.util.spec_from_file_location('isolated_tune_runtime',Path(__file__).parents[1]/'tools/tune_runtime.py')
        worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)
        return worker

    def test_worker_applies_compute_mode_instead_of_legacy_budget(self):
        from bot.config import Config
        worker=self.worker();cfg=Config()
        with patch('bot.config.os.cpu_count',return_value=24):
            settings=worker.apply_candidate(cfg,{'model':'private/local:v1','compute_mode':'cpu',
                                                'resource_level':100,'ollama_num_gpu':-1})
        self.assertEqual(cfg.ollama_model,'private/local:v1')
        self.assertEqual(cfg.compute_mode,'cpu')
        self.assertEqual(settings['ollama_num_gpu'],0)
        self.assertEqual(cfg.ollama_num_thread,8)
        cfg.whisper_cpu_threads=12;cfg.tts_cpu_threads=12
        worker.apply_candidate(cfg,{'model':'private/local:v1','compute_mode':'auto'})
        self.assertEqual(cfg.ollama_num_thread,4)
        self.assertEqual(cfg.whisper_cpu_threads,4);self.assertEqual(cfg.tts_cpu_threads,4)
        with self.assertRaises(ValueError):worker.apply_candidate(cfg,{'model':'local','compute_mode':'invalid'})

    def test_tuning_formatter_names_actual_mode_without_percentage(self):
        from desktop.tuning import format_tuning
        text=format_tuning({'rows':[{'model':'private/local:v1','compute_mode':'cpu',
                                   'passed':8,'total':8,'first_pcm_ms':1500}]})
        self.assertIn('Только процессор',text)
        self.assertNotIn('/100',text)

    def test_worker_reloads_stt_and_tts_when_compute_profile_changes(self):
        from bot.config import Config
        from desktop.model_benchmark import CASES
        worker=self.worker();state={'stt_loaded':[],'tts_loaded':[],'prompt':'','resources':[]}
        answers={prompt:expected for _,prompt,expected in CASES}
        class STT:
            def __init__(self,logger,cfg):self.cfg=cfg
            def load(self):state['stt_loaded'].append(self.cfg.compute_mode)
            async def transcribe(self,audio,active):return state['prompt']
            def close(self):pass
        class TTS:
            def __init__(self,logger,cfg):self.cfg=cfg
            def load(self):state['tts_loaded'].append(self.cfg.compute_mode)
            async def generate_audio(self,text):state['prompt']=text;return b'\x01\x00'*24
            def close(self):pass
        class Client:
            async def post(self,*args,**kwargs):pass
        class LLM:
            def __init__(self,logger,cfg):
                self.client=Client();state['resources'].append((cfg.compute_mode,cfg.ollama_num_gpu,cfg.ollama_num_thread))
            async def warmup(self):pass
            async def stream_sentences(self,messages):yield answers[messages[-1]['content']]
            async def close(self):pass
        modules={'bot.services.audio_service':types.SimpleNamespace(AudioService=STT),
                 'bot.services.tts_service':types.SimpleNamespace(TTSService=TTS),
                 'bot.services.ollama_service':types.SimpleNamespace(OllamaService=LLM)}
        candidates=[{'model':'local:4b','compute_mode':'auto'},
                    {'model':'local:8b','compute_mode':'auto'},
                    {'model':'local:4b','compute_mode':'cpu'}]
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);path=root/'config.json'
            path.write_text(json.dumps(asdict(Config(compute_mode='cpu'))),encoding='utf-8')
            before=path.read_bytes();output=root/'report.json'
            with patch.dict('sys.modules',modules),patch.object(worker,'installed_models',return_value=['local:4b','local:8b']), \
                 patch('bot.config.os.cpu_count',return_value=24):
                asyncio.run(worker.measure(root,candidates,output))
            self.assertEqual(path.read_bytes(),before)
            report=json.loads(output.read_text(encoding='utf-8'))
        self.assertEqual(state['stt_loaded'],['auto','cpu'])
        self.assertEqual(state['tts_loaded'],['auto','cpu'])
        self.assertEqual(state['resources'],[('auto',-1,4),('auto',-1,4),('cpu',0,8)])
        self.assertEqual(report['schema'],2)
        self.assertEqual(report['suite'],'synthetic-pipeline-v2')
        self.assertTrue(report['complete'])
        for row in report['rows']:
            self.assertNotIn('error',row)
            self.assertEqual(row['passed'],8)
            self.assertEqual(row['applied_resources']['ollama_num_gpu'],0 if row['compute_mode']=='cpu' else -1)

    def test_quality_precedes_speed_and_failed_or_incomplete_runs_are_excluded(self):
        rows=[{'model':'fast-wrong','passed':6,'total':8,'first_pcm_ms':200},
              {'model':'good','passed':8,'total':8,'first_pcm_ms':900,'resource_level':70},
              {'model':'slower','passed':8,'total':8,'first_pcm_ms':1200},
              {'model':'broken','passed':8,'total':8,'first_pcm_ms':100,'error':'failed'},
              {'model':'incomplete','passed':4,'total':4,'first_pcm_ms':100}]
        self.assertEqual(choose_recommendation(rows)['model'],'good')
    def test_no_recommendation_when_every_model_is_inaccurate_or_invalid(self):
        self.assertIsNone(choose_recommendation([{'passed':6,'total':8,'first_pcm_ms':1},
            {'passed':8,'total':8,'first_pcm_ms':float('nan')}]))

    def test_cancel_and_success_never_modify_configuration(self):
        from desktop.tuning import run_tuning
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'config.json').write_text('{"ollama_model":"original"}')
            before=(root/'config.json').read_bytes()
            with patch('desktop.tuning.run_step',side_effect=InterruptedError):
                with self.assertRaises(InterruptedError):run_tuning(root,[],threading.Event(),lambda _:None)
            (root/'reports').mkdir();(root/'reports/hardware-tuning.json').write_text(json.dumps({'complete':True,'rows':[]}))
            with patch('desktop.tuning.run_step'):
                self.assertTrue(run_tuning(root,[],threading.Event(),lambda _:None)['complete'])
            self.assertEqual(before,(root/'config.json').read_bytes())

    def test_cancel_also_unloads_candidate_from_external_ollama_server(self):
        from desktop.tuning import run_tuning
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            with patch('desktop.tuning.run_step',side_effect=InterruptedError),patch('desktop.tuning.release_candidates') as release:
                with self.assertRaises(InterruptedError):run_tuning(root,[{'model':'local'}],threading.Event(),lambda _:None)
                release.assert_called_once()
