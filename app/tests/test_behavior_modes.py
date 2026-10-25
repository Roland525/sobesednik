import asyncio
import logging
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from bot.config import Config
from bot.session import GuildSession
from bot.voice.transcription_sink import Utterance
from bot.desktop_control import DesktopControl
from desktop.protocol import Protocol


class BehaviorModeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):self.sessions=[]

    async def asyncTearDown(self):
        for session in self.sessions:await session.close()

    def make_session(self,mode='participant',config=None):
        class LLM:
            def __init__(self):self.participate=AsyncMock(return_value='');self.calls=0
            async def stream_sentences(self,messages,timings):
                self.calls+=1;yield 'Короткие волны света рассеиваются сильнее.'
        stt=NS(text='',last_ms={})
        async def transcribe(*args,**kwargs):return stt.text
        stt.transcribe=transcribe
        state=GuildSession(1,config or Config(conversation_mode=mode,initiative_enabled=False),stt,LLM(),
            NS(generate_audio=AsyncMock(return_value=bytes(3840))),logging.getLogger(),logging.getLogger())
        state.play_pcm=AsyncMock();self.sessions.append(state);return state

    async def hear(self,state,text,uid=1):
        state.start();state.stt.text=text;now=time.monotonic()
        state.audio_queue.put_nowait(Utterance(NS(id=uid,display_name='Synthetic speaker'),b'a',now,now,now,0))
        await asyncio.wait_for(state.audio_queue.join(),2)
        await asyncio.wait_for(state.questions.join(),2)

    async def test_participant_answers_a_clear_public_question_without_name_or_optional_comment_filter(self):
        state=self.make_session()
        await self.hear(state,'Почему небо голубое?')
        state.tts.generate_audio.assert_awaited_once()
        self.assertEqual(state.llm.calls,1)
        state.llm.participate.assert_not_awaited()
        self.assertEqual(state.journal.snapshot()[-1]['status'],'answered')
        self.assertEqual(state.journal.snapshot()[-1]['reason'],'public_question')
        self.assertEqual(state.dialogue_owner,1)
        self.assertIn('рассеиваются',state.conversation.history[-1]['content'])

    async def test_other_modes_do_not_turn_every_public_question_into_a_direct_answer(self):
        for mode in ('strict','summon','rare'):
            with self.subTest(mode=mode):
                state=self.make_session(mode)
                await self.hear(state,'Почему небо голубое?')
                state.tts.generate_audio.assert_not_awaited();self.assertEqual(state.llm.calls,0)

    async def test_optional_comment_rejection_is_not_left_working_forever(self):
        state=self.make_session()
        await self.hear(state,'Мы обсуждаем настройки нового рабочего микрофона')
        state.llm.participate.assert_awaited_once()
        self.assertEqual(state.journal.snapshot()[-1]['status'],'ignored')
        self.assertEqual(state.journal.snapshot()[-1]['reason'],'no_useful_comment')

    async def test_apply_behavior_updates_existing_and_future_sessions_without_resetting_context_or_unmuting(self):
        config=Config(conversation_mode='strict',initiative_enabled=False)
        states=[self.make_session(config=config),self.make_session(config=config)]
        states[0].conversation.history.append({'role':'user','content':'Synthetic saved context'})
        states[0].muted=True;states[0].quiet_mode=True
        bot=NS(config=config,sessions={1:states[0],2:states[1]})
        settings={'conversation_mode':'participant','followup_enabled':True,'interrupt_enabled':True}
        with tempfile.TemporaryDirectory() as folder:
            protocol=Protocol(Path(folder))
            protocol.request('apply_behavior',{'settings':settings})
            control=DesktopControl(bot,Path(folder))
            await control.dispatch('apply_behavior',{'settings':settings})
            self.assertEqual(config.conversation_mode,'participant')
            for state in states:
                self.assertTrue(state.participant_mode);self.assertFalse(state.require_wake_each_turn)
            self.assertTrue(states[0].muted);self.assertTrue(states[0].quiet_mode)
            self.assertEqual(states[0].conversation.history[0]['content'],'Synthetic saved context')
            fresh=self.make_session(config=config);self.assertTrue(fresh.participant_mode)
            await control.dispatch('apply_behavior',{'settings':{**settings,'conversation_mode':'strict'}})
            self.assertTrue(states[1].require_wake_each_turn);self.assertFalse(states[1].participant_mode)

    async def test_behavior_command_rejects_unknown_keys_and_invalid_values_atomically(self):
        config=Config(conversation_mode='strict')
        state=self.make_session(config=config)
        settings={'conversation_mode':'participant','followup_enabled':True,'interrupt_enabled':True}
        with tempfile.TemporaryDirectory() as folder:
            control=DesktopControl(NS(config=config,sessions={1:state}),Path(folder))
            for invalid in ({**settings,'ollama_model':'anything'}, {**settings,'conversation_mode':'anything'},
                            {**settings,'followup_enabled':'true'}):
                with self.subTest(invalid=invalid),self.assertRaises(ValueError):
                    await control.dispatch('apply_behavior',{'settings':invalid})
                self.assertEqual(config.conversation_mode,'strict');self.assertTrue(state.require_wake_each_turn)

    def test_public_question_filter_rejects_noise_statements_and_personal_questions(self):
        from bot.conversation import public_question
        for text in ('Почему небо голубое?','Кто знает, как настроить микрофон?','Как правильно настроить звук?'):
            self.assertTrue(public_question(text),text)
        for text in ('Да да да','Как всегда было очень весело.','Как у тебя дела сегодня?',
                     'Я подумал как его настроить.','Паша, почему небо голубое?',
                     'Как же я устал от этой игры.','Какой красивый сегодня закат.'):
            self.assertFalse(public_question(text),text)

    async def test_question_addressed_to_another_member_is_not_a_public_question(self):
        state=self.make_session()
        state.voice_client=NS(channel=NS(members=[NS(id=2,bot=False,display_name='Паша',name='pasha')]))
        try:
            await self.hear(state,'Почему небо голубое, Паша?')
            state.tts.generate_audio.assert_not_awaited()
            state.llm.participate.assert_not_awaited()
            self.assertEqual(state.llm.calls,0)
        finally:state.voice_client=None

    async def test_switching_mode_cancels_optional_generation_and_keeps_direct_dialogue(self):
        entered=asyncio.Event();cancelled=asyncio.Event()
        state=self.make_session()
        async def decide(*args,**kwargs):
            entered.set()
            try:await asyncio.Event().wait()
            finally:cancelled.set()
        state.llm.participate=decide
        state.conversation.history.append({'role':'user','content':'Synthetic direct dialogue'})
        state.start();state.stt.text='Мы обсуждаем настройки нашего нового рабочего микрофона'
        now=time.monotonic();state.audio_queue.put_nowait(Utterance(NS(id=1,display_name='Speaker'),b'a',now,now,now,0))
        await asyncio.wait_for(entered.wait(),1)
        await state.apply_behavior({'conversation_mode':'strict','followup_enabled':True,'interrupt_enabled':True})
        self.assertTrue(cancelled.is_set())
        self.assertEqual(state.conversation.history[0]['content'],'Synthetic direct dialogue')
        self.assertFalse(state.participant_mode)

    async def test_hush_cancels_public_answer_and_unaddressed_speech_cannot_restart_it(self):
        entered=asyncio.Event();cancelled=asyncio.Event()
        state=self.make_session()
        async def blocked_stream(*args,**kwargs):
            entered.set()
            try:await asyncio.Event().wait()
            finally:cancelled.set()
            yield 'Never spoken.'
        state.llm.stream_sentences=blocked_stream
        state.start();state.stt.text='Почему небо голубое?'
        now=time.monotonic();state.audio_queue.put_nowait(Utterance(NS(id=1,display_name='Speaker'),b'a',now,now,now,0))
        await asyncio.wait_for(entered.wait(),1)
        await state.hush()
        self.assertTrue(cancelled.is_set());self.assertTrue(state.muted)
        await self.hear(state,'Как правильно настроить микрофон?')
        state.tts.generate_audio.assert_not_awaited()

    async def test_named_question_respects_live_interrupt_setting_during_public_answer(self):
        state=self.make_session()
        entered=asyncio.Event();cancelled=asyncio.Event()
        async def blocked_stream(*args,**kwargs):
            entered.set()
            try:await asyncio.Event().wait()
            finally:cancelled.set()
            yield 'Never spoken.'
        state.llm.stream_sentences=blocked_stream;state.start();state.stt.text='Почему небо голубое?'
        now=time.monotonic();state.audio_queue.put_nowait(Utterance(NS(id=1,display_name='Speaker'),b'a',now,now,now,0))
        await asyncio.wait_for(entered.wait(),1)
        await state.apply_behavior({'conversation_mode':'participant','followup_enabled':True,'interrupt_enabled':False})
        state.stt.text='Собеседник, почему на закате небо красное?'
        now=time.monotonic();state.audio_queue.put_nowait(Utterance(NS(id=2,display_name='Other'),b'a',now,now,now,0))
        await asyncio.wait_for(state.audio_queue.join(),1)
        self.assertFalse(cancelled.is_set(),'Turning interruptions off must preserve the current answer')
        self.assertEqual(state.questions.qsize(),1)

    async def test_public_questions_have_cooldown_but_named_questions_keep_priority(self):
        state=self.make_session()
        await self.hear(state,'Почему небо голубое?',uid=1)
        await self.hear(state,'Как правильно настроить микрофон?',uid=2)
        self.assertEqual(state.llm.calls,1)
        await self.hear(state,'Собеседник, как правильно настроить микрофон?',uid=2)
        self.assertEqual(state.llm.calls,2)

    async def test_optional_turn_waiting_behind_a_recent_reply_gets_explicit_cooldown_status(self):
        from bot.session import Question
        state=self.make_session();state.last_reply_at=time.monotonic()
        state.enqueue(Question(1,'Speaker','Мы обсуждаем настройки нового микрофона',time.monotonic(),kind='ambient'))
        await asyncio.wait_for(state.questions.join(),1)
        self.assertEqual(state.journal.snapshot()[-1]['status'],'ignored')
        self.assertEqual(state.journal.snapshot()[-1]['reason'],'ambient_cooldown')
