import asyncio
import heapq
from dataclasses import dataclass, field
from difflib import SequenceMatcher
import logging
import json
import re
import time
from collections import deque
import discord

from .conversation import Conversation, addressed, likely_followup
from .voice.transcription_sink import TranscriptionSink
from .controls import voice_control, addresses_someone_else, screen_request, SCREEN_UNAVAILABLE
from .security import private_request_reply, computer_action_reply, safe_output
from .factuality import freshness_reply
from .services.dota_patch_source import DotaPatchSource
from .diagnostics import TurnTrace
from .memory import MEMORY_OPERATION, memory_request
from .voice.lifecycle import close_voice_client


class MemoryPCM(discord.AudioSource):
    """Discord's audio thread reads exactly 20 ms of stereo PCM48 per call."""
    def __init__(self, pcm):
        self.pcm, self.offset = pcm, 0

    def read(self):
        frame = self.pcm[self.offset:self.offset + 3840]
        self.offset += 3840
        return frame.ljust(3840, b'\x00') if frame else b''

    def is_opus(self):
        return False

    def cleanup(self):
        self.pcm = b''


@dataclass
class Question:
    user_id: int
    name: str
    text: str
    ended: float
    timings: dict = field(default_factory=dict)
    kind: str = 'voice'
    future: object = None
    enqueued_at: float = field(default_factory=time.monotonic)


