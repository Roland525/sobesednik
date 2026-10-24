import asyncio
import logging
from types import SimpleNamespace as NS
import time
import unittest
from unittest.mock import Mock

from bot.config import Config
from bot.session import GuildSession, Question
from bot.voice_routes import VoiceRoutes, parse_order


class ParsingTests(unittest.TestCase):
    def test_conversational_move_with_trailing_channel_and_return(self):
        for text in ('Можешь ты зайти в Общий канал и вернуться?',
                     'Ты можешь зайти в Общий канал и вернуться?',
                     'Зайди пожалуйста в Общий канал и вернись'):
            order = parse_order(text)
            self.assertEqual(order.destination, 'Общий')
            self.assertTrue(order.return_back)

    def test_move_and_literal_relay_roundtrip(self):
        for text in ('Перейди в канал Общий', 'Можешь перейти в голосовой канал «Общий»?'):
            order = parse_order(text)
            self.assertEqual(order.destination, 'Общий')
            self.assertFalse(order.relay)
        order = parse_order('Перейди в канал Общий, скажи: «Собираемся через пять минут», и вернись ко мне')
        self.assertEqual(order.destination, 'Общий')
        self.assertEqual(order.message, 'Собираемся через пять минут')
        self.assertTrue(order.return_back)
        self.assertTrue(order.relay)
        order = parse_order('Перейди в канал Общий, скажи Мы готовы, после чего перейди обратно ко мне в канал')
        self.assertEqual(order.message, 'Мы готовы')
        self.assertTrue(order.return_back)

    def test_relay_first_and_person_destination(self):
        order = parse_order('Передай сообщение в канал Общий: Мы готовы')
        self.assertEqual((order.destination, order.message), ('Общий', 'Мы готовы'))
        order = parse_order('Зайди к Паше и скажи: Привет')
        self.assertTrue(order.person)
        self.assertEqual(order.destination, 'Паше')

    def test_discussion_quotes_and_payload_are_not_executed(self):
        for text in ('Как перейти в другой канал?', 'Он сказал: перейди в канал Общий', '«Перейди в канал Общий»'):
            self.assertIsNone(parse_order(text))
        self.assertIsNone(parse_order('Зайди на демку'))
        order = parse_order('Перейди в канал Общий, скажи «вернись ко мне»')
        self.assertFalse(order.return_back)
        self.assertEqual(order.message, 'вернись ко мне')


class RouteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.guild = NS(id=1, me=NS(), voice_client=None)
        self.author = NS(id=10, display_name='Owner', name='owner', bot=False)
        self.channels = []
        for cid, name in ((11, 'Наш'), (22, 'Общий'), (33, 'Игровой')):
            channel = NS(id=cid, name=name, guild=self.guild, members=[], category=None, user_limit=0)
            channel.permissions = NS(view_channel=True, connect=True, speak=True, move_members=False)
            channel.permissions_for = lambda member, channel=channel: channel.permissions
            self.channels.append(channel)
        self.channels[0].members.append(self.author)
        self.guild.voice_channels = self.channels
        self.guild.voice_client = NS(channel=self.channels[0])
        self.joined, self.spoken = [], []
        self.bot = NS(config=Config(memory_enabled=False), memory=None, logger=logging.getLogger(),
                      shutting_down=False, get_guild=lambda gid: self.guild if gid == 1 else None, sessions={})
        self.routes = VoiceRoutes(self.bot)
        self.session = GuildSession(1, self.bot.config, None, None, None, logging.getLogger(),
                                    logging.getLogger(), voice_routes=self.routes)
        self.session.voice_client = self.guild.voice_client
        self.session.bound_channel_id = 11
        self.bot.sessions[1] = self.session

        async def join(channel):
            # Real connection resets the current response worker. The errand
            # must run outside that worker or it would cancel itself here.
            await self.session.reset()
            self.guild.voice_client = self.session.voice_client = NS(channel=channel)
            self.session.bound_channel_id = channel.id
            self.joined.append(channel.id)
        self.bot.join_channel = join

        async def say(guild, text):
            self.spoken.append((self.guild.voice_client.channel.id, text))
        self.routes.say = say

    async def asyncTearDown(self):
        await self.routes.close()
        self.session.voice_client = None
        await self.session.close()

    def prepare(self, text, uid=10, kind='voice'):
        return self.routes.prepare(self.session, Question(uid, 'Owner', text, time.monotonic(), kind=kind))

    async def execute(self, decision):
        self.assertIsNotNone(decision.job)
        self.routes.start(decision.job)
        await self.routes.active[1][0]

    async def test_roundtrip_delivers_once_as_speaker_and_restores_origin(self):
        decision = self.prepare('Перейди в канал Общий, скажи: Мы готовы, и вернись ко мне')
        self.assertIn('вернусь', decision.reply)
        await self.execute(decision)
        self.assertEqual(self.joined, [22, 11])
        self.assertEqual(self.spoken[0], (22, 'Сообщение от Owner: Мы готовы'))
        self.assertEqual(len([s for _, s in self.spoken if 'Мы готовы' in s]), 1)
        self.assertFalse(self.routes.active)

    async def test_progress_only_marks_delivery_and_return_after_they_complete(self):
        await self.execute(self.prepare('Перейди в Общий, скажи: Мы готовы, и вернись'))
        status=self.routes.status(1)
        self.assertEqual(status['outcome'],'completed')
        self.assertTrue(status['delivered']);self.assertTrue(status['returned'])
        stages=[event['stage'] for event in status['events']]
        self.assertLess(stages.index('joined'),stages.index('delivered'))
        self.assertLess(stages.index('delivered'),stages.index('returned'))

    async def test_unconfirmed_move_and_failed_return_never_report_success(self):
        original=self.bot.join_channel
        async def noop(channel):pass
        self.bot.join_channel=noop
        await self.execute(self.prepare('Перейди в Общий, скажи: Мы готовы, и вернись'))
        self.assertEqual(self.routes.status(1)['outcome'],'failed')
        self.assertFalse(self.routes.status(1)['delivered'])
        self.assertFalse(any('Сообщение передал' in text for _,text in self.spoken))
        async def fail_return(channel):
            if channel.id==11:raise RuntimeError('return failed')
            await original(channel)
        self.bot.join_channel=fail_return
        await self.execute(self.prepare('Перейди в Общий, скажи: Мы готовы, и вернись'))
        self.assertEqual(self.routes.status(1)['outcome'],'failed')
        self.assertTrue(self.routes.status(1)['delivered']);self.assertFalse(self.routes.status(1)['returned'])

    async def test_cancelled_playback_can_return_but_cannot_claim_delivery(self):
        entered=asyncio.Event()
        async def say(guild,text):entered.set();await asyncio.sleep(20)
        self.routes.say=say
        decision=self.prepare('Перейди в Общий, скажи: Мы готовы, и вернись')
        self.routes.start(decision.job);task=self.routes.active[1][0]
        await entered.wait();self.routes.cancel(1,restore=True)
        await asyncio.gather(task,return_exceptions=True)
        self.assertEqual(self.routes.status(1)['outcome'],'cancelled')
        self.assertFalse(self.routes.status(1)['delivered'])
        self.assertEqual(self.guild.voice_client.channel.id,11)

    async def test_one_way_move_and_relay_without_return(self):
        await self.execute(self.prepare('Перейди в канал Общий'))
        self.assertEqual(self.joined, [22])
        self.assertEqual(self.spoken, [])

    async def test_uncertain_name_suggests_but_never_moves_without_selection(self):
        decision = self.prepare('Зайди в Обший канал и вернись')
        self.assertIsNone(decision.job)
        self.assertIn('Общий', decision.reply)
        self.assertEqual(self.joined, [])
        decision = self.prepare('Общий')
        self.assertTrue(decision.job.return_back)
        self.assertEqual(decision.job.target.id, 22)

    async def test_unspecified_one_channel_asks_destination(self):
        decision = self.prepare('Зайди в один канал и вернись')
        self.assertIsNone(decision.job)
        self.assertIn('какой', decision.reply)

    async def test_move_without_message_confirms_return(self):
        decision = self.prepare('Зайди в Общий канал и вернись')
        self.assertIn('вернусь', decision.reply)
        await self.execute(decision)
        self.assertEqual(self.joined, [22, 11])
        self.assertEqual(self.spoken, [(11, 'Вернулся.')])

    async def test_explicit_order_interrupts_old_generation_before_enqueue(self):
        started, cancelled = asyncio.Event(), asyncio.Event()
        async def old_answer():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        self.session.response_task = asyncio.create_task(old_answer())
        await started.wait()
        self.session.responding, self.session.current_kind = True, 'voice'
        question = Question(10, 'Owner', 'Зайди в Общий канал и вернись', time.monotonic())
        self.session.enqueue = Mock(side_effect=lambda q: self.assertTrue(cancelled.is_set()))
        await asyncio.wait_for(self.session.submit_direct(question), 1)
        self.session.enqueue.assert_called_once_with(question)
        self.assertEqual(question.timings['command_preempted_reply'], 1)

    async def test_ordinary_followup_does_not_cancel_current_answer(self):
        task = asyncio.create_task(asyncio.sleep(30))
        self.session.response_task = task
        self.session.responding, self.session.current_kind = True, 'voice'
        self.session.enqueue = Mock()
        await self.session.submit_direct(Question(10, 'Owner', 'А почему?', time.monotonic()))
        self.assertFalse(task.cancelled())
        self.assertIs(self.session.response_task, task)

    async def test_duplicate_names_require_category_and_preserve_message(self):
        self.channels[2].name = 'Общий'
        self.channels[1].category = NS(name='Игры')
        self.channels[2].category = NS(name='Беседы')
        decision = self.prepare('Перейди в канал Общий, скажи: Привет, и вернись')
        self.assertIsNone(decision.job)
        self.assertIn('одинаковые', decision.reply)
        decision = self.prepare('Беседы Общий')
        self.assertEqual(decision.job.target.id, 33)
        self.assertEqual(decision.job.message, 'Привет')
        self.assertTrue(decision.job.return_back)

    async def test_missing_destination_and_message_are_collected_from_same_caller(self):
        self.assertIn('какой', self.prepare('Можешь передать сообщение?').reply)
        self.assertFalse(self.routes.can_continue(self.session, 20, 'Общий'))
        self.assertIn('текст', self.prepare('Общий').reply)
        decision = self.prepare('Скажи: Играем через минуту')
        self.assertEqual(decision.job.message, 'Играем через минуту')
        self.assertEqual(decision.job.target.id, 22)

    async def test_ambient_and_foreign_channel_cannot_dispatch_errand(self):
        self.assertIsNone(self.prepare('Перейди в канал Общий', kind='ambient'))
        self.assertIsNone(self.prepare('Перейди в канал Общий', uid=20).job)

    async def test_missing_permissions_and_full_channel_cannot_dispatch(self):
        self.channels[1].permissions.connect = False
        self.assertIn('прав', self.prepare('Перейди в канал Общий').reply)
        self.channels[1].permissions.connect = True
        self.channels[1].members = [NS(id=99)]
        self.channels[1].user_limit = 1
        self.assertIn('заполнен', self.prepare('Перейди в канал Общий').reply)

    async def test_person_lookup_uses_current_voice_channel(self):
        member = NS(id=20, bot=False, display_name='Паша', name='pink', voice=NS(channel=self.channels[2]))
        self.channels[2].members.append(member)
        self.assertEqual(self.prepare('Зайди к Паше').job.target.id, 33)

    async def test_unknown_person_can_be_clarified_by_nickname(self):
        member = NS(id=20, bot=False, display_name='PinkGuy', name='pink', voice=NS(channel=self.channels[2]))
        self.channels[2].members.append(member)
        decision = self.prepare('Зайди к Паше, скажи: Мы готовы, и вернись')
        self.assertIsNone(decision.job)
        decision = self.prepare('PinkGuy')
        self.assertEqual(decision.job.target.id, 33)
        self.assertEqual(decision.job.message, 'Мы готовы')
        self.assertTrue(decision.job.return_back)

    async def test_failed_connection_returns_and_does_not_claim_delivery(self):
        real_join = self.bot.join_channel
        async def fail_target(channel):
            if channel.id == 22:
                self.guild.voice_client = None
                raise TimeoutError
            await real_join(channel)
        self.bot.join_channel = fail_target
        await self.execute(self.prepare('Перейди в канал Общий, скажи: Привет, и вернись'))
        self.assertEqual(self.joined, [11])
        self.assertFalse(any('передал' in text for _, text in self.spoken))

    async def test_hush_cancels_delivery_but_roundtrip_can_return(self):
        started = asyncio.Event()
        async def blocked_say(guild, text):
            started.set()
            await asyncio.Event().wait()
        self.routes.say = blocked_say
        self.routes.start(self.prepare('Перейди в канал Общий, скажи: Привет, и вернись').job)
        task = self.routes.active[1][0]
        await asyncio.wait_for(started.wait(), 1)
        self.routes.cancel(1, restore=True)
        await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(self.joined, [22, 11])

    async def test_external_kick_cancels_without_forcing_return(self):
        started = asyncio.Event()
        async def blocked_say(guild, text):
            started.set()
            await asyncio.Event().wait()
        self.routes.say = blocked_say
        self.routes.start(self.prepare('Перейди в канал Общий, скажи: Привет, и вернись').job)
        task = self.routes.active[1][0]
        await asyncio.wait_for(started.wait(), 1)
        self.routes.cancel(1)
        self.guild.voice_client = None
        await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(self.joined, [22])

    async def test_session_acknowledges_before_moving_and_does_not_call_llm(self):
        future = asyncio.get_running_loop().create_future()
        self.session.enqueue(Question(10, 'Owner', 'Перейди в канал Общий', time.monotonic(), kind='text', future=future))
        self.assertIn('перехожу', await asyncio.wait_for(future, 2))
        task = self.routes.active[1][0]
        await task
        self.assertEqual(self.joined, [22])
