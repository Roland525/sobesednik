import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PySide6.QtWidgets import QApplication,QLineEdit
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

    def test_response_model_and_profile_are_saved_separately_from_speech_speed(self):
        self.window.installed_model_names=['huihui_ai/qwen3-abliterated:4b','huihui_ai/qwen3-abliterated:8b']
        self.window.populate_models(self.window.installed_model_names,'huihui_ai/qwen3-abliterated:4b')
        self.window.response_choice.setCurrentIndex(self.window.response_choice.findData('fast'))
        self.window.speed.setValue(1.06)
        self.window.save_settings()
        cfg=self.window.profile.settings()
        self.assertEqual(cfg['ollama_model'],'huihui_ai/qwen3-abliterated:4b')
        self.assertEqual(cfg['response_profile'],'fast');self.assertEqual(cfg['tts_speed'],1.06)

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
