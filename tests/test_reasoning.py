import json
import asyncio
import logging
import unittest
from unittest.mock import patch
import httpx
from bot.config import Config
from bot.services.ollama_service import OllamaService


class ReasoningTests(unittest.IsolatedAsyncioTestCase):
    def test_pronouns_and_ordinary_comparisons_do_not_get_five_seconds(self):
        from bot.services.ollama_service import needs_reasoning, reasoning_window
        self.assertFalse(needs_reasoning('Он играет на гитаре?'))
        self.assertFalse(needs_reasoning('Это мой любимый фильм.'))
        self.assertEqual(reasoning_window('Сравни оперативную память и накопитель'), 1.0)
        self.assertEqual(reasoning_window('Реши задачу: бита дороже мяча на сто рублей'), 3.0)
    async def test_warmup_prepares_actual_shared_prompt_without_personal_memory(self):
        from bot.conversation import VOICE_TURN_RULES
        from bot.memory import MEMORY_RULES
        sent = []
        config = Config(memory_enabled=True)
        def respond(request):
            if request.url.path == '/api/tags':
                return httpx.Response(200, json={'models': [{'name': config.ollama_model, 'size': 1}]})
            sent.append(json.loads(request.content))
            return httpx.Response(200, json={'message': {'content': 'Готов.'}})
        service = OllamaService(logging.getLogger(), config)
        await service.client.aclose()
        service.client = httpx.AsyncClient(base_url='http://localhost', transport=httpx.MockTransport(respond))
        try:
            await service.warmup()
            self.assertTrue(service.ready)
            self.assertEqual(len(sent[0]['messages']), 2)
            self.assertIn(VOICE_TURN_RULES, sent[0]['messages'][0]['content'])
            self.assertIn(MEMORY_RULES, sent[0]['messages'][0]['content'])
            self.assertFalse(sent[0]['think'])
        finally:
            await service.close()

    async def test_ordinary_question_stops_long_thinking_and_answers(self):
        now, requests = [0.0], []
        class SlowThought(httpx.AsyncByteStream):
            closed = False
            async def __aiter__(self):
                yield b'{"message":{"thinking":"Private notes"},"done":false}\n'
                now[0] = 2.1
                yield b'{"message":{"thinking":"More notes"},"done":false}\n'
                await asyncio.Event().wait()
            async def aclose(self): self.closed = True
        stream = SlowThought()
        def respond(request):
            requests.append(json.loads(request.content))
            if len(requests) == 1:
                return httpx.Response(200, stream=stream)
            return httpx.Response(200, text=json.dumps({'message': {'content': 'Это рассеяние света.'}, 'done': True}))
        service = OllamaService(logging.getLogger(), Config(ollama_reasoning=True))
        await service.client.aclose()
        service.client = httpx.AsyncClient(base_url='http://localhost', transport=httpx.MockTransport(respond))
        try:
            timings = {}
            with patch('bot.services.ollama_service.time.perf_counter', lambda: now[0]):
                reply = await asyncio.wait_for(self.collect(service, 'Как мне лучше играть в Доту?', timings), 1)
            self.assertEqual(reply, ['Это рассеяние света.'])
            self.assertTrue(stream.closed)
            self.assertEqual(len(requests), 2)
            self.assertFalse(requests[-1]['think'])
            self.assertEqual(timings['llm_reasoning_time_limited'], 1)
        finally:
            await service.close()

    async def collect(self, service, question, timings):
        return [part async for part in service.stream_sentences([{'role': 'user', 'content': question}], timings)]

    async def test_complex_question_keeps_more_time_for_reasoning(self):
        now, requests = [0.0], []
        class ComplexThought(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b'{"message":{"thinking":"Check conditions"},"done":false}\n'
                now[0] = 2.1
                yield b'{"message":{"thinking":"Check calculation"},"done":false}\n'
                yield (json.dumps({'message': {'content': 'Пять рублей.'}, 'done': True}) + '\n').encode()
        def respond(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, stream=ComplexThought())
        service = OllamaService(logging.getLogger(), Config(ollama_reasoning=True))
        await service.client.aclose()
        service.client = httpx.AsyncClient(base_url='http://localhost', transport=httpx.MockTransport(respond))
        try:
            with patch('bot.services.ollama_service.time.perf_counter', lambda: now[0]):
                reply = await self.collect(service, 'Реши задачу про стоимость мяча.', {})
            self.assertEqual(reply, ['Пять рублей.'])
            self.assertEqual(len(requests), 1)
        finally:
            await service.close()

    async def test_cancellation_during_private_reasoning_closes_stream_and_unlocks(self):
        entered = asyncio.Event()
        class SlowThought(httpx.AsyncByteStream):
            closed = False
            async def __aiter__(self):
                yield b'{"message":{"thinking":"private"},"done":false}\n'
                entered.set()
                await asyncio.Event().wait()
            async def aclose(self): self.closed = True
        stream = SlowThought()
        service = OllamaService(logging.getLogger(), Config(ollama_reasoning=True))
        await service.client.aclose()
        service.client = httpx.AsyncClient(base_url='http://localhost', transport=httpx.MockTransport(
            lambda request: httpx.Response(200, stream=stream)))
        iterator = service.stream_sentences([{'role': 'user', 'content': 'Реши задачу.'}])
        task = asyncio.create_task(iterator.__anext__())
        try:
            await asyncio.wait_for(entered.wait(), 1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertFalse(service.lock.locked())
            self.assertTrue(stream.closed)
        finally:
            await iterator.aclose()
            await service.close()

    async def test_exhausted_thinking_budget_forces_short_final_answer(self):
        config = Config(ollama_reasoning=True)
        requests = []
        def respond(request):
            body = json.loads(request.content)
            requests.append(body)
            row = ({'message': {'thinking': 'Нужно проверить все условия.'}, 'done': True, 'done_reason': 'length'}
                   if len(requests) == 1 else {'message': {'content': 'Пять рублей.'}, 'done': True})
            return httpx.Response(200, text=json.dumps(row))
        service = OllamaService(logging.getLogger(), config)
        await service.client.aclose()
        service.client = httpx.AsyncClient(base_url='http://localhost', transport=httpx.MockTransport(respond))
        try:
            parts = [part async for part in service.stream_sentences([{'role': 'user', 'content': 'Реши задачу.'}])]
            self.assertEqual(parts, ['Пять рублей.'])
            self.assertEqual(len(requests), 2)
            self.assertFalse(requests[-1]['think'])
            final_messages = requests[-1]['messages']
            self.assertEqual(requests[-1]['options']['temperature'], config.ollama_temperature)
            self.assertEqual(requests[-1]['options']['repeat_penalty'], service.options['repeat_penalty'])
            self.assertEqual(requests[-1]['options']['num_predict'], 110)
            self.assertEqual(final_messages[-2]['role'], 'assistant')
            self.assertIn('Нужно проверить все условия.', final_messages[-2]['content'])
            self.assertIn('Реши задачу.', final_messages[-1]['content'])
            self.assertNotIn('Нужно проверить все условия.', final_messages[-1]['content'])
        finally:
            await service.close()

    async def run_question(self, question):
        config = Config()
        config.ollama_reasoning = True
        config.ollama_reasoning_budget = 512
        sent = []
        rows = [{'message': {'thinking': 'Внутренняя проверка, не произносить.'}, 'done': False},
                {'message': {'content': 'Пять рублей.'}, 'done': True}]
        def respond(request):
            sent.append(json.loads(request.content))
            return httpx.Response(200, text='\n'.join(json.dumps(row) for row in rows))
        service = OllamaService(logging.getLogger(), config)
        await service.client.aclose()
        service.client = httpx.AsyncClient(base_url='http://localhost', transport=httpx.MockTransport(respond))
        try:
            parts = [part async for part in service.stream_sentences([{'role': 'user', 'content': question}])]
            self.assertEqual(parts, ['Пять рублей.'])
            return sent[0]
        finally:
            await service.close()

    async def test_meaningful_question_gets_bounded_private_reasoning(self):
        request = await self.run_question('Бита и мяч стоят 110 рублей, разница 100. Сколько стоит мяч?')
        self.assertTrue(request['think'])
        self.assertGreaterEqual(request['options']['num_predict'], 512)
        self.assertLessEqual(request['options']['num_predict'], 772)

    async def test_greeting_uses_fast_path(self):
        request = await self.run_question('Привет, как дела?')
        self.assertFalse(request['think'])
        self.assertLessEqual(request['options']['num_predict'], 110)

    async def test_short_definitions_and_self_description_use_one_fast_pass(self):
        for question in ('Что ты умеешь?', 'Почему небо голубое?', 'Почему в космосе не слышно взрывов?', 'Что такое метеорит?'):
            request = await self.run_question(question)
            self.assertFalse(request['think'], question)

    async def test_short_logic_and_code_questions_keep_reasoning(self):
        for question in ('Почему корреляция не доказывает причинность?', 'Что такое рекурсия в коде?'):
            request = await self.run_question(question)
            self.assertTrue(request['think'], question)

    async def test_advice_and_recall_questions_are_checked_before_answering(self):
        for question in ('Как мне лучше играть в Доту?',
                         'Какую марку машины я назвал?', 'Какая у меня любимая игра?'):
            request = await self.run_question(question)
            self.assertTrue(request['think'], question)

    async def test_natural_voice_wording_does_not_enable_a_second_generation_pass(self):
        for question in ('Объясни мне, пожалуйста, почему небо голубое?',
                         'Расскажи коротко про чёрные дыры.',
                         'Почему при плохом освещении трудно различить оттенки на старой фотографии?',
                         'А почему вообще небо бывает голубым, объясни?',
                         'Какая разница между астероидом и кометой?'):
            request = await self.run_question('[Собеседник Тест]: ' + question)
            self.assertFalse(request['think'], question)

    async def test_greeting_does_not_hide_an_actual_analytical_request(self):
        request = await self.run_question('Привет, как дела? Реши задачу: бита и мяч стоят 110, бита дороже на 100.')
        self.assertTrue(request['think'])

