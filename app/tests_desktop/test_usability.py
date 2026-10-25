import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
from PySide6.QtWidgets import QApplication,QSlider
from PySide6.QtTest import QTest


class UsabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def setUp(self):
        from desktop.app import MainWindow
        self.temp=tempfile.TemporaryDirectory();self.window=MainWindow(Path(self.temp.name),start_timers=False)
        self.window.show();self.wait_jobs()
    def wait_jobs(self):
        deadline=time.monotonic()+4
        while self.window.jobs and time.monotonic()<deadline:self.app.processEvents();QTest.qWait(10)
        self.app.processEvents()
    def tearDown(self):
        self.wait_jobs();self.window.force_close=True;self.window.close();self.window.deleteLater()
        self.app.processEvents();self.temp.cleanup()

    def test_notification_expires_and_can_be_closed_without_hiding_next_message(self):
        banner=self.window.notice_banner
        banner.show_message('Первое сообщение',timeout_ms=20)
        banner.show_message('Новое сообщение',timeout_ms=1000)
        QTest.qWait(80);self.assertTrue(banner.isVisible())
        self.assertEqual(banner.message.text(),'Новое сообщение')
        banner.close_button.click();QTest.qWait(250);self.assertFalse(banner.isVisible())
        banner.show_message('Готово',timeout_ms=20);QTest.qWait(300)
        self.assertFalse(banner.isVisible())

    def test_restart_notification_finishes_only_after_voice_is_ready(self):
        state={}
        with patch.object(self.window.protocol,'status',side_effect=lambda:dict(state)), \
             patch.object(self.window.runtime,'running',return_value=True), \
             patch.object(self.window.runtime,'stop'),patch.object(self.window.runtime,'start'):
            self.window.restart_bot();self.wait_jobs()
            self.assertTrue(self.window.awaiting_ready)
            state.update(ready=True,services_ready=False,services_stage='Готовим голос')
            self.window.refresh();self.assertTrue(self.window.awaiting_ready)
            state['services_ready']=True;self.window.refresh()
            self.assertFalse(self.window.awaiting_ready)
            self.assertIn('готов',self.window.notice_label.text().lower())

    def test_preset_is_a_preview_and_custom_draft_can_be_restored(self):
        self.window.profile.save_persona('Сохранённый характер')
        self.window.reload_forms();self.window.persona_edit.setPlainText('Мой несохранённый текст')
        index=self.window.persona_preset.findData('aggressive')
        self.assertGreaterEqual(index,0);self.window.persona_preset.setCurrentIndex(index)
        self.assertNotEqual(self.window.persona_edit.toPlainText(),'Мой несохранённый текст')
        self.assertEqual(self.window.profile.persona(),'Сохранённый характер')
        self.window.persona_preset.setCurrentIndex(self.window.persona_preset.findData('custom'))
        self.assertEqual(self.window.persona_edit.toPlainText(),'Мой несохранённый текст')
        self.window.persona_preset.setCurrentIndex(index);self.window.save_persona()
        self.assertEqual(self.window.profile.persona(),self.window.persona_edit.toPlainText())

    def test_compute_mode_replaces_percentage_slider_and_preserves_voice(self):
        self.assertFalse(self.window.findChildren(QSlider))
        self.window.compute_choice.setCurrentIndex(self.window.compute_choice.findData('cpu'))
        self.window.speed.setValue(1.12);self.window.save_settings()
        cfg=self.window.profile.settings()
        self.assertEqual(cfg['compute_mode'],'cpu');self.assertEqual(cfg['ollama_num_gpu'],0)
        self.assertEqual(cfg['tts_speed'],1.12)

    def test_benchmark_has_start_instructions_and_real_progress_without_fake_result(self):
        self.assertIn('запуст',self.window.benchmark_result.toPlainText().lower())
        self.window.install_update(0,'Проверка модели','3/8 · synthetic · arithmetic')
        self.assertEqual(self.window.benchmark_progress.maximum(),8)
        self.assertEqual(self.window.benchmark_progress.value(),3)

    def test_restart_does_not_claim_old_gateway_ready_while_stopping(self):
        import threading
        release=threading.Event();state={'ready':True,'services_ready':True,'guilds':[]}
        def stop():release.wait(2);state.clear()
        with patch.object(self.window.protocol,'status',side_effect=lambda:dict(state)), \
             patch.object(self.window.runtime,'running',return_value=True), \
             patch.object(self.window.runtime,'stop',side_effect=stop),patch.object(self.window.runtime,'start'):
            try:
                self.window.restart_bot();QTest.qWait(50)
                self.assertNotIn('готов к разговору',self.window.notice_label.text().lower())
            finally:release.set();self.wait_jobs()

    def test_selected_model_check_runs_and_persists_real_report_in_test_profile(self):
        import io,json
        name='huihui_ai/qwen3-abliterated:8b';answers=iter(['42','синий','кошка','готово']*2)
        self.window.accept_model_inventory([{'name':name,'size':5*1024**3}],name)
        before=(self.window.profile.root/'config.json').read_bytes()
        def request(host,endpoint,payload=None,timeout=15):
            value={'models':[{'name':name,'size':5*1024**3}]} if endpoint=='/api/tags' else \
                {'models':[]} if endpoint=='/api/ps' else {'message':{'content':next(answers)},'done':True}
            return io.BytesIO((json.dumps(value)+'\n').encode())
        with patch('desktop.model_benchmark.request',side_effect=request), \
             patch('desktop.models.request',side_effect=request), \
             patch.object(self.window.runtime,'ensure_ollama'),patch.object(self.window.runtime,'running',return_value=False):
            self.window.benchmark_model();self.wait_jobs()
        path=self.window.profile.root/'reports/model-benchmark.json'
        report=json.loads(path.read_text())
        self.assertEqual((report['passed'],report['total']),(8,8))
        self.assertEqual(report['compute_mode'],'auto')
        self.assertIn('8/8',self.window.benchmark_result.toPlainText())
        self.assertEqual((self.window.profile.root/'config.json').read_bytes(),before)

    def test_tuning_shows_work_and_requires_explicit_apply(self):
        import threading
        release=threading.Event();name='huihui_ai/qwen3-abliterated:4b'
        self.window.installed_model_names=[name]
        before=self.window.profile.settings()['ollama_model']
        report={'complete':True,'rows':[],'recommendation':{'model':name,'compute_mode':'auto'}}
        def compare(*args):release.wait(3);return report
        with patch('desktop.tuning.run_tuning',side_effect=compare), \
             patch.object(self.window.runtime,'running',return_value=False),patch.object(self.window.runtime,'ensure_ollama'):
            try:
                self.window.start_tuning();self.app.processEvents()
                self.assertTrue(self.window.tuning_running)
                self.assertFalse(self.window.tune_button.isEnabled())
                self.assertTrue(self.window.tune_cancel.isEnabled())
            finally:release.set();self.wait_jobs()
        self.assertFalse(self.window.tuning_running)
        self.assertTrue(self.window.tune_apply.isEnabled())
        self.assertEqual(self.window.profile.settings()['ollama_model'],before)

    def test_old_auto_fragment_is_marked_for_review_without_deleting_it(self):
        from bot.memory import MemoryStore
        memory=MemoryStore(self.window.profile.root/'data/memory.sqlite3')
        memory.save(1,10,'auto:quote:old','Ну да конечно',target=10);memory.close()
        self.window.memory_data=self.window.profile.memory_rows('notes',1)
        self.window.fill_memory('notes')
        self.assertIn('Проверьте',self.window.memory_table.item(0,4).text())
        self.assertEqual(len(self.window.profile.memory_rows('notes',1)),1)
