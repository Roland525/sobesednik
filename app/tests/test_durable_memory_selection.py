"""New automatic writes need positive evidence of a durable, complete statement."""
import json
import logging
from pathlib import Path
import tempfile
import unittest

import httpx

from bot.config import Config
from bot.memory import MemoryStore
from bot.services.ollama_service import OllamaService


NOISE = (
    'Ну да', 'Ага, понятно', 'Всем привет', 'Это очень интересно',
    'Нормально всё', 'И потом мы', 'Потому что я', 'Договорились тогда',
    'Я люблю', 'Мне нравится', 'Я работаю над', 'Я обычно',
    'По пятницам после работы', 'Мой любимый цвет', 'Я люблю шахматы и',
    'Я работаю дизайнером, потому что', 'Я люблю шахматы, но',
    'Я люблю это', 'Мне нравится так', 'Я обычно вот так',
    'Я буду работать дизайнером', 'Я хочу играть в шахматы',
    'Я собираюсь играть в шахматы вечером', 'Мы договорились встретиться завтра',
    'Я работаю до шести', 'Как мне нравится играть в шахматы',
    'Скажи, что я люблю шахматы', 'Запомни, что я люблю шахматы',
    'У меня есть кот?', 'Я работаю дизайнером…',
    'Я люблю шахматы. А ты', 'Я работаю дизайнером. Привет всем',
)

DURABLE = (
    ('Я работаю дизайнером.', 'profile'),
    ('Мне нравится играть в шахматы.', 'preference'),
    ('Я не люблю громкую музыку.', 'preference'),
    ('У меня есть кот.', 'profile'),
    ('Я в одной команде с Валерой.', 'profile'),
    ('По пятницам после работы мы собираемся играть в шахматы.', 'routine'),
    ('Я обычно бегаю по утрам.', 'routine'),
    ('Мы договорились созваниваться по понедельникам.', 'agreement'),
    ('Мы договорились не перебивать друг друга.', 'agreement'),
)


class DurableMemoryWriteTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.memory = MemoryStore(Path(self.folder.name) / 'memory.db')

    def tearDown(self):
        self.memory.close()
        self.folder.cleanup()

    def test_model_category_cannot_turn_noise_into_an_automatic_fact(self):
        # Removing the positive durable gate must make this fail, even when
        # a selector confidently labels junk as a supported category.
        for text in NOISE:
            with self.subTest(text=text):
                self.assertFalse(self.memory.save_observation(1, 2, 'Speaker', text, text, 'profile'))
        self.assertEqual(self.memory.db.execute('SELECT COUNT(*) FROM notes').fetchone()[0], 0)

    def test_direct_auto_capture_and_ordinary_handle_also_reject_fragments(self):
        for text in NOISE:
            with self.subTest(text=text):
                self.assertFalse(self.memory.observe_auto(1, 2, 'Speaker', text))
                if not text.startswith('Запомни'):
                    self.assertFalse(self.memory.handle(1, 2, 'Speaker', text).reply)
        self.assertEqual(self.memory.db.execute('SELECT COUNT(*) FROM notes').fetchone()[0], 0)

    def test_clear_durable_statements_are_saved_with_the_original_speaker(self):
        for index, (text, category) in enumerate(DURABLE, 10):
            with self.subTest(text=text):
                self.assertTrue(self.memory.save_observation(1, index, 'Speaker', text, text, category))
                rows = self.memory.db.execute('SELECT source,target,content FROM notes WHERE source=?', (index,)).fetchall()
                self.assertEqual(len(rows), 1)
                self.assertEqual((rows[0]['source'], rows[0]['target']), (index, index))
                self.assertIn(text.rstrip('.'), rows[0]['content'])

    def test_explicit_one_word_and_transient_notes_remain_available(self):
        for text in ('Шахматы', 'Завтра встреча', 'Нравится'):
            with self.subTest(text=text):
                self.assertIn('Запомнил', self.memory.handle(1, 2, 'Speaker', 'Запомни: ' + text).reply)
        self.assertEqual(self.memory.db.execute('SELECT COUNT(*) FROM notes').fetchone()[0], 3)
        self.memory.handle(1, 3, 'Other', 'Запомни это')
        self.assertIn('Запомнил', self.memory.handle(1, 3, 'Other', 'Шахматы').reply)

    def test_new_filter_does_not_remove_existing_quotes_and_forget_is_scoped(self):
        self.memory.save(1, 2, 'auto:quote:old', 'Ага, понятно', target=2)
        self.memory.save(1, 3, 'auto:quote:old', 'Ну да', target=3)
        self.memory.close()
        self.memory = MemoryStore(Path(self.folder.name) / 'memory.db')
        self.assertFalse(self.memory.save_observation(1, 2, 'Speaker', 'Ну да', 'Ну да', 'profile'))
        self.assertEqual(self.memory.db.execute('SELECT COUNT(*) FROM notes').fetchone()[0], 2)
        self.memory.handle(1, 2, 'Speaker', 'Забудь всё обо мне')
        self.assertEqual([tuple(row) for row in self.memory.db.execute('SELECT source,content FROM notes')], [(3, 'Ну да')])

    def test_selected_third_party_quote_preserves_source_target_and_entire_evidence(self):
        self.memory.observe(1, 3, 'Other', 'Меня зовут Валера')
        text = 'Валера работает дизайнером.'
        self.assertTrue(self.memory.save_observation(1, 2, 'Speaker', text, text, 'profile'))
        row = self.memory.db.execute('SELECT source,target,content FROM notes').fetchone()
        self.assertEqual(tuple(row), (2, 3, text))
        self.assertFalse(self.memory.save_observation(1, 2, 'Speaker', 'Валера не работает дизайнером.', text, 'profile'))

    def test_selected_full_quote_keeps_its_discourse_prefix_and_cannot_be_reduced(self):
        text = 'Кстати, мы договорились не перебивать друг друга.'
        self.assertTrue(self.memory.save_observation(1, 2, 'Speaker', text, text, 'agreement'))
        row = self.memory.db.execute('SELECT source,target,content FROM notes').fetchone()
        self.assertEqual(tuple(row), (2, 2, text))
        self.assertFalse(self.memory.save_observation(1, 3, 'Other', text,
            'мы договорились не перебивать друг друга.', 'agreement'))

    def test_unresolved_or_ambiguous_third_party_is_not_assigned_to_the_speaker(self):
        text = 'Валера работает дизайнером.'
        self.assertFalse(self.memory.save_observation(1, 2, 'Speaker', text, text, 'profile'))
        self.memory.observe(1, 3, 'One', 'Меня зовут Валера')
        self.memory.observe(1, 4, 'Two', 'Меня зовут Валерий')
        self.assertFalse(self.memory.save_observation(1, 2, 'Speaker', text, text, 'profile'))
        self.assertEqual(self.memory.db.execute('SELECT COUNT(*) FROM notes').fetchone()[0], 0)

    def test_legacy_auto_noise_is_kept_with_history_but_excluded_from_retrieval(self):
        self.memory.register(1, 2, 'Speaker')
        self.memory.save(1, 2, 'auto:quote:old', 'Я люблю', target=2)
        self.memory.save(1, 2, 'auto:quote:old', 'Я люблю шахматы и', target=2)
        self.memory.save(1, 2, 'auto:preference:old', 'Я предпочитаю это', target=2)
        before = [tuple(row) for row in self.memory.db.execute('SELECT * FROM notes')]
        history = [tuple(row) for row in self.memory.db.execute('SELECT * FROM note_history')]
        self.assertEqual(json.loads(self.memory.context(1, 2, 'Какие шахматы я люблю?'))['notes'], [])
        self.assertFalse(self.memory.recall_check(1, 2, 'Какие шахматы я люблю?')['retrieved'])
        self.assertEqual(self.memory.handle(1, 2, 'Speaker', 'Какие шахматы я люблю?').reply, '')
        self.assertEqual([tuple(row) for row in self.memory.db.execute('SELECT * FROM notes')], before)
        self.assertEqual([tuple(row) for row in self.memory.db.execute('SELECT * FROM note_history')], history)

    def test_useful_auto_and_unfamiliar_explicit_records_remain_retrievable(self):
        self.memory.register(1, 2, 'Speaker')
        self.memory.save(1, 2, 'auto:occupation', 'Я работаю дизайнером', target=2)
        self.memory.save(1, 2, 'auto:preference:old', 'Я люблю шахматы', target=2)
        self.memory.save(1, 2, 'auto:quote:old', 'Я люблю шахматы и', target=2)
        self.memory.handle(1, 2, 'Speaker', 'Запомни: Ультрамарин')
        self.memory.save(1, 0, 'owner:unfamiliar', 'Назначен флиппер', target=2)
        self.memory.save(1, 2, 'city', 'Город: Рига', target=2)
        notes = json.loads(self.memory.context(1, 2, ''))['notes']
        self.assertEqual({note['content'] for note in notes}, {
            'Я работаю дизайнером', 'Я люблю шахматы', 'Ультрамарин', 'Назначен флиппер', 'Город: Рига'})
        self.assertIn('Ультрамарин', str(self.memory.recall_check(1, 2, 'Ультрамарин')))

    def test_negative_name_without_replacement_cannot_overwrite_confirmed_identity(self):
        from bot.memory import automatic_memory_category, automatic_memory_usable
        self.memory.observe_auto(1, 2, 'Speaker', 'Меня зовут Паша')
        for text in ('Меня зовут не Павел', 'Моё имя не Павел', 'Зови меня не Павел'):
            with self.subTest(text=text):
                self.assertEqual(automatic_memory_category(text), '')
                self.assertFalse(automatic_memory_usable({'slot': 'auto:quote:old', 'content': text}))
                self.assertFalse(self.memory.observe_auto(1, 2, 'Speaker', text))
                self.memory.handle(1, 2, 'Speaker', 'Запомни: ' + text)
                self.assertEqual(self.memory.profile(1, 2)['real_name'], 'Паша')
                self.assertTrue(self.memory.profile(1, 2)['confirmed'])
        self.assertFalse(self.memory.set_name(1, 2, 2, 'не Павел'))
        self.assertTrue(self.memory.observe_auto(1, 2, 'Speaker', 'Меня зовут не Паша, а Дима'))
        self.assertEqual(self.memory.profile(1, 2)['real_name'], 'Дима')

    def test_explicit_occupation_in_shared_canonical_slot_remains_retrievable(self):
        text = 'Я работаю над большим проектом'
        self.assertIn('Запомнил', self.memory.handle(1, 2, 'Speaker', 'Запомни: ' + text).reply)
        row = self.memory.db.execute('SELECT slot,content FROM notes').fetchone()
        self.assertEqual(tuple(row), ('auto:occupation', text))
        self.assertIn(text, str(self.memory.recall_check(1, 2, 'Над каким проектом я работаю?')))


