import asyncio
import json
import os
import time
from desktop.protocol import Protocol
from desktop.storage import Profile, atomic_json, redact


class DesktopControl:
    def __init__(self, bot, root):
        self.bot=bot; self.profile=Profile(root); self.protocol=Protocol(root)

    async def dispatch(self, op, payload):
        guild=self.bot.get_guild(int(payload.get('guild',0)))
        if not guild: raise ValueError('Сервер не найден. Обновите список.')
        if op=='feedback_add':
            from .feedback import FeedbackStore
            session=self.bot.sessions.get(guild.id)
            turn=next((row for row in session.journal.snapshot() if row['id']==payload.get('turn_id')),None) if session else None
            if not turn:raise ValueError('Реплика уже очищена. Выберите реплику из текущего разговора.')
            store=FeedbackStore(self.profile.root/'data/learning.sqlite3',secrets=(self.bot.config.discord_token,))
            try:record=store.add(guild.id,turn,payload.get('kind'),payload.get('correction'),self.bot.config.wake_word)
            finally:store.close()
            if record.get('invalidated') and self.bot.learning:
                old=record['invalidated']
                with self.bot.learning.db:
                    self.bot.learning.db.execute("DELETE FROM examples WHERE guild=? AND author=? AND kind='correction' AND prompt=? AND target=?",
                        (guild.id,old['uid'],old['question'],old['answer']))
            if record['trainable'] and self.bot.learning:
                added=self.bot.learning.add(guild.id,record['uid'],record['uid'],'correction',record['question'],record['answer'],time.time())
                if added:return 'Исправление сохранено в локальных проверках и учебных примерах.'
            return 'Исправление сохранено локально. Эта запись не используется для изменения весов.'
        elif op=='join':
            channel=guild.get_channel(int(payload.get('channel',0)))
            if not channel or getattr(channel.type,'value',channel.type)!=2: raise ValueError('Голосовой канал не найден')
            permissions=channel.permissions_for(guild.me)
            if not permissions.view_channel or not permissions.connect:
                raise ValueError('У бота нет права просматривать этот канал или подключаться к нему')
            await self.bot.join_channel(channel)
        elif op=='leave':
            self.bot.voice_routes.cancel(guild.id)
            session=self.bot.sessions.pop(guild.id,None)
            if session: await session.close()
            await self.bot.disconnect_voice(guild.voice_client)
        elif op in ('mute','reset','replace_context'):
            session=self.bot.sessions.get(guild.id)
            if not session: raise ValueError('Сначала подключите бота к каналу')
            messages=payload.get('messages',[])
            if op=='replace_context':
                if (not isinstance(messages,list) or len(messages)>60
                    or any(not isinstance(m,dict) or set(m)!={'role','content'}
                           or m['role'] not in ('user','assistant') or not isinstance(m['content'],str)
                           for m in messages) or sum(len(m['content']) for m in messages)>12000):
                    raise ValueError('Контекст: до 60 реплик человека/бота и 12000 символов')
            if op=='mute': await session.hush()
            else:
                if session.voice_client: session.voice_client.stop_playing()
                await session.reset()
                if op=='replace_context':
                    session.conversation.history.extend(messages); session.conversation.trim()
                    kept=len(session.conversation.history)
                    return f'Контекст применён: {kept} из {len(messages)} реплик. Старые реплики сверх лимита удалены.' if kept<len(messages) else f'Контекст применён: {kept} реплик.'
        elif op in ('memory_add','memory_delete','memory_forget','memory_clear','learning_delete','learning_clear'):
            self.profile.mutate_memory(op,payload)
            if op=='memory_add': return 'Заметка сохранена в постоянной памяти'
            if self.bot.memory: self.bot.memory.pending_facts.clear()
            if self.bot.learning:
                self.bot.learning.humans.pop(guild.id,None)
                self.bot.learning.answers={key:value for key,value in self.bot.learning.answers.items() if key[0]!=guild.id}
            session=self.bot.sessions.get(guild.id)
            if session:
                if session.voice_client: session.voice_client.stop_playing()
                await session.reset()
        else: raise ValueError('Неизвестная команда')
        return 'Выполнено'

    def state(self):
        guilds=[]; sessions={}
        token=self.bot.config.discord_token
        for guild in self.bot.guilds:
            channels=[]
            for channel in guild.voice_channels:
                permissions=channel.permissions_for(guild.me)
                if permissions.view_channel and permissions.connect:
                    channels.append({'id':str(channel.id),'name':channel.name,
                                     'members':len(channel.members)})
            vc=guild.voice_client
            guilds.append({'id':str(guild.id),'name':guild.name,'channels':channels,
                           'connected_channel':str(vc.channel.id) if vc and vc.is_connected() else '',
                           'errand':self.bot.voice_routes.status(guild.id)})
        for gid,session in self.bot.sessions.items():
            sessions[str(gid)]={'context':[{'role':m['role'],'content':redact(m['content'],token)}
                for m in list(session.conversation.history)[-60:]],
                'metrics':dict(session.last_metrics),'muted':getattr(session,'muted',False),
                'turns':session.journal.snapshot()}
        return {'updated':time.time(),'pid':os.getpid(),'ready':self.bot.is_ready(),
                'guilds':guilds,'sessions':sessions,'name':self.bot.config.bot_name,
                'learning_maintenance':bool(self.bot.learning_request)}

    async def tick(self):
        for path in sorted(self.protocol.commands.glob('*.json'))[:20]:
            try:
                if path.stat().st_size>50000: continue
                command=json.loads(path.read_text(encoding='utf-8'))
                if not self.protocol.valid(command) or path.stem!=command['id']: continue
                try:
                    message=await self.dispatch(command['operation'],command['payload'])
                    result={'ok':True,'message':message}
                except Exception as error:
                    result={'ok':False,'message':redact(str(error),self.bot.config.discord_token)[:400]}
                atomic_json(self.protocol.responses/path.name,result)
            except (OSError,ValueError): pass
            finally: path.unlink(missing_ok=True)
        # Windows replacement retries must not pause the Discord voice loop.
        state=self.state()
        await asyncio.to_thread(atomic_json,self.protocol.directory/'state.json',state)
        for path in self.protocol.responses.glob('*.json'):
            if time.time()-path.stat().st_mtime>300: path.unlink(missing_ok=True)

    async def run(self):
        await self.bot.wait_until_ready()
        try:
            while not self.bot.is_closed():
                try: await self.tick()
                except Exception: self.bot.logger.exception('Ошибка локальной панели управления')
                await asyncio.sleep(.5)
        finally:
            (self.protocol.directory/'state.json').unlink(missing_ok=True)
