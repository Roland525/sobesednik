import asyncio
import logging
import time
import unittest
from bot.config import Config
from bot.session import GuildSession, Question
from bot.factuality import freshness_reply


class CurrentFactsTests(unittest.IsolatedAsyncioTestCase):
    def test_stable_knowledge_and_supplied_patch_text_remain_available(self):
        for text in ('Что такое патч?', 'Почему небо голубое?', 'Как играть за Pudge в Dota 2?',
                     'Вот текст патча: у героя уменьшили скорость передвижения на десять единиц '
                     'и увеличили время восстановления первой способности на две секунды. Объясни новое обновление.'):
            self.assertIsNone(freshness_reply(text), text)

    def test_explicit_fiction_is_not_treated_as_a_current_fact_request(self):
        self.assertIsNone(freshness_reply('Придумай смешное новое обновление Dota 2 для шутки.'))

    async def test_latest_patch_does_not_reach_offline_model(self):
        class LLM:
            async def stream_sentences(self, *args):
                yield 'В новом обновлении Dota 2 добавили двух героев.'
        state = GuildSession(1, Config(), None, LLM(), None, logging.getLogger(), logging.getLogger())
        try:
            for question in ('Расскажи про новое обновление Dota 2.', 'Что добавили в последнем патче доты?',
                             'Кого добавили в свежем обновлении Дота?', 'Какие новости Dota 2 сегодня?'):
                future = asyncio.get_running_loop().create_future()
                state.enqueue(Question(1, 'Тест', question, time.monotonic(), kind='text', future=future))
                answer = await asyncio.wait_for(future, 2)
                self.assertNotIn('добавили двух героев', answer, question)
                self.assertRegex(answer.lower(), 'актуальн|свеж|патчноут|проверенн')
        finally:
            await state.close()

