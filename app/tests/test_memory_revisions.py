import json,tempfile,unittest
from pathlib import Path
from bot.memory import MemoryStore

class MemoryRevisionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=MemoryStore(Path(self.temp.name)/'memory.db')
    def tearDown(self):self.store.close();self.temp.cleanup()
    def test_replaced_occupation_keeps_attributed_history_but_recall_uses_current(self):
        self.store.observe_auto(1,2,'Tester','Я работаю курьером')
        self.store.observe_auto(1,2,'Tester','Теперь я работаю дизайнером')
        row=self.store.db.execute('SELECT rowid,* FROM notes').fetchone()
        history=self.store.history(1,row['rowid'])
        self.assertEqual(len(history),2);self.assertIn('курьером',history[0]['content'])
        self.assertIn('дизайнером',history[1]['content']);self.assertEqual(history[0]['source'],2)
        self.assertTrue(history[0]['replaced']);self.assertIsNone(history[1]['replaced'])
        check=self.store.recall_check(1,2,'Кем я работаю?')
        self.assertIn('дизайнером',str(check));self.assertNotIn('курьером',str(check))
    def test_forget_and_direct_sql_deletion_also_remove_revision_history(self):
        for value in ['синий','красный']:self.store.observe_auto(1,2,'Tester','Мой любимый цвет '+value)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM note_history').fetchone()[0],1)
        self.store.handle(1,2,'Tester','Забудь всё обо мне')
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM note_history').fetchone()[0],0)
    def test_repeated_same_fact_does_not_create_history(self):
        for _ in range(3):self.store.observe_auto(1,2,'Tester','Я работаю дизайнером')
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM note_history').fetchone()[0],0)

    def test_voice_forget_clears_feedback_even_when_capture_service_is_disabled(self):
        import logging,time
        from bot.feedback import FeedbackStore
        from bot.session import GuildSession,Question
        from bot.config import Config
        corpus=FeedbackStore(Path(self.temp.name)/'learning.sqlite3')
        turn={'id':'a'*32,'user_id':2,'heard':'Сколько два плюс два?','answer':'Пять','memories':[]}
        corpus.add(1,turn,'answer','Четыре');corpus.close()
        session=GuildSession(1,Config(learning_capture_enabled=False),None,None,None,logging.getLogger(),logging.getLogger(),self.store)
        self.assertIn('Удалил',session.memory_turn(Question(2,'Tester','Забудь всё обо мне',time.monotonic()))[0])
        corpus=FeedbackStore(Path(self.temp.name)/'learning.sqlite3')
        try:self.assertEqual(corpus.cases(1),[])
        finally:corpus.close()
