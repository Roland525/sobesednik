import asyncio
import logging
import threading
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, patch

from bot.bot import DiscordBot
from bot.config import Config
from bot.desktop_control import DesktopControl


class StartupReadinessTests(unittest.IsolatedAsyncioTestCase):
    async def test_setup_returns_before_gateway_or_slow_models_and_commands(self):
        gateway = asyncio.Event()
        models = asyncio.Event()
        bot = DiscordBot(Config(memory_enabled=False, learning_capture_enabled=False),
                         logging.getLogger(), logging.getLogger())
        bot.wait_until_ready = gateway.wait
        bot.ollama_service.warmup = AsyncMock(side_effect=models.wait)
        bot.audio_service.load = Mock()
        bot.tts_service.load = Mock()
        bot.tree.sync = AsyncMock()
        try:
            with patch.object(DesktopControl, 'run', side_effect=gateway.wait):
                await asyncio.wait_for(bot.setup_hook(), .5)
                await asyncio.sleep(0)
                bot.ollama_service.warmup.assert_not_awaited()
                bot.tree.sync.assert_not_awaited()
                gateway.set()
                await asyncio.sleep(.02)
                self.assertFalse(bot.services_ready)
                self.assertFalse(bot.services_task.done())
                bot.tree.sync.assert_awaited_once()
                models.set()
                await asyncio.wait_for(bot.services_task, 2)
                self.assertTrue(bot.services_ready)
                bot.audio_service.load.assert_called_once()
                bot.tts_service.load.assert_called_once()
        finally:
            models.set()
            await bot.close()

    async def test_failed_service_load_is_visible_and_does_not_allow_join(self):
        bot = DiscordBot(Config(memory_enabled=False, learning_capture_enabled=False),
                         logging.getLogger(), logging.getLogger())
        bot.wait_until_ready = AsyncMock()
        bot.ollama_service.warmup = AsyncMock()
        bot.audio_service.load = Mock(side_effect=RuntimeError('synthetic load failure'))
        try:
            with self.assertLogs(level='ERROR'):
                await bot.prepare_services()
            self.assertFalse(bot.services_ready)
            self.assertEqual(bot.services_error, 'RuntimeError')
            with self.assertRaisesRegex(Exception, 'Не удалось подготовить голос'):
                await bot.join_channel(NS())
        finally:
            await bot.close()

    async def test_close_retains_ownership_of_native_loader_until_it_finishes(self):
        entered, release = threading.Event(), threading.Event()
        bot = DiscordBot(Config(memory_enabled=False, learning_capture_enabled=False),
                         logging.getLogger(), logging.getLogger())
        bot.wait_until_ready = AsyncMock()
        bot.ollama_service.warmup = AsyncMock()
        def load():
            entered.set()
            release.wait(3)
        bot.audio_service.load = load
        bot.tts_service.load = Mock()
        bot.audio_service.close = Mock()
        try:
            bot.services_task = asyncio.create_task(bot.prepare_services())
            while not entered.is_set():
                await asyncio.sleep(.005)
            closing = asyncio.create_task(bot.close())
            await asyncio.sleep(.02)
            self.assertFalse(closing.done())
            bot.audio_service.close.assert_not_called()
            release.set()
            await asyncio.wait_for(closing, 2)
            bot.audio_service.close.assert_called_once()
            bot.tts_service.load.assert_not_called()
        finally:
            release.set()
            bot.audio_service.executor.shutdown(wait=True)
            bot.audio_service.control_executor.shutdown(wait=True)

    async def test_current_guilds_are_visible_during_preparation_and_after_invite(self):
        bot = NS(config=Config(), guilds=[], sessions={}, voice_routes=NS(status=lambda gid: {}),
                 is_ready=lambda: True, services_ready=False,
                 services_stage='Загрузка распознавания речи', services_error='', learning_request=None)
        control = DesktopControl.__new__(DesktopControl)
        control.bot = bot
        self.assertEqual(control.state()['guilds'], [])
        channel = NS(id=2, name='Voice', members=[],
                     permissions_for=lambda member: NS(view_channel=True, connect=True))
        bot.guilds.append(NS(id=1, name='New server', voice_channels=[channel],
                             voice_client=None, me=NS(display_name='Bot')))
        state = control.state()
        self.assertTrue(state['ready'])
        self.assertFalse(state['services_ready'])
        self.assertEqual(state['guilds'][0]['channels'][0]['id'], '2')