class AutomaticMemoryUsabilityTests(unittest.TestCase):
    def test_public_helper_accepts_dicts_and_sqlite_rows_and_only_filters_auto_slots(self):
        from bot.memory import automatic_memory_usable
        for record, expected in (
            ({'slot': 'auto:quote:old', 'content': 'Ну да'}, False),
            ({'slot': 'auto:preference:old', 'content': 'Я люблю шахматы и'}, False),
            ({'slot': 'auto:occupation', 'content': 'Я работаю дизайнером'}, True),
            ({'slot': 'auto:occupation', 'content': 'Я работаю над большим проектом'}, True),
            ({'slot': 'note:explicit', 'content': 'Ну да'}, True),
            ({'slot': 'owner:manual', 'content': 'Ультрамарин'}, True),
            ({'slot': 'city', 'content': 'Город: Рига'}, True),
        ):
            with self.subTest(record=record):
                self.assertIs(automatic_memory_usable(record), expected)
        with tempfile.TemporaryDirectory() as folder:
            memory = MemoryStore(Path(folder) / 'memory.db')
            try:
                memory.save(1, 2, 'auto:quote:old', 'Ага, понятно', target=2)
                row = memory.db.execute('SELECT * FROM notes').fetchone()
                self.assertIs(automatic_memory_usable(row), False)
            finally:
                memory.close()


class DurableSelectorProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_noise_only_batch_does_not_call_conversation_model(self):
        service = OllamaService(logging.getLogger(__name__), Config())
        await service.client.aclose()
        requests = []
        def handler(request):
            requests.append(request)
            return httpx.Response(200, json={'message': {'content': '{"facts":[]}'}})
        service.client = httpx.AsyncClient(base_url='http://selector.test', transport=httpx.MockTransport(handler))
        try:
            result = await service.select_memories([{'speaker': 'Speaker', 'text': text} for text in
                ('Ну там в меню вроде тоже', 'Шарум барум, шурум бурум', 'Блин, у нас тут вообще ничего нет')])
            self.assertEqual(result, [])
            self.assertEqual(requests, [], 'Rejected chatter must not evict the conversation prompt cache')
        finally:
            await service.close()

    async def test_prefilter_keeps_original_indices_and_sends_only_durable_candidates(self):
        service = OllamaService(logging.getLogger(__name__), Config())
        await service.client.aclose()
        requests = []
        def handler(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json={'message': {'content': '{"facts":[{"index":1,"category":"profile"}]}'}})
        service.client = httpx.AsyncClient(base_url='http://selector.test', transport=httpx.MockTransport(handler))
        try:
            result = await service.select_memories([{'speaker': 'Speaker', 'text': 'Ну да'},
                {'speaker': 'Speaker', 'text': 'Я работаю дизайнером.'}])
            self.assertEqual(result, [{'index': 1, 'category': 'profile', 'quote': 'Я работаю дизайнером.'}])
            sent = json.loads(requests[0]['messages'][-1]['content'])
            self.assertEqual(sent, [{'index': 1, 'speaker': 'Speaker', 'text': 'Я работаю дизайнером.'}])
        finally:
            await service.close()

    async def select(self, texts, facts):
        service = OllamaService(logging.getLogger(__name__), Config())
        await service.client.aclose()
        service.client = httpx.AsyncClient(base_url='http://selector.test', transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={'model': 'test', 'done': True,
                'message': {'role': 'assistant', 'content': json.dumps({'facts': facts})}})))
        try:
            return await service.select_memories([{'index': i, 'speaker': 'Speaker', 'text': text} for i, text in enumerate(texts)])
        finally:
            await service.close()

    async def test_model_selection_is_filtered_and_full_quote_is_attached_by_code(self):
        texts = ['Ну да', 'По пятницам после работы мы собираемся играть в шахматы.', 'Я люблю шахматы и']
        result = await self.select(texts, [{'index': i, 'category': 'routine', 'quote': 'invented', 'confidence': 1} for i in range(3)])
        self.assertEqual(result, [{'index': 1, 'category': 'routine', 'quote': texts[1]}])

    async def test_unknown_categories_and_malformed_indices_cannot_create_candidates(self):
        result = await self.select(['У меня есть кот.'], [
            {'index': True, 'category': 'profile'}, {'index': 1, 'category': 'profile'},
            {'index': -1, 'category': 'profile'}, {'index': 0, 'category': 'anything'},
            {'index': 0}, None])
        self.assertEqual(result, [])

    async def test_valid_supported_quote_survives_without_accepting_model_rewrite(self):
        result = await self.select(['У меня есть кот.'], [{'index': 0, 'category': 'profile', 'quote': 'У меня собака.'}])
        self.assertEqual(result, [{'index': 0, 'category': 'profile', 'quote': 'У меня есть кот.'}])
