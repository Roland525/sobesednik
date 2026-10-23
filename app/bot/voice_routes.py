"""Explicit, local voice errands. LLM output never dispatches these actions."""
import asyncio
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
import re
import time

from .security import safe_output
from .session import Question
from .memory import name_key


def channel_key(text):
    return ' '.join(re.findall(r'[^\W_]+', text.casefold().replace('ё', 'е')))


def trim(text):
    return text.strip(' \t\n,.!?:;«»"“”')


@dataclass
class Order:
    destination: str = ''
    message: str = ''
    return_back: bool = False
    person: bool = False
    relay: bool = False


def parse_order(text):
    text = text.strip()
    start = re.match(r'^(?:пожалуйста[, ]+)?(?:(?:можешь(?: ты)?|ты можешь|мог бы ты)\s+)?'
                     r'(перейди|перейти|зайди|зайти|иди|сходи|сходить|подключись|подключиться|'
                     r'переключись|переключиться|переместись|передай|передать)\b\s*', text, re.I)
    if not start:
        return None
    body = re.sub(r'^пожалуйста[, ]+', '', text[start.end():].strip(), flags=re.I)
    if re.match(r'^(?:на|в)\s+(?:демку|демонстрацию|стрим)\b', body, re.I):
        return None
    relay = start[1].casefold() in ('передай', 'передать')
    back = re.search(r'\s*[,;.!]?\s*(?:(?:и|а потом|потом|затем|после чего|после этого)\s+)?'
                     r'(?:(?:вернись|возвращайся|вернуться|возвратись)|(?:перейди|перейти|зайди|иди)\s+обратно)\s*'
                     r'(?:(?:обратно|назад)\s*)?(?:ко мне(?: в (?:мой )?канал)?|в мой канал|в этот канал|сюда)?'
                     r'(?:\s*(?:назад|обратно))?[.!? ]*$', body, re.I)
    if back:
        body = body[:back.start()].rstrip(' ,;')
    body = re.sub(r'^(?:там\s+)?сообщение\b\s*', '', body, flags=re.I)
    # A delimiter separates the destination from the literal speech payload.
    split = re.search(r'\s*[,;]?\s+(?:и\s+)?(?:скажи|сказать|сообщи|сообщить|передай|передать|сообщение)\b\s*[:—-]?\s*', body, re.I)
    if split:
        destination, message = body[:split.start()], trim(body[split.end():])
        relay = True
    elif relay and ':' in body:
        destination, message = body.split(':', 1)
        message = trim(message)
    else:
        destination, message = body, ''
    destination = trim(destination)
    destination = re.sub(r'\s+(?:голосовой\s+)?канал$', '', destination, flags=re.I)
    person = bool(re.match(r'^к\s+', destination, re.I))
    destination = re.sub(r'^(?:в|на|к)\s+', '', destination, flags=re.I)
    destination = re.sub(r'^(?:(?:этот|другой|голосовой)\s+)*(?:канал|войс)\b\s*', '', destination, flags=re.I)
    destination = re.sub(r'^(?:под названием|с названием)\s+', '', destination, flags=re.I)
    if channel_key(destination) in ('другой', 'другой канал', 'один', 'один канал', 'какой нибудь', 'какой то', 'кому нибудь', 'кому то'):
        destination = ''
    if not message and not split and relay:
        destination = re.sub(r'^в\s+', '', destination, flags=re.I)
    return Order(trim(destination), message, bool(back), person, relay)


@dataclass
class Job:
    guild: object
    origin: object
    target: object
    speaker: str
    message: str
    return_back: bool
    restore_allowed: bool = True


@dataclass
class Decision:
    reply: str
    job: object = None


