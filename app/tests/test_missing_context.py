import json
import logging
import unittest
import httpx
from bot.config import Config
from bot.services.ollama_service import OllamaService


class MissingContextTests(unittest.IsolatedAsyncioTestCase):
    async def answer(self, question, history=()):
        calls = []
        def respond(request):
            calls.append(json.loads(request.content))
            return httpx.Response(200, text=json.dumps({'message': {'content': 'Да, он уже это сделал.'}, 'done': True}))
        service = OllamaService(logging.getLogger(), Config())
        await service.client.aclose()
        service.client = httpx.AsyncClient(base_url='http://localhost', transport=httpx.MockTransport(respond))
        try:
            messages = [{'role': 'system', 'content': 'Тест'}, *history,
                        {'role': 'user', 'content': '[Собеседник Тест]: ' + question}]
            timings = {}
            reply = ' '.join([part async for part in service.stream_sentences(messages, timings)])
            return reply, calls, timings
        finally:
            await service.close()

    async def test_unresolved_short_questions_ask_for_context_without_model_call(self):
        for question in ('Он это уже сделал?', 'А она уже пришла?', 'Почему они так поступили?',
                         'Это правда?', 'Это работает?', 'Что ты об этом думаешь?',
                         'Я правильно сделал?', 'У меня получилось?', 'С этим всё в порядке?'):
            with self.subTest(question=question):
                reply, calls, timings = await self.answer(question)
                self.assertEqual(calls, [])
                self.assertRegex(reply, 'О ком|О чём')
                self.assertEqual(timings['llm_context_clarification'], 1)

    async def test_greeting_is_not_an_antecedent(self):
        reply, calls, _ = await self.answer('Он уже закончил?', [
            {'role': 'user', 'content': '[Собеседник Тест]: Привет, как дела?'},
            {'role': 'assistant', 'content': 'Привет!'}])
        self.assertEqual(calls, [])
        self.assertIn('О ком', reply)

    async def test_assistant_guess_is_not_human_grounding(self):
        reply, calls, _ = await self.answer('Он это сделал?', [
            {'role': 'assistant', 'content': 'Петя уже всё сделал.'}])
        self.assertEqual(calls, [])
        self.assertIn('О ком', reply)

    async def test_human_context_is_preserved_for_model(self):
        context = {'role': 'user', 'content': '[Собеседник Тест]: Петя сказал, что уже закончил ремонт.'}
        reply, calls, _ = await self.answer('Он это уже сделал?', [context])
        self.assertEqual(len(calls), 1)
        self.assertIn(context, calls[0]['messages'])
        self.assertTrue(reply.startswith('Да'))

    async def test_self_contained_and_quoted_questions_are_not_blocked(self):
        for question in ('Это правда, что Земля вращается вокруг Солнца?',
                         'Почему она называется Венера?', 'Он сделал домашнее задание по словам Пети?',
                         'Переведи фразу «Он это сделал?» на английский.',
                         'Почему небо голубое?', 'Как это работает: двигатель внутреннего сгорания?'):
            with self.subTest(question=question):
                _, calls, _ = await self.answer(question)
                self.assertEqual(len(calls), 1)
