import asyncio,logging,time,unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from bot.config import Config
from bot.session import GuildSession,Question
from bot.voice.transcription_sink import Utterance

class TurnTakingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.stt=NS(transcribe=AsyncMock(),last_ms={})
        self.session=GuildSession(1,Config(conversation_mode='strict'),self.stt,NS(accept_followup=AsyncMock(return_value=True)),None,logging.getLogger(),logging.getLogger())
        self.captured=[];self.session.enqueue=self.captured.append
        self.worker=asyncio.create_task(self.session.recognize())
    async def asyncTearDown(self):
        self.worker.cancel();await asyncio.gather(self.worker,return_exceptions=True)
        self.session.voice_client=None;await self.session.close()
    async def hear(self,uid,text,started=None):
        self.stt.transcribe.return_value=text;t=time.monotonic()
        await self.session.audio_queue.put(Utterance(NS(id=uid,display_name='Tester'),b'a',started or t,t,t,0))
        await asyncio.wait_for(self.session.audio_queue.join(),2)
    async def test_only_current_speaker_can_follow_up_and_setting_can_disable_it(self):
        await self.hear(1,'Серёга, почему небо голубое?')
        self.session.conversation.history.append({'role':'assistant','content':'Из-за рассеяния света.'})
        self.session.conversation.replied_to(1)
        await self.hear(2,'А почему вечером красное?')
        await self.hear(1,'А почему вечером красное?')
        self.assertEqual(len(self.captured),2)
        self.assertEqual(self.captured[-1].route_reason,'followup')
        self.session.config.followup_enabled=False
        await self.hear(1,'А почему утром розовое?')
        self.assertEqual(len(self.captured),2)
    async def test_unfinished_question_is_combined_before_first_playback(self):
        ended=time.monotonic()-.4
        self.session.current_question=Question(1,'Tester','Расскажи про планеты',ended)
        self.session.dialogue_owner=1
        await self.hear(1,'которые находятся рядом с Солнцем',started=ended+.1)
        self.assertEqual(self.captured[-1].route_reason,'continuation')
        self.assertIn('Расскажи про планеты',self.captured[-1].text)
        self.assertIn('рядом с Солнцем',self.captured[-1].text)
    async def test_speech_start_waits_for_owner_to_finish(self):
        speaker=NS(frames=[b'a'],last_voice=time.monotonic())
        self.session.sink=NS(speakers={1:speaker})
        async def finish():await asyncio.sleep(.07);speaker.frames=[]
        task=asyncio.create_task(finish());start=time.monotonic()
        self.assertTrue(await self.session.wait_for_speaker(Question(1,'Tester','Question',time.monotonic())))
        self.assertGreaterEqual(time.monotonic()-start,.06);await task;self.session.sink=None

    async def test_deferred_player_cancels_producer_instead_of_blocking_full_audio_queue(self):
        async def stream(*args):
            for _ in range(20):yield 'Synthetic sentence.'
        self.session.llm.stream_sentences=stream
        self.session.tts=NS(generate_audio=AsyncMock(return_value=bytes(3840)))
        self.session.wait_for_speaker=AsyncMock(return_value=False)
        result=await asyncio.wait_for(self.session.speak_stream(Question(1,'Tester','Question',time.monotonic()),[]),.2)
        self.assertEqual(result,'')

    async def test_address_to_another_person_does_not_inherit_reply_window(self):
        self.session.voice_client=NS(channel=NS(members=[NS(id=2,display_name='Паша',name='pasha',bot=False)]))
        await self.hear(1,'Серёга, почему небо голубое?')
        self.session.conversation.history.append({'role':'assistant','content':'Из-за рассеяния света.'})
        self.session.conversation.replied_to(1)
        await self.hear(1,'Паша, а почему вечером красное?')
        self.assertEqual(len(self.captured),1)
