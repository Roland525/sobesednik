import unittest
import json,tempfile,threading
from pathlib import Path
from unittest.mock import patch
from bot.tuning import choose_recommendation

class TuningTests(unittest.TestCase):
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
