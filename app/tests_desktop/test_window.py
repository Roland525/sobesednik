import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PySide6.QtWidgets import QApplication,QLineEdit,QScrollArea,QSlider
from PySide6.QtTest import QTest
from PySide6.QtCore import Qt,QLockFile


class WindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        from desktop.app import MainWindow
        self.temp=tempfile.TemporaryDirectory()
        self.window=MainWindow(Path(self.temp.name),start_timers=False)
        self.window.show(); self.app.processEvents()

    def tearDown(self):
        deadline=__import__('time').monotonic()+5
        while self.window.jobs and __import__('time').monotonic()<deadline:
            self.app.processEvents(); QTest.qWait(10)
        self.window.force_close=True; self.window.close(); self.window.deleteLater()
        self.app.processEvents(); self.temp.cleanup()

    def test_navigation_is_real_and_token_is_masked(self):
        for index in range(6):
            self.window.navigation.setCurrentRow(index); self.app.processEvents()
            self.assertEqual(self.window.pages.currentIndex(),index)
        self.assertEqual(self.window.token_input.echoMode(),QLineEdit.Password)

    def test_diagnostics_show_heard_routing_and_attributed_evidence(self):
        self.window.guild_box.addItem('Test','1');self.window.guild_box.setCurrentIndex(1)
        self.window.state={'sessions':{str(self.window.guild_id()):{'turns':[{'id':'a','at':1,'name':'Tester',
            'heard':'Synthetic question','reason':'named','status':'answered','answer':'Synthetic answer',
            'memories':[{'content':'Synthetic fact','source':'Tester'}]}]}}}
        self.window.refresh_diagnostics()
        self.assertEqual(self.window.turn_table.rowCount(),1)
        self.assertIn('Synthetic question',self.window.turn_detail.toPlainText())
        self.assertIn('Обращение по имени',self.window.turn_detail.toPlainText())
        self.assertIn('Synthetic fact — Tester',self.window.turn_detail.toPlainText())

    def test_settings_and_persona_save_to_selected_profile(self):
        self.window.speed.setValue(1.12)
        self.window.save_settings()
        self.assertAlmostEqual(self.window.profile.settings()['tts_speed'],1.12)
        self.window.persona_edit.setPlainText('A test persona')
        self.window.save_persona()
        self.assertEqual(self.window.profile.persona(),'A test persona')

    def test_context_roundtrip_and_empty_memory(self):
        from desktop.app import context_text,parse_context
        messages=[{'role':'user','content':'Question\nmore details'},
                  {'role':'assistant','content':'Answer'}]
        self.assertEqual(parse_context(context_text(messages)),messages)
        self.assertEqual(self.window.memory_table.rowCount(),0)

    def test_offline_status_does_not_claim_running_or_invent_load(self):
        self.window.refresh()
        self.assertIn('Остановлен',self.window.status_label.text())
        self.assertFalse(self.window.join_button.isEnabled())

    def test_switching_memory_kind_while_loading_cannot_show_old_record(self):
        import time
        note={'record_id':1,'guild':1,'target_name':'Person','content':'old note',
              'author_name':'Author','updated':1,'kind':'fact'}
        example={'record_id':1,'guild':1,'prompt':'example prompt','target':'answer','author':2,'created':1,'kind':'correction'}
        def rows(kind,guild):
            time.sleep(.15)
            return [note] if kind=='notes' else [example]
        with patch.object(self.window.profile,'memory_rows',side_effect=rows):
            self.window.refresh_memory()
            self.window.memory_kind.setCurrentIndex(2)
            deadline=time.monotonic()+3
            while time.monotonic()<deadline:
                self.app.processEvents(); QTest.qWait(20)
                if not self.window.memory_pending: break
        self.assertIn('example prompt',self.window.memory_table.item(0,1).text())

    def test_switching_profile_moves_single_instance_lock(self):
        import time
        deadline=time.monotonic()+3
        while self.window.jobs and time.monotonic()<deadline:
            self.app.processEvents(); QTest.qWait(10)
        old=self.window.profile.root; target=old/'different-profile'
        self.window.switch_profile(target)
        destination=QLockFile(str(target/'.desktop-window.lock'))
        self.assertFalse(destination.tryLock(1))
        original=QLockFile(str(old/'.desktop-window.lock'))
        self.assertTrue(original.tryLock(100)); original.unlock()

    def test_unchecking_automatic_channel_clears_saved_channel(self):
        self.window.profile.save_settings({'auto_join_channel_id':123})
        self.window.reload_forms()
        self.window.remember_channel.setChecked(False)
        self.assertEqual(self.window.profile.settings()['auto_join_channel_id'],0)

    def test_resource_slider_and_model_are_saved_separately_from_speech_speed(self):
        self.window.installed_model_names=['huihui_ai/qwen3-abliterated:4b','huihui_ai/qwen3-abliterated:8b']
        self.window.populate_models(self.window.installed_model_names,'huihui_ai/qwen3-abliterated:4b')
        self.assertIsInstance(self.window.resource_slider,QSlider)
        self.window.resource_slider.setValue(40)
        self.window.speed.setValue(1.06)
        self.window.save_settings()
        cfg=self.window.profile.settings()
        self.assertEqual(cfg['ollama_model'],'huihui_ai/qwen3-abliterated:4b')
        self.assertEqual(cfg['response_profile'],'custom');self.assertEqual(cfg['tts_speed'],1.06)
        self.assertEqual(cfg['resource_level'],40)

    def test_installed_download_is_disabled_and_guarded(self):
        name=self.window.model_download.itemData(0)
        self.window.installed_model_names=[name.upper()]
        self.window.model_download.setCurrentIndex(0)
        self.window.refresh()
        self.assertFalse(self.window.model_download_button.isEnabled())
        self.assertIn('Уже установлена',self.window.model_download_button.text())
        with patch.object(self.window,'run_job') as run:
            self.window.download_model()
        run.assert_not_called()
        self.window.model_download.setCurrentIndex(1)
        self.assertTrue(self.window.model_download_button.isEnabled())

    def test_model_names_are_readable_and_technical_tag_is_in_tooltip(self):
        name='huihui_ai/qwen3-abliterated:8b'
        self.window.accept_model_inventory([{'name':name,'size':5*1024**3,
            'details':{'parameter_size':'8.2B','quantization_level':'Q4_K_M'}}],name)
        self.assertIn('Qwen 3 · 8B',self.window.model_choice.currentText())
        self.assertIn('5.00 ГиБ',self.window.model_choice.currentText())
        self.assertNotIn('huihui_ai',self.window.model_choice.currentText())
        self.assertEqual(self.window.model_choice.toolTip(),name)
        self.assertIn('8.2B',self.window.model_detail.text())
        self.assertIn('Q4_K_M',self.window.model_detail.text())
        self.assertNotIn('huihui_ai',self.window.model_download.itemText(0))

    def test_saved_benchmark_is_shown_only_for_matching_model_and_resources(self):
        import json
        name='huihui_ai/qwen3-abliterated:8b'
        self.window.populate_models([name,'huihui_ai/qwen3-abliterated:4b'],name)
        self.window.resource_slider.setValue(70)
        reports=self.window.profile.root/'reports';reports.mkdir(exist_ok=True)
        (reports/'model-benchmark.json').write_text(json.dumps({'model':name,'resource_level':70,
            'suite':'synthetic-exact-v1','created_utc':'2026-10-24T12:00:00Z'}),encoding='utf-8')
        with patch('desktop.model_benchmark.format_report',return_value='8/8 verified'):
            self.window.load_saved_benchmark()
            self.assertIn('8/8 verified',self.window.benchmark_result.toPlainText())
            self.window.resource_slider.setValue(40)
            self.assertNotIn('8/8 verified',self.window.benchmark_result.toPlainText())
            self.window.resource_slider.setValue(70)
            self.assertIn('8/8 verified',self.window.benchmark_result.toPlainText())
            self.window.model_choice.setCurrentIndex(self.window.model_choice.findData('huihui_ai/qwen3-abliterated:4b'))
            self.assertNotIn('8/8 verified',self.window.benchmark_result.toPlainText())

    def test_settings_toolbar_remains_visible_at_bottom_of_scroll(self):
        self.window.navigation.setCurrentRow(2);self.app.processEvents()
        scroll=self.window.pages.widget(2).findChild(QScrollArea)
        scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
        self.app.processEvents()
        self.assertTrue(self.window.settings_save_button.isVisible())
        self.assertFalse(scroll.isAncestorOf(self.window.settings_save_button))
        self.assertTrue(self.window.settings_restart_button.isVisible())

    def test_benchmark_cannot_start_while_bot_runs(self):
        with patch.object(self.window.runtime,'running',return_value=True),patch.object(self.window,'run_job') as run:
            self.window.benchmark_model()
        run.assert_not_called()

    def test_memory_preferences_are_saved_and_capture_disables_training(self):
        self.window.listen_conversation.setChecked(False)
        self.window.memory_auto_enabled.setChecked(False)
        self.window.learning_capture_enabled.setChecked(True)
        self.window.learning_auto_enabled.setChecked(True)
        self.window.learning_capture_enabled.setChecked(False)
        self.assertFalse(self.window.learning_auto_enabled.isChecked())
        self.assertFalse(self.window.learning_auto_enabled.isEnabled())
        self.window.save_memory_settings()
        cfg=self.window.profile.settings()
        self.assertFalse(cfg['listen_conversation']);self.assertFalse(cfg['memory_auto_enabled'])
        self.assertFalse(cfg['learning_capture_enabled']);self.assertFalse(cfg['learning_auto_enabled'])

    def test_enabled_training_does_not_claim_weights_were_trained(self):
        self.window.profile.save_settings({'learning_capture_enabled':True,'learning_auto_enabled':True})
        self.window.refresh_learning_status()
        self.assertIn('Подтверждённого результата',self.window.learning_label.text())

    def test_training_status_explains_qwen3_8b_compatibility(self):
        self.window.profile.save_settings({'ollama_model':'huihui_ai/qwen3.5-abliterated:4B'})
        self.window.refresh_learning_status()
        self.assertIn('только Qwen 3 · 8B',self.window.learning_label.text())
        self.assertIn('обучение весов недоступно',self.window.learning_label.text())

    def test_memory_auto_refresh_only_runs_on_visible_idle_page(self):
        self.window.memory_pending=False
        with patch.object(self.window,'refresh_memory') as refresh:
            self.window.auto_refresh_memory();refresh.assert_not_called()
            self.window.pages.setCurrentIndex(4)
            self.window.auto_refresh_memory();refresh.assert_called_once_with(automatic=True)
            self.window.busy=True;self.window.auto_refresh_memory();self.assertEqual(refresh.call_count,1)
            self.window.busy=False;self.window.memory_pending=True
            self.window.auto_refresh_memory();self.assertEqual(refresh.call_count,1)
        self.window.memory_pending=False

    def test_memory_auto_refresh_keeps_selected_record_when_new_notes_arrive(self):
        deadline=__import__('time').monotonic()+3
        while self.window.memory_pending and __import__('time').monotonic()<deadline:
            self.app.processEvents();QTest.qWait(10)
        def note(identity):
            return {'record_id':identity,'guild':1,'target_name':'Person','content':f'note {identity}',
                    'author_name':'Author','updated':1,'kind':'fact'}
        self.window.memory_data=[note(1)]
        self.window.loaded_memory_key=(self.window.memory_kind.currentData(),self.window.guild_id())
        self.window.fill_memory();self.window.memory_table.selectRow(0)
        with patch.object(self.window.profile,'memory_rows',return_value=[note(2),note(1)]):
            self.window.refresh_memory(automatic=True)
            deadline=__import__('time').monotonic()+3
            while self.window.memory_pending and __import__('time').monotonic()<deadline:
                self.app.processEvents();QTest.qWait(10)
        self.assertEqual(self.window.memory_table.rowCount(),2)
        self.assertEqual(self.window.memory_table.currentRow(),1)
        self.assertIn('note 1',self.window.memory_table.item(1,1).text())

    def test_load_context_reads_fresh_state_and_keeps_unsaved_text_when_offline(self):
        self.window.guild_box.addItem('Test','1');self.window.guild_box.setCurrentIndex(self.window.guild_box.findData('1'))
        self.window.state={'sessions':{'1':{'context':[{'role':'user','content':'stale'}]}}}
        with patch.object(self.window.protocol,'status',return_value={'sessions':{'1':{'context':[{'role':'user','content':'fresh'}]}}}):
            self.window.load_context()
        self.assertIn('fresh',self.window.context_edit.toPlainText())
        with patch.object(self.window.protocol,'status',return_value={}):self.window.load_context()
        self.assertIn('fresh',self.window.context_edit.toPlainText())

    def test_persona_save_is_durable_and_independent_of_context(self):
        self.window.persona_edit.setPlainText('Общайся кратко и по делу.')
        self.window.save_persona();self.window.context_edit.setPlainText('Человек: Другая тема')
        from desktop.storage import Profile
        self.assertIn('кратко',Profile(self.window.profile.root).persona())

    def test_available_update_shows_banner_without_touching_bot_or_settings(self):
        from desktop.updates import UpdateInfo
        info=UpdateInfo('99.0.0','https://github.com/Roland525/sobesednik/releases/download/v99.0.0/Sobesednik-Setup-99.0.0.exe',8,'a'*64,'')
        before=self.window.profile.settings()
        with patch.object(self.window.runtime,'close') as close:
            self.window.offer_update(info)
        self.assertFalse(self.window.update_banner.isHidden())
        self.assertIn('99.0.0',self.window.update_text.text())
        self.assertIn('Скачать',self.window.update_button.text())
        close.assert_not_called();self.assertEqual(before,self.window.profile.settings())

    def test_manual_update_check_reports_current_version_without_installing(self):
        from desktop.version import VERSION
        with patch('desktop.updates.check_update',return_value=None):
            self.window.check_for_updates(manual=True)
            deadline=__import__('time').monotonic()+3
            while self.window.jobs and __import__('time').monotonic()<deadline:
                self.app.processEvents();QTest.qWait(10)
        self.assertIn(VERSION,self.window.notice_label.text())
        self.assertTrue(self.window.update_banner.isHidden())

    def test_update_download_precedes_bot_shutdown_and_handoff(self):
        from desktop.updates import UpdateInfo
        info=UpdateInfo('99.0.0','',8,'a'*64,'');events=[]
        self.window.offer_update(info)
        with patch('desktop.updates.download_update',side_effect=lambda *a:(events.append('download') or Path(self.temp.name)/'update.exe')), \
             patch.object(self.window.runtime,'close',side_effect=lambda:events.append('stop')), \
             patch.object(self.window,'queue_update_handoff',side_effect=lambda *a:events.append('handoff')):
            self.window.apply_update()
            deadline=__import__('time').monotonic()+3
            while self.window.jobs and __import__('time').monotonic()<deadline:
                self.app.processEvents();QTest.qWait(10)
        self.assertEqual(events,['download','stop','handoff'])
