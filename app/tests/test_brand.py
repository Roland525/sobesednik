import unittest
from bot.config import Config
from bot.conversation import Conversation, addressed, VOICE_TURN_RULES
from bot.controls import voice_control
from bot.services.ollama_service import self_directed_reply


class BrandTests(unittest.TestCase):
    def test_new_name_preserves_old_addressing_and_stop(self):
        config = Config()
        self.assertEqual(config.bot_name, 'Собеседник')
        self.assertEqual(config.wake_word, 'собеседник')
        for question in ('Собеседник, почему небо голубое?', 'Почему небо голубое, Собеседник?',
                         'Серёга, почему небо голубое?'):
            self.assertTrue(addressed(question, config.wake_word)[0])
        self.assertEqual(voice_control('Собеседник, заткнись', config.wake_word), 'mute')
        messages = Conversation(config).messages(1, 'Test', 'Почему небо голубое?')
        self.assertTrue(messages[-1]['content'].startswith('[Участник '))
        self.assertNotIn('Ты — Серёга', VOICE_TURN_RULES)
        self.assertTrue(self_directed_reply('Собеседник, ты тут?', config.wake_word))
