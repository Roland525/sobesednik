import asyncio
import contextlib
import time
import discord
import aiohttp
from discord.ext import commands, voice_recv

from .services import AudioService, TTSService, OllamaService
from .session import GuildSession, Question
from .voice.compat import stats
from .voice.lifecycle import close_voice_client
from .config import ROOT
from .memory import MemoryStore
from .voice_routes import VoiceRoutes
from .learning import LearningCorpus


class DiscordBot(commands.Bot):
    def __init__(self, config, logger, metrics):
        intents = discord.Intents.default()
        intents.message_content = config.enable_prefix_commands
        super().__init__(command_prefix=commands.when_mentioned_or('!'), intents=intents,
                         connector=aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver()),
                         allowed_mentions=discord.AllowedMentions.none(),
                         activity=discord.Activity(type=discord.ActivityType.listening, name='«' + config.wake_word.capitalize() + '»'))
        self.config, self.logger, self.metrics = config, logger, metrics
        self.audio_service = AudioService(logger, config)
        self.tts_service = TTSService(logger, config)
        self.ollama_service = OllamaService(logger, config)
        self.memory = None
        self.learning = None
        if config.learning_capture_enabled:
            try:
                self.learning = LearningCorpus(ROOT / 'data/learning.sqlite3', secrets=[config.discord_token])
            except Exception as error:
                logger.warning('Учебный корпус недоступен: %s', type(error).__name__)
        if config.memory_enabled:
            try:
                self.memory = MemoryStore(ROOT / 'data/memory.sqlite3', secrets=[config.discord_token])
            except Exception as error:
                logger.warning('Постоянная память недоступна: %s', type(error).__name__)
        self.sessions = {}
        self.guild_locks = {}
        self.synced_guilds = set()
        self.monitor_task = None
        self.voice_transitions = set()
        self.voice_recovery_tasks = {}
        self.disconnected_since = {}
        self.shutting_down = False
        self.learning_request = None
        self.learning_idle_since = time.time()
        self.voice_smoke_requested = False
        self.voice_routes = VoiceRoutes(self)
        self.register_commands()

    def session(self, guild_id):
        if guild_id not in self.sessions:
            session = self.sessions[guild_id] = GuildSession(guild_id, self.config, self.audio_service,
                self.ollama_service, self.tts_service, self.logger, self.metrics, memory=self.memory,
                voice_routes=self.voice_routes, learning=self.learning)
            session.start()
        return self.sessions[guild_id]

    async def setup_hook(self):
        try:
            await self.ollama_service.warmup()
            self.logger.info('Ollama прогрета: %s', self.config.ollama_model)
        except Exception:
            self.logger.exception('Ollama недоступна при старте; /status покажет состояние')
        await asyncio.to_thread(self.audio_service.load)
        await asyncio.to_thread(self.tts_service.load)
        await self.tree.sync()
        self.monitor_task = asyncio.create_task(self.monitor(), name='voice-monitor')

    async def on_ready(self):
        self.logger.info('Собеседник подключён к Discord; серверов: %d', len(self.guilds))
        if self.user.name != self.config.bot_name:
            try:
                await self.user.edit(username=self.config.bot_name)
                self.logger.info('Имя Discord-аккаунта изменено на %s', self.config.bot_name)
            except discord.HTTPException:
                self.logger.warning('Не удалось изменить глобальное имя; настрой его в Discord Developer Portal')
        for guild in self.guilds:
            await self.sync_guild(guild)
        if self.config.auto_join_channel_id:
            channel = self.get_channel(self.config.auto_join_channel_id)
            if isinstance(channel, discord.VoiceChannel) and not channel.guild.voice_client:
                try:
                    await self.join_channel(channel)
                except Exception:
                    self.logger.exception('Не удалось выполнить auto_join')
        if self.voice_smoke_requested:
            self.voice_smoke_requested = False
            asyncio.create_task(self.voice_smoke(), name='voice-smoke')

    async def voice_smoke(self):
        import json
        from .config import ROOT
        report = {'scope': 'live Discord, empty channel; no human receive test',
                  'commands': [command.name for command in self.tree.get_commands()]}
        selected = None
        try:
            for guild in self.guilds:
                for channel in guild.voice_channels:
                    permissions = channel.permissions_for(guild.me)
                    if (not channel.members and channel != guild.afk_channel and
                        'создать' not in channel.name.lower() and permissions.connect and permissions.speak and permissions.view_channel):
                        selected = channel
                        break
                if selected:
                    break
            if not selected:
                report['skipped'] = 'Нет доступного пустого канала'
                return
            await self.join_channel(selected)
            report['connected'] = selected.guild.voice_client.is_connected()
            session = self.sessions[selected.guild.id]
            vc = session.voice_client
            report['receive_started'] = bool(vc.is_listening())
            report['dave_protocol_version'] = vc._connection.dave_protocol_version
            future = asyncio.get_running_loop().create_future()
            session.enqueue(Question(0, 'Проверка', 'Собеседник на связи. Проверка голоса завершена.',
                                    time.monotonic(), kind='say', future=future))
            async with asyncio.timeout(30):
                await future
            report['playback_completed'] = True
            report['pcm_received'] = session.sink.received_frames
            report['human_receive_verified'] = False
            self.logger.info('Discord voice smoke: connected=%s, receive=%s, playback=%s',
                             report['connected'], report['receive_started'], report['playback_completed'])
        except Exception as error:
            report['error'] = type(error).__name__
            self.logger.exception('Discord voice smoke failed')
        finally:
            if selected:
                session = self.sessions.pop(selected.guild.id, None)
                if session:
                    await session.close()
            (ROOT / 'reports/discord-voice-smoke.json').write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')

    async def sync_guild(self, guild):
        if guild.id in self.synced_guilds:
            return
        try:
            if self.config.sync_guild_commands:
                self.tree.copy_global_to(guild=guild)
                await self.tree.sync(guild=guild)
            if guild.me.nick != self.config.bot_name:
                with contextlib.suppress(discord.Forbidden):
                    await guild.me.edit(nick=self.config.bot_name)
            self.synced_guilds.add(guild.id)
            self.logger.info('Команды готовы guild=%s', guild.id)
        except discord.HTTPException:
            self.logger.exception('Ошибка регистрации команд guild=%s', guild.id)

    async def on_guild_join(self, guild):
        await self.sync_guild(guild)

    async def join_channel(self, channel, user_id=None):
        lock = self.guild_locks.setdefault(channel.guild.id, asyncio.Lock())
        async with lock:
            if self.shutting_down or self.learning_request:
                raise commands.CheckFailure('Идёт технический перезапуск. Попробуй после подключения.')
            self.voice_transitions.add(channel.guild.id)
            try:
                await self.connect_voice(channel, user_id)
            finally:
                self.voice_transitions.discard(channel.guild.id)

    async def disconnect_voice(self, vc):
        await close_voice_client(vc)

    async def connect_voice(self, channel, user_id=None):
        """Called under the guild lock, with our own voice events suppressed."""
        session = self.session(channel.guild.id)
        if user_id is None and session.sink and self.config.listen_only_joiner:
            user_id = session.sink.target_user_id
        vc = channel.guild.voice_client
        if (vc and vc.is_connected() and session.voice_client is vc
            and session.bound_channel_id == channel.id and vc.channel.id == channel.id):
            if vc.is_listening() and session.sink and not session.sink.closed:
                return
        else:
            await session.reset()
            await session.detach_capture()
            session.voice_client = None
            await self.disconnect_voice(vc)
            vc = await channel.connect(cls=voice_recv.VoiceRecvClient, reconnect=True,
                                       timeout=25, self_deaf=False)
        await session.attach(vc, user_id if self.config.listen_only_joiner else None)
        self.disconnected_since.pop(channel.guild.id, None)

    async def on_voice_state_update(self, member, before, after):
        if not self.user or member.id != self.user.id or before.channel == after.channel:
            return
        if member.guild.id not in self.voice_transitions and not self.shutting_down:
            self.voice_routes.cancel(member.guild.id)
            self.schedule_voice_recovery(member.guild)

    def schedule_voice_recovery(self, guild):
        previous = self.voice_recovery_tasks.get(guild.id)
        if previous and not previous.done():
            return
        self.voice_recovery_tasks[guild.id] = asyncio.create_task(
            self.recover_voice(guild), name=f'voice-recover-{guild.id}')

    async def recover_voice(self, guild):
        # Coalesce quick moves and allow discord.py's own state update to finish.
        await asyncio.sleep(.7)
        for attempt in range(3):
            try:
                await self.reconcile_voice(guild)
                return
            except asyncio.CancelledError:
                raise
            except Exception:
                self.logger.exception('Восстановление voice guild=%s, попытка=%s', guild.id, attempt + 1)
                if attempt < 2:
                    await asyncio.sleep(2 ** (attempt + 1))

    async def reconcile_voice(self, guild):
        async with self.guild_locks.setdefault(guild.id, asyncio.Lock()):
            if self.shutting_down:
                return
            # Read the current cache, never a stale event's destination.
            channel = getattr(getattr(guild.me, 'voice', None), 'channel', None)
            session = self.sessions.get(guild.id)
            if not guild.voice_client and (not session or session.bound_channel_id is None):
                return
            self.voice_transitions.add(guild.id)
            try:
                if channel is None:
                    self.sessions.pop(guild.id, None)
                    vc = guild.voice_client or (session.voice_client if session else None)
                    if session:
                        # detach_capture still needs the old client to stop its reader.
                        session.voice_client = vc
                        await session.detach_capture()
                        session.voice_client = None
                        await session.close()
                    await self.disconnect_voice(vc)
                    self.disconnected_since.pop(guild.id, None)
                    self.logger.info('Отключение voice очищено guild=%s; /join снова доступен', guild.id)
                elif session:
                    await self.connect_voice(channel)
            finally:
                self.voice_transitions.discard(guild.id)

    def require_voice_control(self, ctx):
        vc = ctx.guild.voice_client
        caller = getattr(ctx.author, 'voice', None)
        if vc and vc.is_connected() and (not caller or not caller.channel or caller.channel.id != vc.channel.id):
            if not ctx.author.guild_permissions.manage_guild:
                raise commands.CheckFailure('Зайди в мой голосовой канал для этой команды.')

    def register_commands(self):
        @self.hybrid_command(name='join', description='Собеседник присоединится к твоему голосовому каналу')
        @commands.guild_only()
        async def join(ctx):
            voice = getattr(ctx.author, 'voice', None)
            if not voice or not voice.channel:
                raise commands.CheckFailure('Сначала зайди в голосовой канал.')
            self.require_voice_control(ctx)
            await ctx.defer(ephemeral=True)
            await self.join_channel(voice.channel, ctx.author.id)
            await ctx.send(f'На месте. Для прямого вопроса зови «{self.config.wake_word.capitalize()}». Аудио на диск не записываю.', ephemeral=True)

        @self.hybrid_command(name='leave', description='Выйти из голосового канала')
        @commands.guild_only()
        async def leave(ctx):
            self.require_voice_control(ctx)
            await ctx.defer(ephemeral=True)
            self.voice_routes.cancel(ctx.guild.id)
            lock = self.guild_locks.setdefault(ctx.guild.id, asyncio.Lock())
            async with lock:
                self.voice_transitions.add(ctx.guild.id)
                try:
                    session = self.sessions.pop(ctx.guild.id, None)
                    vc = ctx.guild.voice_client
                    if session:
                        await session.close()
                    if ctx.guild.voice_client is vc:
                        await self.disconnect_voice(vc)
                finally:
                    self.voice_transitions.discard(ctx.guild.id)
            await ctx.send('Вышел.', ephemeral=True)

        @self.hybrid_command(name='mute', description='Сразу остановить голос и молчать до обращения по имени')
        @commands.guild_only()
        async def mute(ctx):
            self.require_voice_control(ctx)
            await ctx.defer(ephemeral=True)
            await self.session(ctx.guild.id).hush()
            await ctx.send('Молчу. Для продолжения позови по имени или вызови /unmute.', ephemeral=True)

        @self.hybrid_command(name='unmute', description='Снова разрешить Серёге говорить')
        @commands.guild_only()
        async def unmute(ctx):
            self.require_voice_control(ctx)
            session = self.session(ctx.guild.id)
            session.muted = False
            session.quiet_mode = False
            session.initiative_armed = True
            session.conversation.replied_to(ctx.author.id)
            await ctx.send('Снова слушаю и отвечаю.', ephemeral=True)

        @self.hybrid_command(name='ping', description='Задержка соединения с Discord')
        async def ping(ctx):
            await ctx.send(f'Discord: {self.latency * 1000:.0f} мс.', ephemeral=True)

        @self.hybrid_command(name='reset', description='Сбросить контекст и голосовую очередь этого сервера')
        @commands.guild_only()
        async def reset(ctx):
            self.require_voice_control(ctx)
            await ctx.defer(ephemeral=True)
            self.voice_routes.cancel(ctx.guild.id)
            await self.session(ctx.guild.id).reset()
            await ctx.send('Контекст и очереди сброшены.', ephemeral=True)

        @self.hybrid_command(name='status', description='Состояние локальных моделей и голосового канала')
        @commands.guild_only()
        async def status(ctx):
            await ctx.defer(ephemeral=True)
            try:
                healthy = await self.ollama_service.health()
            except Exception:
                healthy = False
            session = self.sessions.get(ctx.guild.id)
            sink = session.sink if session else None
            vc = ctx.guild.voice_client
            details = [f'{self.config.bot_name} · STT {self.config.whisper_model.split("/")[-1]}: {self.audio_service.device}',
                       f'Режим: {"участник беседы" if session and session.participant_mode else self.config.conversation_mode}',
                       f'Инициатива: {"включена, после паузы" if self.config.initiative_enabled else "выключена"}',
                       f'Тишина: {"только личный диалог" if session and session.quiet_mode else "выключена"}',
                       'Демка Discord: кадры недоступны этому клиенту',
                       f'Ollama: {self.config.ollama_model} — {"готова" if healthy else "недоступна"}',
                       f'TTS: {self.config.tts_engine}, CPU, локально',
                       f'Voice: {"подключён" if vc and vc.is_connected() else "отключён"}; приём: {bool(vc and vc.is_listening())}',
                       f'DAVE: decrypted={stats["dave_decrypted"]}, dropped={stats["dave_dropped"]}; Opus errors={stats["opus_bad"]}']
            if sink:
                details.append(f'PCM frames={sink.received_frames}, фраз={sink.completed_utterances}, потерь={sink.dropped_frames}/{sink.dropped_utterances}')
            if session:
                details.append(f'Защита от цикла: {session.guard_counts}')
                session.conversation.expire()
                details.append(f'Очередь STT={session.audio_queue.qsize()}, ответов={session.questions.qsize()}; активных участников={len(session.conversation.active)}')
                if session.last_metrics:
                    details.append('Последний ответ: ' + ', '.join(f'{key}={value:.0f} мс' for key, value in session.last_metrics.items()))
                if session.last_error:
                    details.append(f'Последняя ошибка: {session.last_error}; подробности в logs/serega.log')
            await ctx.send('\n'.join(details), ephemeral=True)

        @self.hybrid_command(name='ask', description='Спросить Серёгу текстом')
        @commands.guild_only()
        async def ask(ctx, *, text: str):
            if len(text) > 2000:
                raise commands.CheckFailure('Вопрос слишком длинный (максимум 2000 символов).')
            await ctx.defer(ephemeral=True)
            future = asyncio.get_running_loop().create_future()
            self.session(ctx.guild.id).enqueue(Question(ctx.author.id, ctx.author.display_name, text,
                time.monotonic(), kind='text', future=future))
            async with asyncio.timeout(100):
                response = await future
            await ctx.send(response, ephemeral=True)

        @self.hybrid_command(name='say', description='Произнести текст локальным голосом')
        @commands.guild_only()
        async def say(ctx, *, text: str):
            self.require_voice_control(ctx)
            if not ctx.guild.voice_client or not ctx.guild.voice_client.is_connected():
                raise commands.CheckFailure('Сначала вызови /join.')
            if len(text) > 450:
                raise commands.CheckFailure('Для /say максимум 450 символов.')
            await ctx.defer(ephemeral=True)
            self.session(ctx.guild.id).enqueue(Question(ctx.author.id, ctx.author.display_name, text,
                time.monotonic(), kind='say'))
            await ctx.send('Добавил в очередь.', ephemeral=True)

        @self.hybrid_command(name='listenall', description='Слушать всех участников канала')
        @commands.guild_only()
        async def listenall(ctx):
            self.require_voice_control(ctx)
            session = self.sessions.get(ctx.guild.id)
            if session and session.sink:
                session.sink.target_user_id = None
            await ctx.send(f'Слушаю всех. Для обращения используй имя «{self.config.wake_word.capitalize()}» в любой части фразы.', ephemeral=True)

        @self.hybrid_command(name='listenme', description='Слушать только тебя в текущем канале')
        @commands.guild_only()
        async def listenme(ctx):
            self.require_voice_control(ctx)
            session = self.sessions.get(ctx.guild.id)
            if session and session.sink:
                session.sink.target_user_id = ctx.author.id
            await ctx.send('Слушаю только тебя до следующего /join или /listenall.', ephemeral=True)

        @self.hybrid_command(name='mode', description='Режим: strict — имя в каждой фразе, summon — диалог, participant — участник')
        @commands.guild_only()
        async def mode(ctx, mode: str):
            self.require_voice_control(ctx)
            if mode not in ('participant', 'summon', 'strict', 'rare'):
                raise commands.CheckFailure('Укажи rare, strict, summon или participant.')
            session = self.session(ctx.guild.id)
            session.rare_mode = mode == 'rare'
            session.participant_mode = mode in ('participant', 'rare')
            session.require_wake_each_turn = mode == 'strict'
            await session.reset(clear_context=False)
            label = {'participant': 'участник беседы', 'summon': 'по имени с продолжением диалога',
                     'strict': 'только обращение по имени в каждой фразе',
                     'rare': 'по имени и редкие реплики по понятному контексту'}[mode]
            await ctx.send('Режим: ' + label, ephemeral=True)

    async def on_command_error(self, ctx, error):
        if isinstance(error, commands.CommandNotFound):
            return
        cause = getattr(error, 'original', error)
        if isinstance(cause, commands.CheckFailure):
            message = str(cause)
        else:
            message = 'Не получилось. Проверь /status и logs/serega.log.'
            self.logger.error('Ошибка команды: %s', type(cause).__name__, exc_info=(type(cause), cause, cause.__traceback__))
        with contextlib.suppress(discord.HTTPException):
            await ctx.send(message, ephemeral=True)

    def maybe_learning_maintenance(self):
        if (not self.config.learning_auto_enabled or not self.learning or self.shutting_down
            or self.learning_request or len(self.guilds) != 1
            or self.voice_transitions or any(lock.locked() for lock in self.guild_locks.values())):
            return
        from .learning_cycle import MaintenancePolicy
        from tools.run_learning_cycle import STATE, prerequisites, disk_ready, snapshot
        import json
        now = time.time()
        guild = self.guilds[0]
        vc = guild.voice_client
        humans = bool(vc and any(not member.bot for member in vc.channel.members))
        if humans:
            self.learning_idle_since = now
            return
        if now - self.learning_idle_since < 1800:
            return
        try:
            previous = json.loads(STATE.read_text(encoding='utf-8')) if STATE.exists() else {}
            count = self.learning.db.execute('SELECT count(*) FROM examples WHERE guild=?', (guild.id,)).fetchone()[0]
            if not MaintenancePolicy().ready(now=now, idle_since=self.learning_idle_since, humans=False,
                                              count=count, last_count=previous.get('last_count', 0),
                                              last_attempt=previous.get('last_attempt', 0)) or not prerequisites() or not disk_ready():
                return
            if len(snapshot(guild.id)[1]) < 16:
                return
        except (OSError, ValueError):
            return
        self.learning_request = {'guild': guild.id, 'channel': getattr(getattr(vc, 'channel', None), 'id', 0)}
        self.logger.info('Пустой канал: запрошено локальное обучение на %s примерах.', count)
        # close() cancels monitor; never await it from monitor itself.
        asyncio.create_task(self.close(), name='learning-maintenance-close')

    async def monitor(self):
        await self.wait_until_ready()
        while not self.is_closed():
            self.maybe_learning_maintenance()
            for session in list(self.sessions.values()):
                session.conversation.expire()
                vc = session.voice_client
                if not vc and session.bound_channel_id is None:
                    continue
                if session.guild_id in self.voice_transitions:
                    continue
                guild = self.get_guild(session.guild_id)
                channel = getattr(getattr(getattr(guild, 'me', None), 'voice', None), 'channel', None)
                if guild and (channel is None or channel.id != session.bound_channel_id):
                    self.schedule_voice_recovery(guild)
                    continue
                if vc and not vc.is_connected():
                    since = self.disconnected_since.setdefault(session.guild_id, time.monotonic())
                    if guild and time.monotonic() - since > 15:
                        self.schedule_voice_recovery(guild)
                    continue
                self.disconnected_since.pop(session.guild_id, None)
                capture_failed = any(task.done() for task in session.tasks if task.get_name().startswith('capture-'))
                if vc and vc.is_connected() and (not vc.is_listening() or not session.sink or session.sink.closed or capture_failed):
                    try:
                        async with self.guild_locks.setdefault(session.guild_id, asyncio.Lock()):
                            if self.sessions.get(session.guild_id) is session and session.guild_id not in self.voice_transitions:
                                target = session.sink.target_user_id if session.sink else None
                                await session.attach(vc, target)
                        self.logger.warning('Приём voice перезапущен guild=%s', session.guild_id)
                    except Exception:
                        self.logger.exception('Ошибка восстановления voice guild=%s', session.guild_id)
            await asyncio.sleep(5)

    async def close(self):
        self.shutting_down = True
        await self.voice_routes.close()
        for task in self.voice_recovery_tasks.values():
            task.cancel()
        await asyncio.gather(*self.voice_recovery_tasks.values(), return_exceptions=True)
        self.voice_recovery_tasks.clear()
        if self.monitor_task:
            self.monitor_task.cancel()
            await asyncio.gather(self.monitor_task, return_exceptions=True)
        for session in list(self.sessions.values()):
            with contextlib.suppress(Exception):
                await session.close()
        self.sessions.clear()
        await self.ollama_service.close()
        self.audio_service.close()
        self.tts_service.close()
        if self.memory:
            self.memory.close()
        if self.learning:
            self.learning.close()
        await super().close()
