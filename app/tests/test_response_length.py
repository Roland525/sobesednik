import json
import logging
import unittest
import httpx
from bot.config import Config
from bot.services.ollama_service import OllamaService


class ResponseLengthTests(unittest.IsolatedAsyncioTestCase):
    def test_sentence_ending_in_number_is_counted_but_decimal_is_preserved(self):
        from bot.services.ollama_service import SentenceBuffer
        buffer = SentenceBuffer()
        self.assertEqual(buffer.feed('После повышения стало 96. Число пи 3.14. '),
                         ['После повышения стало 96.', 'Число пи 3.14.'])

    async def test_length_limit_does_not_speak_unfinished_tail(self):
        service = OllamaService(logging.getLogger(), Config())
        await service.client.aclose()
        rows = [{'message': {'content': 'Короткий точный ответ. '}, 'done': False},
                {'message': {'content': 'А это ненужное длинное продолжение ' * 20}, 'done': False}]
        service.client = httpx.AsyncClient(base_url='http://localhost', transport=httpx.MockTransport(
            lambda request: httpx.Response(200, text='\n'.join(json.dumps(row) for row in rows))))
        try:
            parts = [part async for part in service.stream_sentences([{'role': 'user', 'content': 'Ответь кратко.'}])]
            self.assertEqual(parts, ['Короткий точный ответ.'])
        finally:
            await service.close()

    async def answer(self, question):
        sentences = ['Одна короткая мысль.', 'Вторая короткая мысль.', 'Третья мысль.',
                     'Четвёртая мысль.', 'Пятая мысль.', 'Шестая мысль.']
        rows = [{'message': {'content': sentence + ' '}, 'done': False} for sentence in sentences]
        rows.append({'message': {'content': ''}, 'done': True})
        service = OllamaService(logging.getLogger(), Config())
        await service.client.aclose()
        service.client = httpx.AsyncClient(base_url='http://localhost', transport=httpx.MockTransport(
            lambda request: httpx.Response(200, text='\n'.join(json.dumps(row) for row in rows))))
        try:
            return [part async for part in service.stream_sentences([
                {'role': 'system', 'content': 'Серёга'},
                {'role': 'user', 'content': '[Тест, id=1]: ' + question}])]
        finally:
            await service.close()

    async def test_plain_question_and_why_are_short(self):
        for question in ('Как дела?', 'Почему небо голубое?', 'Как устроен интернет?'):
            self.assertLessEqual(len(await self.answer(question)), 2, question)

    async def test_explicit_detail_and_conversation_can_be_longer(self):
        for question in ('Объясни подробно, почему небо голубое.', 'Давай поговорим о кино.'):
            self.assertGreater(len(await self.answer(question)), 2, question)

    async def test_request_to_be_brief_overrides_topic_and_detail_words(self):
        self.assertLessEqual(len(await self.answer('Кратко объясни, почему интернет устроен так.')), 2)

