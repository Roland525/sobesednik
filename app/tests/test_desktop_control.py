import asyncio
import json
from pathlib import Path
from types import SimpleNamespace as NS
import tempfile
import time
import unittest
from unittest.mock import AsyncMock


class DesktopProtocolTests(unittest.TestCase):
    def test_authentication_expiration_and_allowlist(self):
        from desktop.protocol import Protocol
        with tempfile.TemporaryDirectory() as folder:
            p=Protocol(Path(folder)); rid=p.request('reset',{'guild':1})
            path=p.commands/(rid+'.json'); value=json.loads(path.read_text())
            self.assertTrue(p.valid(value))
            value['payload']['guild']=2
            self.assertFalse(p.valid(value))
            with self.assertRaises(ValueError): p.request('shell',{'command':'anything'})
            rid=p.request('reset',{},created=time.time()-300)
            self.assertFalse(p.valid(json.loads((p.commands/(rid+'.json')).read_text())))


class DesktopControlTests(unittest.IsolatedAsyncioTestCase):
    async def test_join_accepts_actual_discord_channel_type_enum(self):
        import discord
        from bot.desktop_control import DesktopControl
        with tempfile.TemporaryDirectory() as folder:
            channel=NS(type=discord.ChannelType.voice,permissions_for=lambda member:NS(view_channel=True,connect=True))
            guild=NS(id=1,me=object(),get_channel=lambda value:channel)
            bot=NS(get_guild=lambda value:guild,join_channel=AsyncMock())
            control=DesktopControl(bot,Path(folder))
            await control.dispatch('join',{'guild':1,'channel':2})
            bot.join_channel.assert_awaited_once_with(channel)

    async def test_context_replacement_rejects_system_and_resets_old_generation(self):
        from bot.desktop_control import DesktopControl
        with tempfile.TemporaryDirectory() as folder:
            session=NS(reset=AsyncMock(),voice_client=None,conversation=NS(history=[],trim=lambda:None))
            guild=NS(id=1)
            bot=NS(get_guild=lambda value:guild if value==1 else None,sessions={1:session})
            control=DesktopControl(bot,Path(folder))
            with self.assertRaises(ValueError):
                await control.dispatch('replace_context',{'guild':1,'messages':[{'role':'system','content':'bad'}]})
            session.reset.assert_not_awaited()
            await control.dispatch('replace_context',{'guild':1,'messages':[{'role':'user','content':'a question'}]})
            session.reset.assert_awaited_once()
            self.assertEqual(session.conversation.history[0]['content'],'a question')

    async def test_join_cannot_target_inaccessible_channel(self):
        from bot.desktop_control import DesktopControl
        with tempfile.TemporaryDirectory() as folder:
            channel=NS(type=2,permissions_for=lambda member:NS(view_channel=False,connect=False))
            guild=NS(id=1,me=object(),get_channel=lambda value:channel)
            bot=NS(get_guild=lambda value:guild,join_channel=AsyncMock())
            with self.assertRaises(ValueError):
                await DesktopControl(bot,Path(folder)).dispatch('join',{'guild':1,'channel':2})
            bot.join_channel.assert_not_awaited()
