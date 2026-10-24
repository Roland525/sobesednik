import tempfile,unittest
from pathlib import Path
from bot.feedback import FeedbackStore,matches_expected,feedback_gate

class FeedbackTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=FeedbackStore(Path(self.temp.name)/'learning.db',secrets=('private-key',))
        self.turn={'id':'a'*32,'user_id':2,'heard':'Серёга, сколько будет два плюс два?','answer':'Пять.','memories':[]}
    def tearDown(self):self.store.close();self.temp.cleanup()
    def test_original_source_is_retained_and_corrected_answer_becomes_case(self):
        row=self.store.add(1,self.turn,'answer','Четыре.')
        self.assertEqual(row['uid'],2);self.assertEqual(row['original_answer'],'Пять.')
        self.assertTrue(row['trainable']);self.assertEqual(len(self.store.cases(1)),1)
        self.assertEqual(self.store.cases(2),[])
    def test_question_correction_is_not_treated_as_a_correct_model_answer(self):
        self.store.add(1,self.turn,'question','Сколько будет три плюс три?')
        self.assertEqual(self.store.cases(1),[])
        row=self.store.add(1,self.turn,'answer','Шесть.')
        self.assertIn('три',row['question'])

    def test_later_question_correction_invalidates_answer_to_misrecognized_question(self):
        self.store.add(1,self.turn,'answer','Четыре.')
        record=self.store.add(1,self.turn,'question','Сколько будет три плюс три?')
        self.assertEqual(self.store.cases(1),[])
        self.assertEqual(record['invalidated']['answer'],'Четыре.')

    def test_replacement_answer_invalidates_previous_training_target(self):
        self.store.add(1,self.turn,'answer','Первое исправление')
        record=self.store.add(1,self.turn,'answer','Второе исправление')
        self.assertEqual(record['invalidated']['answer'],'Первое исправление')
        self.assertEqual(len(self.store.cases(1)),1)

    def test_third_party_personal_facts_cannot_become_weight_training_cases(self):
        row=self.store.add(1,{**self.turn,'heard':'Сколько лет Паше?'},'answer','Паше 32 года.')
        self.assertFalse(row['trainable']);self.assertEqual(self.store.cases(1),[])
    def test_empty_secret_and_memory_dependent_cases_cannot_train_weights(self):
        for value in ['', 'private-key']:
            with self.assertRaises(ValueError):self.store.add(1,self.turn,'answer',value)
        row=self.store.add(1,{**self.turn,'memories':[{'content':'personal'}]},'answer','Четыре.')
        self.assertFalse(row['trainable']);self.assertEqual(self.store.cases(1),[])
    def test_regressions_fail_closed_and_short_answers_require_exact_meaning_rule(self):
        self.assertTrue(matches_expected('4.','Четыре'))
        self.assertFalse(matches_expected('Не четыре.','Четыре'))
        self.assertTrue(feedback_gate([{'id':1,'pass':True}],[{'id':1,'pass':False}]))
        self.assertTrue(feedback_gate([{'id':1,'pass':True}],[]))
        self.assertEqual(feedback_gate([],[]),[])

    def test_forget_removes_feedback_cases_from_shared_learning_database(self):
        from bot.feedback import purge_feedback
        self.store.add(1,self.turn,'answer','Четыре.')
        with self.store.db:purge_feedback(self.store.db,1,2)
        self.assertEqual(self.store.cases(1),[])

    def test_feedback_only_database_can_be_browsed_and_cleared_with_capture_disabled(self):
        from desktop.storage import Profile
        root=Path(self.temp.name)/'profile';profile=Profile(root)
        store=FeedbackStore(root/'data/learning.sqlite3');store.add(1,self.turn,'answer','Четыре');store.close()
        self.assertEqual(profile.memory_rows('examples',1),[])
        profile.mutate_memory('learning_clear',{'guild':1})
        store=FeedbackStore(root/'data/learning.sqlite3')
        try:self.assertEqual(store.cases(1),[])
        finally:store.close()

class FeedbackDispatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_or_cross_server_turn_cannot_be_corrected(self):
        from types import SimpleNamespace as NS
        from unittest.mock import Mock
        from bot.desktop_control import DesktopControl
        with tempfile.TemporaryDirectory() as folder:
            guild=NS(id=1);journal=NS(snapshot=lambda:[])
            bot=NS(get_guild=lambda gid:guild,sessions={1:NS(journal=journal)})
            control=DesktopControl(bot,Path(folder))
            with self.assertRaises(ValueError):await control.dispatch('feedback_add',{'guild':1,'turn_id':'b'*32,'kind':'answer','correction':'Four'})
