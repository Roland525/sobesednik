import json
from pathlib import Path
import tempfile
import unittest

from bot.memory import MemoryStore


class MemoryReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name) / 'memory.sqlite3'
        self.memory = MemoryStore(self.path)
        self.memory.register(1, 10, 'Owner')

    def tearDown(self):
        self.memory.close()
        self.folder.cleanup()

    def say(self, text, uid=10, nick='Owner'):
        return self.memory.handle(1, uid, nick, text)

    def test_conversational_memory_requests_and_confirmation(self):
        for text in ('Можешь запомнить, что я люблю шахматы?', 'Запомни, пожалуйста, что я люблю шахматы',
                     'Сохрани в памяти: я люблю шахматы', 'Я люблю шахматы, запомни это'):
            with self.subTest(text=text):
                result = self.say(text)
                self.assertIn('шахматы', result.reply)
                notes = json.loads(self.memory.context(1, 10, 'Какие у меня увлечения?'))['notes']
                self.assertTrue(any('шахматы' in row['content'] for row in notes))
        self.assertEqual(self.memory.db.execute('SELECT COUNT(*) FROM notes').fetchone()[0], 1)

    def test_question_or_negation_does_not_create_a_memory(self):
        for text in ('Что значит запомни?', 'Не запоминай это', 'Он сказал: запомни, что я люблю шахматы',
                     'Ты можешь запоминать разговоры?'):
            self.say(text)
        self.assertEqual(self.memory.db.execute('SELECT COUNT(*) FROM notes').fetchone()[0], 0)

    def test_multiple_explicit_facts_populate_name_and_preferences(self):
        self.say('Запомни, пожалуйста: меня зовут Паша, мне 25 лет, моя любимая игра это Dota 2')
        self.assertIn('Паша', self.say('Как меня зовут?').reply)
        self.assertIn('Dota 2', self.say('Какая моя любимая игра?').reply)
        self.assertIn('25', self.memory.context(1, 10, 'Сколько мне лет?'))

    def test_correction_replaces_same_personal_fact_instead_of_conflicting_notes(self):
        self.say('Запомни: мой любимый цвет синий')
        self.say('Запомни, мой любимый цвет теперь зелёный')
        self.say('Моя любимая игра это Dota 2')
        self.say('Запомни, теперь моя любимая игра — Disco Elysium')
        context = self.memory.context(1, 10, 'Какой мой любимый цвет и игра?')
        self.assertIn('зелёный', context)
        self.assertNotIn('синий', context)
        self.assertIn('Disco Elysium', context)
        self.assertNotIn('Dota 2', context)

    def test_old_relevant_fact_wins_over_many_recent_unrelated_notes_after_restart(self):
        self.say('Запомни: я играю в шахматы по субботам')
        for i in range(30):
            self.say(f'Запомни: номер моей игровой сессии {i}')
        self.memory.close()
        self.memory = MemoryStore(self.path)
        data = json.loads(self.memory.context(1, 10, 'Когда я играю в шахматы?'))
        self.assertIn('субботам', data['notes'][0]['content'])
        self.assertLessEqual(len(json.dumps(data, ensure_ascii=False)), 1800)

    def test_pending_memory_accepts_next_statement_only_from_requester(self):
        self.assertIn('именно', self.say('Можешь запомнить это?').reply)
        self.assertTrue(self.memory.can_resolve_pending(1, 10, 'Я люблю шахматы'))
        self.assertFalse(self.memory.can_resolve_pending(1, 20, 'Я люблю шахматы'))
        self.say('Я люблю шахматы', uid=20, nick='Other')
        self.assertEqual(self.memory.db.execute('SELECT COUNT(*) FROM notes').fetchone()[0], 0)
        self.say('Я люблю шахматы')
        self.assertIn('шахматы', self.memory.context(1, 10, 'Что я люблю?'))

    def test_long_record_is_not_silently_truncated_and_reported_as_saved(self):
        result = self.say('Запомни: ' + 'очень важная деталь ' * 35)
        self.assertIn('длин', result.reply)
        self.assertEqual(self.memory.db.execute('SELECT COUNT(*) FROM notes').fetchone()[0], 0)

    def test_unresolved_third_party_fact_is_retrievable_and_binds_later(self):
        self.say('Запомни: Паша любит шахматы')
        self.assertIn('шахматы', self.memory.context(1, 30, 'Во что любит играть Паша?'))
        self.say('Меня зовут Паша', uid=20, nick='PinkGuy')
        data = json.loads(self.memory.context(1, 20, 'Что я люблю?'))
        note = next(row for row in data['notes'] if 'шахматы' in row['content'])
        self.assertEqual(note['target_id'], 20)
        self.assertEqual(note['source_id'], 10)

    def test_explicit_first_person_fact_keeps_author_as_target_when_mentioning_another_member(self):
        self.memory.register(1, 20, 'Bob')
        for body in ('я в одной команде с Bob', 'мне помогает Bob', 'меня тренирует Bob',
                     'мой напарник Bob', 'моя команда играет с Bob', 'у меня команда с Bob'):
            with self.subTest(body=body):
                self.assertIn('Запомнил', self.say('Запомни: ' + body).reply)
                note = self.memory.db.execute('SELECT source,target FROM notes WHERE content=?', (body,)).fetchone()
                self.assertEqual(note['source'], 10)
                self.assertEqual(note['target'], 10)

    def test_forgetting_mentioned_member_keeps_authors_first_person_fact_after_restart(self):
        self.memory.register(1, 20, 'Bob')
        self.say('Запомни: я в одной команде с Bob')
        self.memory.close()
        self.memory = MemoryStore(self.path)
        self.say('Забудь всё обо мне', uid=20, nick='Bob')
        note = self.memory.db.execute('SELECT source,target FROM notes WHERE source=10').fetchone()
        self.assertIsNotNone(note)
        self.assertEqual(note['target'], 10)

    def test_explicit_and_auto_occupation_share_current_fact_and_revision_history(self):
        for explicit_first in (True, False):
            with self.subTest(explicit_first=explicit_first):
                uid = 30 if explicit_first else 40
                if explicit_first:
                    self.say('Запомни: я работаю курьером', uid=uid)
                    self.memory.observe_auto(1, uid, 'Owner', 'Теперь я работаю дизайнером')
                else:
                    self.memory.observe_auto(1, uid, 'Owner', 'Я работаю курьером')
                    self.say('Запомни: теперь я работаю дизайнером', uid=uid)
                rows = self.memory.db.execute('SELECT rowid,* FROM notes WHERE source=?', (uid,)).fetchall()
                self.assertEqual(len(rows), 1)
                history = self.memory.history(1, rows[0]['rowid'])
                self.assertEqual(len(history), 2)
                self.assertIn('курьером', history[0]['content'])
                self.assertIn('дизайнером', history[1]['content'])
                self.assertNotIn('курьером', self.memory.context(1, uid, 'Кем я работаю?'))

    def test_legacy_explicit_occupation_moves_to_history_when_auto_fact_changes(self):
        self.memory.save(1, 10, 'note:legacy', 'я работаю курьером', target=10)
        self.memory.observe_auto(1, 10, 'Owner', 'Теперь я работаю дизайнером')
        rows = self.memory.db.execute('SELECT rowid,* FROM notes WHERE source=10').fetchall()
        self.assertEqual(len(rows), 1)
        history = self.memory.history(1, rows[0]['rowid'])
        self.assertEqual(len(history), 2)
        self.assertIn('курьером', history[0]['content'])
        self.assertIn('дизайнером', history[1]['content'])

    def test_repeated_current_occupation_also_archives_stale_legacy_explicit_fact(self):
        self.memory.save(1, 10, 'note:legacy', 'я работаю курьером', target=10)
        self.memory.save(1, 10, 'auto:occupation', 'я работаю дизайнером', target=10)
        # Recreate the pre-upgrade state with both kinds of current record.
        self.memory.save(1, 10, 'note:legacy', 'я работаю курьером', target=10)
        self.memory.observe_auto(1, 10, 'Owner', 'я работаю дизайнером')
        rows = self.memory.db.execute('SELECT rowid,* FROM notes WHERE source=10').fetchall()
        self.assertEqual(len(rows), 1)
        self.assertIn('курьером', str(self.memory.history(1, rows[0]['rowid'])))

    def test_third_party_occupation_does_not_replace_speakers_occupation(self):
        self.memory.register(1, 20, 'Bob')
        self.say('Запомни: я работаю курьером')
        self.say('Запомни: Bob работает дизайнером')
        rows = self.memory.db.execute('SELECT target,content FROM notes ORDER BY updated').fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual([row['target'] for row in rows], [10, 20])

    def test_transient_work_statement_does_not_replace_durable_occupation(self):
        self.memory.observe_auto(1, 10, 'Owner', 'Я работаю дизайнером')
        result = self.say('Я работаю сегодня до шести')
        self.assertEqual(result.reply, '')
        rows = self.memory.db.execute('SELECT slot,content FROM notes WHERE source=10').fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['content'], 'Я работаю дизайнером')

    def test_explicit_transient_work_statement_is_a_note_without_replacing_occupation(self):
        self.memory.observe_auto(1, 10, 'Owner', 'Я работаю дизайнером')
        self.assertIn('Запомнил', self.say('Запомни: я работаю сегодня до шести').reply)
        rows = self.memory.db.execute('SELECT slot,content FROM notes WHERE source=10 ORDER BY updated').fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['slot'], 'auto:occupation')
        self.assertEqual(rows[0]['content'], 'Я работаю дизайнером')
        self.assertTrue(rows[1]['slot'].startswith('note:'))
        self.assertEqual(rows[1]['content'], 'я работаю сегодня до шести')

    def test_pending_capture_can_be_cancelled_and_never_saves_a_new_question(self):
        self.say('Запомни это')
        self.say('Не запоминай это')
        self.say('Я люблю шахматы')
        self.assertEqual(self.memory.db.execute('SELECT COUNT(*) FROM notes').fetchone()[0], 0)
        self.say('Запомни это')
        self.say('Почему небо голубое?')
        self.say('Я люблю шахматы')
        self.assertEqual(self.memory.db.execute('SELECT COUNT(*) FROM notes').fetchone()[0], 0)

    def test_third_person_statement_is_not_a_self_fact_and_optout_does_not_block_fact_binding(self):
        self.memory.observe(1, 10, 'Owner', 'Играет на гитаре')
        self.assertEqual(self.memory.db.execute('SELECT COUNT(*) FROM notes').fetchone()[0], 0)
        self.say('Запомни: Паша любит шахматы')
        self.say('Не подкалывай меня', uid=20, nick='PinkGuy')
        self.say('Меня зовут Паша', uid=20, nick='PinkGuy')
        self.assertIn('шахматы', self.memory.context(1, 20, 'Что я люблю?'))

    def test_existing_hash_notes_are_replaced_when_the_same_personal_fact_is_corrected(self):
        self.memory.save(1, 10, 'note:legacy', 'мой любимый цвет синий', target=10)
        self.say('Сохрани в памяти: мой любимый цвет зелёный')
        self.assertNotIn('синий', self.memory.context(1, 10, 'Мой любимый цвет?'))

    def test_personal_question_is_not_saved_as_a_self_statement(self):
        self.memory.observe(1, 10, 'Owner', 'Мой город Рига?')
        self.assertEqual(self.memory.db.execute('SELECT COUNT(*) FROM notes').fetchone()[0], 0)

    def test_unrelated_notes_are_excluded_when_topic_evidence_exists(self):
        self.say('Запомни: я играю в шахматы по субботам')
        self.say('Моя любимая игра — Dota 2')
        context = self.memory.context(1, 10, 'В какой день я играю в шахматы?')
        self.assertIn('субботам', context)
        self.assertNotIn('Dota 2', context)

    def test_personal_recall_quotes_own_note_without_model_role_confusion(self):
        self.say('Запомни: я играю в шахматы по субботам')
        for i in range(30):
            self.say(f'Запомни: номер моей игровой сессии {i}')
        self.memory.close()
        self.memory = MemoryStore(self.path)
        reply = self.say('В какой день я играю в шахматы?').reply
        self.assertEqual(reply, 'Ты говорил: «я играю в шахматы по субботам».')

    def test_personal_recall_does_not_quote_other_speaker_or_answer_reasoning(self):
        self.say('Запомни: я играю в шахматы по субботам')
        self.assertEqual(self.say('В какой день я играю в шахматы?', uid=20).reply, '')
        self.assertEqual(self.say('Почему я играю в шахматы плохо?').reply, '')
        self.assertEqual(self.say('В какой день я работаю?').reply, '')
        self.assertEqual(self.say('Что мне делать, если я играю в шахматы плохо?').reply, '')
        self.assertEqual(self.say('Какая стратегия мне поможет, если я играю в шахматы плохо?').reply, '')
        self.assertEqual(self.say('Где я могу научиться играть в шахматы?').reply, '')

    def test_personal_recall_does_not_choose_between_conflicting_notes(self):
        self.say('Запомни: я играю в шахматы по субботам')
        self.say('Запомни: я играю в шахматы по воскресеньям')
        self.assertEqual(self.say('В какой день я играю в шахматы?').reply, '')