class GuildSession:
    def __init__(self, guild_id, config, stt, llm, tts, logger, metrics, memory=None, voice_routes=None, learning=None):
        self.guild_id, self.config = guild_id, config
        self.stt, self.llm, self.tts = stt, llm, tts
        self.logger, self.metrics = logger, metrics
        self.memory = memory
        self.voice_routes = voice_routes
        self.learning = learning
        self.conversation = Conversation(config)
        self.channel_conversation = Conversation(config)
        self.dota_patches = DotaPatchSource(logger)
        self.trace = TurnTrace(config)
        self.audio_queue = asyncio.Queue(maxsize=config.max_audio_queue_size)
        self.control_queue = asyncio.Queue(maxsize=1)
        self.questions = asyncio.PriorityQueue(maxsize=config.max_audio_queue_size)
        self.voice_client = self.sink = None
        self.tasks = []
        self.response_task = None
        self.recent_spoken = []
        self.last_metrics = {}
        self.last_error = ''
        self.playing_since = 0
        self.last_play_end = 0
        self.closed = False
        self.generation = 0
        self.rare_mode = config.conversation_mode == 'rare'
        self.participant_mode = config.conversation_mode in ('participant', 'rare')
        self.require_wake_each_turn = config.conversation_mode == 'strict'
        self.ambient_context = deque(maxlen=4)
        self.last_unsolicited_at = 0.0
        self.current_kind = None
        self.dialogue_owner = None
        self.awaiting_question = None
        self.ambient_pending = False
        self.last_reply_at = 0.0
        self.sequence = 0
        self.last_ambient_checked = 0.0
        self.last_human_at = 0.0
        self.last_initiative_at = 0.0
        self.joined_at = time.monotonic()
        self.initiative_armed = True
        self.initiative_pending = False
        self.responding = False
        self.muted = False
        self.quiet_mode = False
        self.recent_inputs = {}
        self.greetings = {}
        self.guard_counts = {'echo': 0, 'duplicate': 0, 'superseded': 0, 'fast_stop': 0,
                             'noise': 0, 'social': 0}
        self.bound_channel_id = None

    def start(self):
        if not self.tasks:
            self.tasks = [asyncio.create_task(self.recognize(), name=f'stt-{self.guild_id}'),
                          asyncio.create_task(self.recognize_controls(), name=f'controls-{self.guild_id}'),
                          asyncio.create_task(self.initiative_loop(), name=f'initiative-{self.guild_id}')]
            self.response_task = asyncio.create_task(self.respond(), name=f'reply-{self.guild_id}')

    async def attach(self, vc, target_user_id=None):
        await self.detach_capture()
        self.voice_client = vc
        self.refresh_memory_members()
        self.joined_at = time.monotonic()
        self.sink = TranscriptionSink(self.config, self.audio_queue, self.logger, target_user_id,
                                      control_callback=self.offer_control_audio)
        self.initiative_armed = True
        self.tasks.append(asyncio.create_task(self.sink.pump(), name=f'capture-{self.guild_id}'))
        vc.listen(self.sink)
        self.bound_channel_id = getattr(getattr(vc, 'channel', None), 'id', None)

    def refresh_memory_members(self):
        if self.memory:
            try:
                for member in getattr(getattr(self.voice_client, 'channel', None), 'members', ()):
                    if not getattr(member, 'bot', False):
                        self.memory.register(self.guild_id, member.id, member.display_name, getattr(member, 'name', ''))
            except Exception as error:
                self.logger.warning('Не удалось обновить имена в памяти: %s', type(error).__name__)

    async def detach_capture(self):
        if self.voice_client and self.voice_client.is_listening():
            self.voice_client.stop_listening()
        if self.sink:
            self.sink.cleanup()
            self.sink.clear_buffers()
        capture = [task for task in self.tasks if task.get_name().startswith('capture-')]
        for task in capture:
            task.cancel()
        await asyncio.gather(*capture, return_exceptions=True)
        self.tasks = [task for task in self.tasks if task not in capture]
        self.sink = None

    def is_echo(self, text, utterance):
        now = time.monotonic()
        normalized = re.sub(r'\W', '', text.lower().replace('ё', 'е'))
        self.recent_spoken = [item for item in self.recent_spoken
                              if now - item[1] < self.config.max_audio_age + 30][-24:]
        overlapping = []
        for item in self.recent_spoken:
            words, started = item[:2]
            ended = item[2] if len(item) > 2 else (now if self.playing_since else self.last_play_end)
            if utterance.started <= ended + self.config.echo_guard_seconds and utterance.ended >= started - .15:
                overlapping.append(words)
        if not normalized or not overlapping:
            return False
        joined = ''.join(overlapping)
        # Short greetings and partial microphone echoes were previously exempt.
        return bool(normalized == joined or len(normalized) >= 5 and normalized in joined or
                    any(SequenceMatcher(None, normalized, words).ratio() > .88 for words in overlapping))

    def offer_control_audio(self, member, pcm, ended):
        if self.closed or self.muted or not getattr(self.stt, 'recognize_control', None):
            return
        if self.control_queue.full():
            self.control_queue.get_nowait()
            self.control_queue.task_done()
        self.control_queue.put_nowait((member, pcm, ended, self.generation))

    async def recognize_controls(self):
        while True:
            member, pcm, ended, generation = await self.control_queue.get()
            try:
                if time.monotonic() - ended > 2 or generation != self.generation:
                    continue
                command = await self.stt.recognize_control(pcm)
                if command == 'mute' and generation == self.generation:
                    self.guard_counts['fast_stop'] += 1
                    await self.hush()
            except asyncio.CancelledError:
                raise
            except Exception:
                self.logger.exception('Ошибка быстрого распознавания команды guild=%s', self.guild_id)
            finally:
                self.control_queue.task_done()

    async def recognize(self):
        while True:
            utterance = await self.audio_queue.get()
            try:
                if time.monotonic() - utterance.ended > self.config.max_audio_age:
                    self.logger.warning('Пропущена устаревшая фраза STT')
                    continue
                uid = utterance.member.id
                generation = self.generation
                active = self.muted or (not self.require_wake_each_turn and
                                       (self.conversation.follows_up(uid) or self.responding))
                start = time.monotonic()
                text = await self.stt.transcribe(utterance.pcm, active=active or self.participant_mode or self.trace.active)
                if generation != self.generation:
                    continue
                if not text:
                    if self.stt.last_ms.get('noise_rejected'):
                        self.guard_counts['noise'] += 1
                        self.metrics.info('guild=%s speech_gate=noise', self.guild_id)
                    continue
                hit, _ = addressed(text, self.config.wake_word)
                control = voice_control(text, self.config.wake_word, self.muted)
                self.trace.record('heard', user=uid, text=text[:800], named=hit, control=control,
                                  echo=self.is_echo(text, utterance), duration=round(utterance.ended-utterance.started, 2))
                if control == 'mute':
                    await self.hush()
                    continue
                if control == 'unmute':
                    if not hit:
                        continue
                    self.muted = False
                    self.quiet_mode = False
                    self.initiative_armed = True
                    self.conversation.replied_to(uid)
                    self.enqueue(Question(uid, utterance.member.display_name, 'Слушаю.', utterance.ended, kind='say'))
                    continue
                if self.require_wake_each_turn and not hit and not self.rare_mode:
                    continue
                if self.is_echo(text, utterance):
                    self.guard_counts['echo'] += 1
                    continue
                normalized = re.sub(r'\W', '', text.lower().replace('ё', 'е'))
                now = time.monotonic()
                self.recent_inputs = {key: value for key, value in self.recent_inputs.items() if now - value[1] < 12}
                previous = self.recent_inputs.get(uid)
                if not hit and previous and previous[0] == normalized:
                    self.guard_counts['duplicate'] += 1
                    continue
                self.recent_inputs[uid] = (normalized, now)
                if self.memory:
                    try:
                        _, memory_text = addressed(text, self.config.wake_word)
                        self.memory.observe(self.guild_id, uid, utterance.member.display_name, memory_text,
                                            getattr(utterance.member, 'name', ''))
                    except Exception as error:
                        self.logger.warning('Не удалось сохранить сведения участника: %s', type(error).__name__)
                if self.muted:
                    if not hit:
                        continue
                    self.muted = False
                    if self.rare_mode:
                        self.quiet_mode = False
                        self.last_unsolicited_at = now
                if self.learning:
                    try:
                        _, learning_text = addressed(text, self.config.wake_word)
                        notice = self.learning.heard(self.guild_id, uid, learning_text)
                        if notice:
                            if hit or self.conversation.follows_up(uid):
                                self.enqueue(Question(uid, utterance.member.display_name, notice, utterance.ended, kind='say'))
                            continue
                    except Exception as error:
                        self.logger.warning('Не удалось сохранить учебный пример: %s', type(error).__name__)
                if self.ignore_social_signal(uid, text, hit, now):
                    self.guard_counts['social'] += 1
                    continue
                self.last_human_at = now
                self.initiative_armed = not self.quiet_mode
                members = getattr(getattr(self.voice_client, 'channel', None), 'members', ())
                names = [name for member in members if not member.bot and member.id != uid
                         for name in (getattr(member, 'display_name', ''), getattr(member, 'name', ''))]
                if hit:
                    self.dialogue_owner = uid
                memory_answer = False
                if self.voice_routes:
                    memory_answer = self.voice_routes.can_continue(self, uid, text)
                if self.memory:
                    try:
                        self.refresh_memory_members()
                        memory_answer = memory_answer or self.memory.can_resolve_pending(self.guild_id, uid, text)
                    except Exception as error:
                        self.logger.warning('Не удалось проверить уточнение памяти: %s', type(error).__name__)
                followup = (not self.require_wake_each_turn and self.conversation.follows_up(uid)
                            and (self.participant_mode and not self.rare_mode or self.dialogue_owner in (None, uid))
                            and (memory_answer or not addresses_someone_else(text, names, self.config.wake_word)))
                if followup and not hit and (self.rare_mode or not self.participant_mode):
                    greeting = re.fullmatch(r'(?:привет|здравствуй|здравствуйте)[.! ]*', text.lower())
                    waiting = self.awaiting_question == uid and (greeting or likely_followup(text, self.conversation.history))
                    followup = waiting or memory_answer or likely_followup(text, self.conversation.history)
                    if followup and not waiting and not memory_answer and getattr(self.llm, 'accept_followup', None):
                        followup = await self.llm.accept_followup(self.conversation.messages(uid, utterance.member.display_name, text))
                        if generation != self.generation:
                            continue
                question = self.conversation.accept(uid, text) if hit or followup else None
                timings = {**self.stt.last_ms, 'vad_ms': utterance.vad_ms,
                           'endpoint_ms': (utterance.queued - utterance.ended) * 1000,
                           'stt_queue_ms': (start - utterance.queued) * 1000}
                if question is not None:
                    if not question:
                        self.awaiting_question = uid
                        continue
                    self.awaiting_question = None
                    if self.rare_mode and hit and self.current_kind == 'ambient' and self.response_task:
                        self.response_task.cancel()
                        await asyncio.gather(self.response_task, return_exceptions=True)
                        if not self.closed:
                            self.response_task = asyncio.create_task(self.respond(), name=f'reply-{self.guild_id}')
                    self.metrics.info('guild=%s turn=direct named=%s chars=%s', self.guild_id, hit, len(question))
                    await self.submit_direct(Question(uid, utterance.member.display_name, question, utterance.ended, timings))
                elif self.participant_mode and not self.quiet_mode:
                    if self.rare_mode:
                        if not self.rare_candidate(text, now, names):
                            self.observe_ambient(uid, utterance.member.display_name, text)
                            continue
                        self.last_ambient_checked = now
                    if (self.ambient_pending or time.monotonic() - self.last_reply_at < self.config.ambient_cooldown
                        or (not self.rare_mode and time.monotonic() - self.last_ambient_checked < 4)
                        or len(text.split()) < 3):
                        self.observe_ambient(uid, utterance.member.display_name, text)
                    else:
                        self.enqueue(Question(uid, utterance.member.display_name, text, utterance.ended, timings, kind='ambient'))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = type(exc).__name__
                self.logger.exception('Ошибка распознавания guild=%s', self.guild_id)
            finally:
                self.audio_queue.task_done()

    def context_for(self, kind):
        return self.channel_conversation if self.rare_mode and kind.startswith('ambient') else self.conversation

    def memory_turn(self, question):
        if question.kind not in ('voice', 'text'):
            return '', ''
        if not self.memory:
            if self.learning and re.fullmatch(r'(?:забудь (?:вс[её] )?обо мне|удали память обо мне|забудь вс[её])\s*[.!?]*', question.text, re.I):
                self.learning.forget(self.guild_id, question.user_id)
                return 'Удалил связанные с тобой учебные примеры. Уже обученную версию потребуется переобучить.', ''
            if memory_request(question.text) is not None or re.match(r'^забудь\b', question.text, re.I):
                return 'Постоянная память сейчас недоступна.' if self.config.memory_enabled else 'Постоянная память выключена.', ''
            return '', ''
        try:
            self.refresh_memory_members()
            result = self.memory.handle(self.guild_id, question.user_id, question.name, question.text)
            if result.forget:
                if self.learning:
                    self.learning.forget(self.guild_id, question.user_id)
                self.conversation.history.clear()
                self.channel_conversation.history.clear()
                self.ambient_context.clear()
            context = self.memory.context(self.guild_id, question.user_id, question.text)
            data = json.loads(context)
            if not data['notes'] and not any(p['real_name'] or p['banter_optout'] for p in data['people']):
                context = ''
            return result.reply, context
        except Exception as error:
            self.logger.warning('Память недоступна: %s', type(error).__name__)
            if memory_request(question.text) is not None or re.match(r'^забудь\b', question.text, re.I):
                return 'Не смог обновить постоянную память. Попробуй ещё раз позже.', ''
            return '', ''

    def observe_ambient(self, uid, name, text):
        if not self.rare_mode or len(text.split()) >= 6:
            self.context_for('ambient').observe(uid, name, text)

    def rare_candidate(self, text, now, names):
        if len(text.split()) < 6 or addresses_someone_else(text, names, self.config.wake_word):
            return False
        self.ambient_context.append((now, text))
        recent = [value for when, value in self.ambient_context if now - when < 45]
        return (len(recent) >= 2 and sum(len(value.split()) for value in recent) >= 16
                and re.search(r'как|почему|не могу|не получается|не понимаю|проблем|спор|кто знает|интересно|неясно|непонятно|застрял', text.lower())
                and not freshness_reply(text) and not self.ambient_pending
                and not private_request_reply(text) and not computer_action_reply(text) and not screen_request(text)
                and now - self.last_ambient_checked >= 30
                and now - max(self.last_unsolicited_at, self.last_reply_at) >= self.config.rare_cooldown)

    def ignore_social_signal(self, uid, text, hit, now):
        _, body = addressed(text, self.config.wake_word)
        words = re.findall(r'\w+', body.lower().replace('ё', 'е'))
        if not words or '?' in body:
            return False
        if all(word in ('привет', 'приветик', 'здарова', 'здорово', 'здравствуй',
                        'здравствуйте', 'хай', 'ну', 'еще', 'раз') for word in words):
            self.greetings = {key: when for key, when in self.greetings.items() if now - when < 120}
            if uid in self.greetings:
                return True
            self.greetings[uid] = now
        if not hit and all(word in ('да', 'угу', 'ага', 'понятно', 'ясно', 'ладно', 'ок',
                                   'окей', 'хорошо', 'понял', 'поняла') for word in words):
            last_bot = next((item['content'] for item in reversed(self.conversation.history)
                             if item['role'] == 'assistant'), '')
            # A real answer to our question can be one word. A backchannel
            # after a statement does not need another acknowledgement.
            return not last_bot.rstrip().endswith('?')
        return False

    async def submit_direct(self, question):
        # A requested channel action must not wait behind a previous LLM answer.
        # Only explicit commands preempt; ordinary followups keep normal turn-taking.
        command = (memory_request(question.text) is not None
                   or bool(self.voice_routes and self.voice_routes.is_command(question.text)))
        if command and self.responding and self.current_kind not in ('say', 'safe_say') and self.response_task:
            self.response_task.cancel()
            await asyncio.gather(self.response_task, return_exceptions=True)
            if self.closed:
                return
            self.response_task = asyncio.create_task(self.respond(), name=f'reply-{self.guild_id}')
            question.timings['command_preempted_reply'] = 1
        self.enqueue(question)

    def enqueue(self, question):
        self.trace.record('queued', user=question.user_id, kind=question.kind, text=question.text[:800])
        self.start()
        if question.kind == 'voice':
            # Keep one pending turn per speaker, not a backlog of old greetings.
            for item in list(self.questions._queue):
                old = item[2]
                if (old.kind == 'voice' and old.user_id == question.user_id
                    and memory_request(old.text) is None
                    and not (self.voice_routes and self.voice_routes.is_command(old.text))):
                    self.questions._queue.remove(item)
                    self.questions.task_done()
                    if old.future and not old.future.done():
                        old.future.cancel()
                    self.guard_counts['superseded'] += 1
            heapq.heapify(self.questions._queue)
        question.enqueued_at = time.monotonic()
        self.sequence += 1
        try:
            priority = 20 if question.kind == 'initiative' else (10 if question.kind == 'ambient' else 0)
            self.questions.put_nowait((priority, self.sequence, question))
            if question.kind == 'ambient':
                self.ambient_pending = True
            if question.kind == 'initiative':
                self.initiative_pending = True
        except asyncio.QueueFull:
            self.logger.warning('Очередь ответов заполнена guild=%s', self.guild_id)
            if question.future and not question.future.done():
                question.future.set_exception(RuntimeError('Очередь заполнена. Попробуй чуть позже.'))

    async def play_pcm(self, pcm, question, text):
        vc = self.voice_client
        if not vc or not vc.is_connected():
            raise RuntimeError('Голосовой канал отключён')
        loop = asyncio.get_running_loop()
        done = loop.create_future()
        def complete(error):
            if not done.done():
                done.set_exception(error) if error else done.set_result(None)
        source = MemoryPCM(pcm)
        try:
            self.playing_since = time.monotonic()
            self.recent_spoken.append((re.sub(r'\W', '', text.lower().replace('ё', 'е')),
                                       self.playing_since, self.playing_since + len(pcm) / 192000))
            vc.play(source, after=lambda error: loop.call_soon_threadsafe(complete, error))
            self.trace.record('play', kind=question.kind, text=text[:800])
            question.timings.setdefault('first_playback_ms', (time.monotonic() - question.ended) * 1000)
            async with asyncio.timeout(len(pcm) / 192000 + 10):
                await done
        except BaseException:
            vc.stop_playing()
            raise
        finally:
            self.last_play_end = time.monotonic()
            if self.recent_spoken:
                last = self.recent_spoken[-1]
                self.recent_spoken[-1] = (last[0], last[1], self.last_play_end)
            self.playing_since = 0
            source.cleanup()

    async def speak_stream(self, question, messages):
        audio = asyncio.Queue(maxsize=2)
        spoken = []
        response_start = time.monotonic()
        async def producer():
            if not question.text:
                await audio.put(None)
                return
            if question.kind in ('say', 'ambient_say', 'initiative_say', 'safe_say'):
                sentences = [question.text]
                for sentence in sentences:
                    sentence = safe_output(sentence, [self.config.discord_token])
                    started = time.monotonic()
                    pcm = await self.tts.generate_audio(sentence)
                    question.timings.setdefault('tts_ms', (time.monotonic() - started) * 1000)
                    await audio.put((pcm, sentence))
            else:
                async for sentence in self.llm.stream_sentences(messages, question.timings):
                    if (question.kind == 'initiative' and 'first_playback_ms' not in question.timings
                        and self.last_human_at > question.enqueued_at):
                        await audio.put(None)
                        return
                    started = time.monotonic()
                    pcm = await self.tts.generate_audio(sentence)
                    question.timings.setdefault('tts_ms', (time.monotonic() - started) * 1000)
                    question.timings.setdefault('tts_ready_ms', (time.monotonic() - response_start) * 1000)
                    await audio.put((pcm, sentence))
            await audio.put(None)
        async def player():
            while True:
                item = await audio.get()
                if item is None:
                    break
                pcm, sentence = item
                if self.rare_mode and question.kind == 'ambient_say':
                    last_voice = max((s.last_voice for s in self.sink.speakers.values()), default=0) if self.sink else 0
                    if (self.quiet_mode or self.muted or self.last_human_at > question.enqueued_at
                        or time.monotonic() - last_voice < self.config.ambient_pause
                        or (not self.questions.empty() and self.questions._queue[0][0] == 0)):
                        return
                await self.play_pcm(pcm, question, sentence)
                spoken.append(sentence)
        try:
            async with asyncio.TaskGroup() as group:
                group.create_task(producer())
                group.create_task(player())
        finally:
            # Keep only speech that was actually played if the stream failed.
            if spoken and question.kind != 'say':
                self.context_for(question.kind).commit(messages, ' '.join(spoken), include_user=question.kind not in ('initiative', 'initiative_say'))
        return ' '.join(spoken)

    async def respond(self):
        while True:
            _, _, question = await self.questions.get()
            pending_kind = question.kind
            self.current_kind = pending_kind
            self.responding = True
            try:
                if question.future and question.future.cancelled():
                    continue
                if self.muted and question.kind != 'text':
                    if question.future and not question.future.done():
                        question.future.set_result('Серёга молчит. Для продолжения — /unmute или обращение по имени.')
                    continue
                if (time.monotonic() - question.ended > min(8, self.config.max_audio_age)
                    and (question.kind == 'voice' or self.rare_mode and question.kind == 'ambient')):
                    continue
                if question.kind == 'ambient':
                    if self.quiet_mode:
                        continue
                    if time.monotonic() - self.last_reply_at < self.config.ambient_cooldown:
                        self.observe_ambient(question.user_id, question.name, question.text)
                        continue
                    # Wait for a natural pause and yield to explicit questions.
                    for _ in range(25):
                        if self.questions.qsize() and self.questions._queue[0][0] == 0:
                            break
                        last_voice = max((state.last_voice for state in self.sink.speakers.values()), default=0) if self.sink else 0
                        if time.monotonic() - last_voice >= self.config.ambient_pause:
                            break
                        await asyncio.sleep(.1)
                    else:
                        self.observe_ambient(question.user_id, question.name, question.text)
                        continue
                    if self.questions.qsize() and self.questions._queue[0][0] == 0:
                        self.observe_ambient(question.user_id, question.name, question.text)
                        continue
                self.conversation.extend(question.user_id)
                question.timings['reply_queue_ms'] = (time.monotonic() - question.enqueued_at) * 1000
                question.timings['before_llm_ms'] = (time.monotonic() - question.ended) * 1000
                route = self.voice_routes.prepare(self, question) if self.voice_routes else None
                memory_reply, memory_context = ('', '') if route else self.memory_turn(question)
                if question.kind == 'initiative':
                    if self.last_human_at > question.enqueued_at or not self.initiative_allowed(time.monotonic(), pending=True):
                        continue
                    messages = self.conversation.initiative_messages()
                else:
                    messages = self.context_for(question.kind).messages(question.user_id, question.name, question.text or '[обращение по имени]', memory_context=memory_context)
                if memory_reply:
                    # The stored record is retrieved separately as bounded data.
                    # Do not keep raw memory instructions in conversational history.
                    messages[-1] = {'role': 'user', 'content': MEMORY_OPERATION}
                fixed_reply = (route.reply if route else '') or memory_reply or private_request_reply(question.text) or computer_action_reply(question.text)
                fresh_reply = freshness_reply(question.text)
                if not fixed_reply and fresh_reply:
                    if self.config.dota_patch_source_enabled:
                        fixed_reply = await self.dota_patches.reply(question.text)
                    fixed_reply = fixed_reply or fresh_reply
                if not route and screen_request(question.text):
                    fixed_reply = SCREEN_UNAVAILABLE
                if self.learning and pending_kind in ('voice', 'text') and not fixed_reply:
                    self.learning.begin_reply(self.guild_id, question.user_id, question.text)
                if fixed_reply:
                    if question.kind == 'text':
                        response = fixed_reply
                        self.conversation.commit(messages, response)
                    else:
                        question.kind, question.text = 'safe_say', fixed_reply
                        response = await self.speak_stream(question, messages)
                elif question.kind == 'initiative':
                    reply = await self.llm.initiate(messages, question.timings)
                    last_voice = max((s.last_voice for s in self.sink.speakers.values()), default=0) if self.sink else 0
                    if (not reply or self.last_human_at > question.enqueued_at
                        or time.monotonic() - last_voice < 1 or not self.questions.empty()):
                        continue
                    question.text, question.kind = reply, 'initiative_say'
                    response = await self.speak_stream(question, messages)
                elif question.kind == 'ambient':
                    self.last_ambient_checked = time.monotonic()
                    if self.rare_mode:
                        reply = await self.llm.participate(messages, question.timings, conservative=True)
                    else:
                        reply = await self.llm.participate(messages, question.timings)
                    # If someone resumed speaking during the decision, save the
                    # context and wait for another opportunity instead of cutting in.
                    pause = self.config.ambient_pause if self.rare_mode else .35
                    speaking = self.sink and any(time.monotonic() - s.last_voice < pause for s in self.sink.speakers.values())
                    direct_pending = self.questions.qsize() and self.questions._queue[0][0] == 0
                    newer_speech = self.rare_mode and self.last_human_at > question.enqueued_at
                    repeated = self.rare_mode and any(SequenceMatcher(None, reply.lower(), item['content'].lower()).ratio() > .75
                        for item in [*self.conversation.history, *self.channel_conversation.history] if item['role'] == 'assistant')
                    if not reply or speaking or direct_pending or newer_speech or repeated:
                        self.observe_ambient(question.user_id, question.name, question.text)
                        continue
                    question.text, question.kind = reply, 'ambient_say'
                    response = await self.speak_stream(question, messages)
                elif question.kind == 'text':
                    parts = [part async for part in self.llm.stream_sentences(messages, question.timings)]
                    response = ' '.join(parts)
                    self.conversation.commit(messages, response)
                else:
                    response = await self.speak_stream(question, messages)
                if not response:
                    if pending_kind in ('ambient', 'initiative'):
                        continue
                    raise RuntimeError('Пустой ответ модели')
                self.last_error = ''
                if self.learning and pending_kind in ('voice', 'text') and not fixed_reply:
                    try:
                        self.learning.complete_reply(self.guild_id, question.user_id, question.text, response)
                    except Exception as error:
                        self.logger.warning('Не удалось учесть ответ для исправления: %s', type(error).__name__)
                self.last_reply_at = time.monotonic()
                if self.rare_mode and pending_kind == 'voice':
                    self.channel_conversation.commit(messages, response)
                if pending_kind == 'ambient':
                    self.last_unsolicited_at = self.last_reply_at
                if question.kind != 'say':
                    self.conversation.replied_to(question.user_id)
                self.last_metrics = dict(question.timings)
                self.metrics.info('guild=%s %s', self.guild_id, {key: round(value, 1) for key, value in question.timings.items()})
                if question.future and not question.future.done():
                    question.future.set_result(response)
                if route and route.job:
                    self.voice_routes.start(route.job)
            except asyncio.CancelledError:
                if question.future and not question.future.done():
                    question.future.cancel()
                raise
            except Exception as exc:
                self.last_error = type(exc).__name__
                self.logger.exception('Ошибка ответа guild=%s', self.guild_id)
                if question.future and not question.future.done():
                    question.future.set_exception(RuntimeError('Локальная модель недоступна. Проверь /status.'))
                elif question.kind not in ('ambient', 'ambient_say', 'initiative', 'initiative_say') and self.voice_client and self.voice_client.is_connected():
                    try:
                        pcm = await self.tts.generate_audio('Что-то заело. Проверь локальные модели.')
                        await self.play_pcm(pcm, question, 'Что-то заело. Проверь локальные модели.')
                    except Exception:
                        self.logger.exception('Не удалось озвучить ошибку')
            finally:
                self.conversation.extend(question.user_id)
                if pending_kind == 'ambient':
                    self.ambient_pending = False
                if pending_kind == 'initiative':
                    self.initiative_pending = False
                    self.last_initiative_at = time.monotonic()
                self.responding = False
                self.current_kind = None
                self.questions.task_done()

    def initiative_allowed(self, now, pending=False):
        if self.rare_mode or not self.config.initiative_enabled or not self.participant_mode or self.closed or self.muted or self.quiet_mode:
            return False
        if not pending and (not self.initiative_armed or self.initiative_pending or self.responding):
            return False
        vc = self.voice_client
        members = getattr(getattr(vc, 'channel', None), 'members', ())
        if not vc or not vc.is_connected() or not any(not member.bot for member in members):
            return False
        if self.playing_since or not self.questions.empty() or not self.audio_queue.empty():
            return False
        last_voice = max((state.last_voice for state in self.sink.speakers.values()), default=0) if self.sink else 0
        reference = max(self.joined_at, self.last_human_at, self.last_reply_at, last_voice)
        if now - reference < self.config.initiative_delay:
            return False
        if self.last_initiative_at and now - self.last_initiative_at < self.config.initiative_cooldown:
            return False
        return now - max(self.last_human_at, self.joined_at) < self.config.initiative_session_ttl

    async def initiative_loop(self):
        while True:
            if self.initiative_allowed(time.monotonic()):
                self.initiative_armed = False
                self.enqueue(Question(0, 'Серёга', 'Начни или продолжи разговор',
                                      time.monotonic(), kind='initiative'))
            await asyncio.sleep(2)

    async def hush(self):
        self.muted = True
        self.quiet_mode = True
        if self.memory:
            self.memory.cancel_pending(self.guild_id)
        if self.voice_routes:
            self.voice_routes.cancel(self.guild_id, restore=True)
        if self.voice_client:
            self.voice_client.stop_playing()
        await self.reset(clear_context=False)
        self.conversation.active.clear()
        self.conversation.open_reply_until = 0.0
        self.logger.info('Голосовой вывод выключен guild=%s', self.guild_id)

    async def reset(self, clear_context=True):
        self.generation += 1
        if self.response_task:
            self.response_task.cancel()
            await asyncio.gather(self.response_task, return_exceptions=True)
        for q in (self.questions, self.audio_queue, self.control_queue):
            while not q.empty():
                item = q.get_nowait()
                if q is self.questions:
                    item = item[2]
                if getattr(item, 'future', None) and not item.future.done():
                    item.future.cancel()
                q.task_done()
        if clear_context:
            if self.memory:
                self.memory.cancel_pending(self.guild_id)
            self.conversation.reset()
            self.channel_conversation.reset()
        if clear_context:
            self.recent_spoken.clear()
        self.recent_inputs.clear()
        self.ambient_context.clear()
        self.dialogue_owner = self.awaiting_question = None
        self.ambient_pending = False
        self.initiative_pending = False
        self.initiative_armed = False
        if self.sink:
            self.sink.clear_buffers()
        if not self.closed:
            self.response_task = asyncio.create_task(self.respond(), name=f'reply-{self.guild_id}')

    async def close(self):
        self.closed = True
        await self.detach_capture()
        await self.reset()
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks.clear()
        vc, self.voice_client = self.voice_client, None
        await close_voice_client(vc)