class VoiceRoutes:
    def __init__(self, bot):
        self.bot = bot
        self.pending = {}
        self.active = {}

    def can_continue(self, session, uid, text):
        pending = self.pending.get((session.guild_id, uid))
        return bool(pending and pending[0] > time.monotonic() and pending[1] == session.bound_channel_id
                    and len(text) <= 650 and not text.strip().endswith('?'))

    def is_command(self, text):
        return parse_order(text) is not None

    def prepare(self, session, question):
        if question.kind not in ('voice', 'text'):
            return None
        key = (session.guild_id, question.user_id)
        self.pending = {k: v for k, v in self.pending.items() if v[0] > time.monotonic()}
        order = parse_order(question.text)
        pending = self.pending.get(key)
        if order is None and self.can_continue(session, question.user_id, question.text):
            old = pending[2]
            if not old.destination:
                order = parse_order('Перейди ' + question.text)
                order = replace(order, return_back=old.return_back or order.return_back,
                                relay=old.relay or order.relay, message=order.message or old.message,
                                person=order.person or old.person and not re.match(
                                    r'^(?:в\s+)?(?:канал|голосовой\s+канал)\b', question.text, re.I))
            elif old.relay and not old.message:
                message = re.sub(r'^(?:скажи|передай|сообщение)\s*[:,]?\s*', '', question.text, flags=re.I)
                order = replace(old, message=trim(message))
        if order is None:
            return None
        guild = self.bot.get_guild(session.guild_id)
        origin = getattr(session.voice_client, 'channel', None)
        if not guild or not origin or not any(m.id == question.user_id and not m.bot for m in origin.members):
            return Decision('Поручение на переход дай из моего голосового канала.')
        if guild.id in self.active:
            return Decision('Сейчас выполняю предыдущее поручение. Подожди немного.')
        if not order.destination:
            self.pending[key] = (time.monotonic() + 45, origin.id, order)
            return Decision('В какой голосовой канал перейти?' if not order.person else 'К кому зайти? Назови ник.')
        channels = list(guild.voice_channels)
        if order.person:
            members = {m.id: m for c in channels for m in c.members if not m.bot}
            matches = [m for m in members.values() if channel_key(order.destination) in
                       (channel_key(m.display_name), channel_key(m.name))
                       or name_key(order.destination) == name_key(m.display_name)]
            if not matches and self.bot.memory:
                matches = [members[p['user_id']] for p in self.bot.memory.resolve(guild.id, order.destination)
                           if p['user_id'] in members]
            targets = [m.voice.channel for m in matches if m.voice and m.voice.channel]
            if len(targets) != 1:
                self.pending[key] = (time.monotonic() + 45, origin.id, replace(order, destination=''))
                return Decision('Не нашёл одного участника в голосовом канале. Назови его точный ник или канал.')
        else:
            ref = channel_key(order.destination)
            targets = [c for c in channels if channel_key(c.name) == ref or str(c.id) == order.destination]
            if not targets:
                targets = [c for c in channels if channel_key(getattr(c.category, 'name', '') + ' ' + c.name) == ref]
            if not targets:
                self.pending[key] = (time.monotonic() + 45, origin.id, replace(order, destination=''))
                accessible = [c for c in channels if c.permissions_for(guild.me).view_channel]
                suggestions = sorted(accessible, key=lambda c: SequenceMatcher(None, ref, channel_key(c.name)).ratio(), reverse=True)[:3]
                labels = '; '.join(safe_output(c.name[:50], [self.bot.config.discord_token]) for c in suggestions)
                return Decision('Такой голосовой канал не нашёл.' +
                                (' Доступны: ' + labels + '. Какой выбрать?' if labels else ' Назови точное название.'))
            if len(targets) > 1:
                self.pending[key] = (time.monotonic() + 45, origin.id, replace(order, destination=''))
                labels = [getattr(c.category, 'name', '') + ' / ' + c.name for c in targets[:3]]
                return Decision('Есть одинаковые названия: ' + '; '.join(labels) + '. Назови категорию вместе с каналом.')
        target = targets[0]
        permissions = target.permissions_for(guild.me)
        if not (permissions.view_channel and permissions.connect and permissions.speak):
            return Decision('У меня нет прав войти и говорить в этом канале.')
        if target.user_limit and len(target.members) >= target.user_limit and not permissions.move_members and target.id != origin.id:
            return Decision('Этот голосовой канал заполнен.')
        if order.relay and not order.message:
            self.pending[key] = (time.monotonic() + 45, origin.id, order)
            return Decision('Что именно передать? Скажи текст сообщения.')
        if len(order.message) > 400:
            return Decision('Сообщение слишком длинное. Сократи его до четырёхсот символов.')
        self.pending.pop(key, None)
        if target.id == origin.id and not order.message:
            return Decision('Я уже в этом канале.')
        message = safe_output(order.message, [self.bot.config.discord_token])
        job = Job(guild, origin, target, safe_output(question.name, [self.bot.config.discord_token]), message, order.return_back)
        reply = 'Окей, перехожу в ' + target.name + '.'
        if order.return_back and not order.message:
            reply += ' Затем вернусь.'
        if order.message:
            reply += ' Передам сообщение' + (' и вернусь.' if order.return_back else '.')
        return Decision(reply, job)

    def start(self, job):
        if job.guild.id in self.active or self.bot.shutting_down:
            return
        task = asyncio.create_task(self.run(job), name=f'voice-errand-{job.guild.id}')
        self.active[job.guild.id] = (task, job)

    def cancel(self, guild_id, *, restore=False):
        self.pending = {k: v for k, v in self.pending.items() if k[0] != guild_id}
        pair = self.active.get(guild_id)
        if pair:
            if not restore:
                pair[1].restore_allowed = False
            pair[0].cancel()

    async def close(self):
        tasks = [pair[0] for pair in self.active.values()]
        for guild_id in list(self.active):
            self.cancel(guild_id)
        await asyncio.gather(*tasks, return_exceptions=True)

    def current_channel(self, guild):
        return getattr(guild.voice_client, 'channel', None)

    async def say(self, guild, text):
        session = self.bot.sessions.get(guild.id)
        if not session or session.closed or session.muted:
            raise asyncio.CancelledError
        future = asyncio.get_running_loop().create_future()
        session.enqueue(Question(0, 'Поручение', text, time.monotonic(), kind='say', future=future))
        await asyncio.wait_for(future, 45)

    async def run(self, job):
        failed = delivered = False
        try:
            if self.current_channel(job.guild) != job.origin:
                return
            async with asyncio.timeout(80):
                await self.bot.join_channel(job.target)
                if self.current_channel(job.guild) != job.target:
                    return
                if job.message:
                    await self.say(job.guild, 'Сообщение от ' + job.speaker + ': ' + job.message)
                    delivered = True
        except asyncio.CancelledError:
            raise
        except Exception as error:
            failed = True
            self.bot.logger.warning('Поручение voice не выполнено guild=%s: %s', job.guild.id, type(error).__name__)
        finally:
            try:
                current = self.current_channel(job.guild)
                if (job.restore_allowed and not self.bot.shutting_down and (job.return_back or failed)
                    and current in (None, job.target) and job.origin != current):
                    await self.bot.join_channel(job.origin)
                if failed and job.restore_allowed and self.current_channel(job.guild) == job.origin:
                    await self.say(job.guild, 'Не получилось выполнить поручение. Я вернулся.')
                elif delivered and job.return_back and self.current_channel(job.guild) == job.origin:
                    await self.say(job.guild, 'Вернулся. Сообщение передал.')
                elif not failed and not job.message and job.return_back and job.restore_allowed and self.current_channel(job.guild) == job.origin:
                    await self.say(job.guild, 'Вернулся.')
            except asyncio.CancelledError:
                pass
            except Exception as error:
                self.bot.logger.warning('Возврат после поручения voice не выполнен guild=%s: %s', job.guild.id, type(error).__name__)
            finally:
                self.active.pop(job.guild.id, None)
