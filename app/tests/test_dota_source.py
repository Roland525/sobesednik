import asyncio
import json
import logging
import time
import unittest
from unittest.mock import patch
import httpx
from bot.config import Config
from bot.session import GuildSession, Question


class DotaSourceTests(unittest.IsolatedAsyncioTestCase):
    async def ask(self, enabled=True, broken=False):
        config = Config()
        config.dota_patch_source_enabled = enabled
        state = GuildSession(1, config, None, None, None, logging.getLogger(), logging.getLogger())
        requests = []
        def respond(request):
            requests.append(request)
            path = request.url.path.rsplit('/', 1)[-1]
            values = {
                'patchnoteslist': {'patches': [{'patch_number': '7.41e', 'patch_timestamp': 100},
                                                {'patch_number': '7.41f', 'patch_timestamp': 200}]},
                'patchnotes': {'success': True, 'patch_number': '7.41f',
                    'heroes': [{'hero_id': 1, 'abilities': [{'ability_notes': [{'note': 'Урон увеличен с 60% до 65%'}]}]}],
                    'items': [{'ability_id': 265, 'ability_notes': [{'note': 'Восстановление маны уменьшено с 0,8 до 0,6'}]}]},
                'herolist': {'result': {'data': {'heroes': [{'id': 1, 'name_loc': 'Anti-Mage'}]}}},
                'itemlist': {'result': {'data': {'itemabilities': [{'id': 265, 'name_loc': 'Sage Mask'}]}}}}
            return httpx.Response(503 if broken else 200, json={} if broken else values[path])
        client = httpx.AsyncClient(base_url='https://www.dota2.com', transport=httpx.MockTransport(respond))
        try:
            with patch('httpx.AsyncClient', return_value=client):
                future = asyncio.get_running_loop().create_future()
                state.enqueue(Question(1, 'Тест', 'Расскажи про новое обновление Dota 2.', time.monotonic(), kind='text', future=future))
                answer = await asyncio.wait_for(future, 3)
            return answer, requests
        finally:
            await client.aclose()
            await state.close()

    async def test_latest_patch_uses_only_fixed_valve_routes_and_verified_notes(self):
        answer, requests = await self.ask()
        self.assertIn('7.41f', answer)
        self.assertIn('Anti-Mage', answer)
        self.assertIn('65%', answer)
        self.assertNotIn('добавили', answer)
        self.assertEqual(len(requests), 4)
        self.assertTrue(all(request.url.host == 'www.dota2.com' for request in requests))
        self.assertTrue(all(request.method == 'GET' and not request.content for request in requests))
        self.assertTrue(all(set(request.url.params) <= {'version', 'language'} for request in requests))

    async def test_disabled_source_never_uses_internet(self):
        answer, requests = await self.ask(enabled=False)
        self.assertFalse(requests)
        self.assertIn('нет проверенных', answer)

    async def test_source_failure_is_honest_instead_of_fabricating_patch(self):
        answer, _ = await self.ask(broken=True)
        self.assertIn('нет проверенных', answer)
        self.assertNotIn('7.41f', answer)

    async def test_unknown_explicit_version_is_not_replaced_with_latest(self):
        from bot.services.dota_patch_source import DotaPatchSource
        def respond(request):
            return httpx.Response(200, json={'patches': [{'patch_number': '7.41f', 'patch_timestamp': 200}]})
        client = httpx.AsyncClient(base_url='https://www.dota2.com', transport=httpx.MockTransport(respond))
        with patch('httpx.AsyncClient', return_value=client):
            answer = await DotaPatchSource(logging.getLogger()).reply('Расскажи о патче Dota 2 7.99z')
        self.assertIsNone(answer)

    async def test_other_topics_never_use_valve_connection(self):
        from bot.services.dota_patch_source import DotaPatchSource
        with patch('httpx.AsyncClient') as client:
            self.assertIsNone(await DotaPatchSource(logging.getLogger()).reply('Что нового в Windows?'))
            client.assert_not_called()

