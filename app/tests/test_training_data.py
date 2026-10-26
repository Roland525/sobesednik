import unittest

from bot.training_data import prepare_samples, informative_dialogue


class Tokenizer:
    def encode(self, text, add_special_tokens=False):
        return list(text.encode('utf-8'))


def row(prompt, target, source='human_dialogue'):
    return {'source': source, 'messages': [{'role': 'user', 'content': prompt},
                                          {'role': 'assistant', 'content': target}]}


class TrainingDataTests(unittest.TestCase):
    def test_attributed_opinions_do_not_become_automatic_training_answers(self):
        self.assertFalse(informative_dialogue('Какая игра заслуживает внимания?',
            'Half Life 2 Episode Two лучшая игра.'))
        self.assertFalse(informative_dialogue('Какая игра заслуживает внимания?',
            'Кстати, Half Life 2 Episode Two лучшая игра.'))

    def test_natural_learning_uses_complete_explanations_not_backchannels_or_identity(self):
        self.assertTrue(informative_dialogue('Почему небо голубое?',
            'Короткие волны солнечного света сильнее рассеиваются в атмосфере.'))
        for prompt,target in [('Ну вот он пришёл', 'Да да да да да вот он пришёл'),
                              ('Почему небо голубое?', 'Ну это такое всё вообще да'),
                              ('Как тебя зовут?', 'Меня зовут Валера и я сейчас тут играю.'),
                              ('Что такое метеор?', 'А что ты вообще хочешь сейчас от меня?')]:
            self.assertFalse(informative_dialogue(prompt,target))

    def test_questions_are_disjoint_and_corrected_answer_wins_over_old_answer(self):
        rows = [row('Первый вопрос?', 'Старый ответ.'),
                row('Первый вопрос?', 'Новый ответ.', 'correction'),
                row('Второй вопрос?', 'Второй ответ.'),
                row('Третий вопрос?', 'Третий ответ.')]
        training, heldout, stats = prepare_samples(rows, Tokenizer(), 1000, 3)
        self.assertEqual(stats['duplicate_prompts'], 1)
        all_samples = training + heldout
        self.assertEqual(len(all_samples), 3)
        self.assertEqual(len({x[2] for x in all_samples}), 3)
        targets = [bytes([n for n in labels if n != -100]).decode() for _, labels, _ in all_samples]
        self.assertFalse(any('Старый ответ' in x for x in targets))
        self.assertTrue(any('Новый ответ' in x for x in targets))
        self.assertTrue(all(any(x == -100 for x in labels) for _, labels, _ in all_samples))

    def test_invalid_roles_and_bot_only_examples_fail_before_model_load(self):
        for rows in ([row('Вопрос', 'Ответ', 'bot_guess')],
                     [{'source': 'confirmed', 'messages': [{'role': 'system', 'content': 'x'}]}]):
            with self.assertRaises(ValueError):
                prepare_samples(rows, Tokenizer(), 1000, 2)

    def test_empty_and_overlong_targets_cannot_silently_satisfy_minimum(self):
        for rows in ([row('Вопрос', '')], [row('Вопрос', 'а' * 1000)]):
            with self.assertRaises((RuntimeError, ValueError)):
                prepare_samples(rows, Tokenizer(), 128, 2)
