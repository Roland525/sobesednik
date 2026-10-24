import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import time
import uuid
import zipfile

from bot.config import Config

EDITABLE = {'wake_word', 'bot_name', 'tts_speed', 'tts_volume', 'tts_supertonic_voice',
            'tts_supertonic_steps', 'conversation_mode', 'max_response_chars', 'history_turns',
            'history_chars', 'history_ttl', 'ollama_reasoning', 'ollama_reasoning_budget',
            'memory_enabled', 'learning_capture_enabled', 'learning_auto_enabled',
            'auto_join_channel_id', 'dota_patch_source_enabled', 'whisper_model', 'ollama_num_gpu',
            'ollama_model', 'response_profile'}


def read_shared(path, encoding='utf-8'):
    """Read a snapshot without preventing Windows atomic replacement."""
    path=Path(path)
    if os.name!='nt': return path.read_text(encoding=encoding)
    import ctypes
    from ctypes import wintypes
    import msvcrt
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    create=kernel.CreateFileW
    create.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,
                     wintypes.LPVOID,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
    create.restype=wintypes.HANDLE
    handle=create(str(path),0x80000000,0x7,None,3,0x80,None)
    if handle==ctypes.c_void_p(-1).value: raise ctypes.WinError(ctypes.get_last_error())
    try: fd=msvcrt.open_osfhandle(handle,os.O_RDONLY|os.O_BINARY)
    except BaseException:
        kernel.CloseHandle.argtypes=[wintypes.HANDLE];kernel.CloseHandle(handle);raise
    with os.fdopen(fd,'rb') as stream: return stream.read().decode(encoding)


def replace_file(source, target):
    # Windows readers briefly hold a handle without FILE_SHARE_DELETE.
    for attempt in range(20):
        try:
            source.replace(target)
            return
        except PermissionError:
            if attempt == 19: raise
            time.sleep(.01 * (attempt + 1))


def atomic_json(path, value):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
        # Keep the previous complete file until replacement succeeds.
        replace_file(temporary,path)
    finally:
        temporary.unlink(missing_ok=True)


def redact(text, token=''):
    if token: text=text.replace(token,'[ТОКЕН СКРЫТ]')
    text=re.sub(r'(Bot|Bearer)\s+[A-Za-z0-9_.-]{20,}',r'\1 [ТОКЕН СКРЫТ]',text)
    return re.sub(r'[A-Za-z0-9_-]{22,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{25,}',
                  '[ТОКЕН СКРЫТ]',text)


def default_root():
    return Path(os.environ.get('LOCALAPPDATA',Path.home()))/'Sobesednik'/'engine'


def install_payload(archive, root):
    root=Path(root).resolve()
    with zipfile.ZipFile(archive) as z:
        names=z.namelist()
        if len(names)!=len(set(names)) or len(names)>1000:
            raise ValueError('Некорректный архив приложения')
        for name in names:
            p=PurePosixPath(name)
            if '\\' in name or ':' in name or p.is_absolute() or '..' in p.parts:
                raise ValueError('Недопустимый путь в архиве')
        manifest=json.loads(z.read('RELEASE_MANIFEST.json'))['files']
        if set(names)!=set(manifest)|{'RELEASE_MANIFEST.json'}:
            raise ValueError('Состав архива не совпадает с манифестом')
        payload={}
        for name,digest in manifest.items():
            data=z.read(name)
            if len(data)>10_000_000 or hashlib.sha256(data).hexdigest()!=digest:
                raise ValueError('Контрольная сумма исходников не совпала')
            # Private state can never arrive via an application source update.
            if name in ('config.json','.env') or name.split('/')[0] in ('data','models','logs','backups','temp'):
                raise ValueError('Архив содержит рабочие данные')
            payload[name]=data
    root.mkdir(parents=True,exist_ok=True)
    for name,data in payload.items():
        target=root/name
        if target.resolve()!=root and not target.resolve().is_relative_to(root):
            raise ValueError('Путь обновления выходит за папку приложения')
        if name=='PERSONA.md' and target.exists(): continue
        target.parent.mkdir(parents=True,exist_ok=True)
        temporary=target.with_name(target.name+'.update')
        temporary.write_bytes(data); temporary.replace(target)
    if not (root/'config.json').exists() and (root/'config.example.json').exists():
        atomic_json(root/'config.json',json.loads((root/'config.example.json').read_text(encoding='utf-8-sig')))


class Profile:
    def __init__(self, root):
        self.root=Path(root).resolve(); self.root.mkdir(parents=True,exist_ok=True)

    def settings(self):
        path=self.root/'config.json'
        raw=json.loads(read_shared(path,'utf-8-sig')) if path.exists() else {}
        values={k:getattr(Config(),k) for k in EDITABLE}
        values.update({k:v for k,v in raw.items() if k in EDITABLE})
        return values

    def save_settings(self, patch):
        if set(patch)-EDITABLE: raise ValueError('Недопустимая настройка')
        path=self.root/'config.json'
        raw=json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else {}
        raw.update(patch)
        if 'response_profile' in patch:
            from bot.config import RESPONSE_PROFILES
            raw.update(RESPONSE_PROFILES.get(patch['response_profile'], {}))
        known=Config.__dataclass_fields__
        validated=Config(**{k:v for k,v in raw.items() if k in known})
        validated.validate()
        if not validated.wake_word.strip() or len(validated.wake_word)>50:
            raise ValueError('Имя обращения должно содержать от 1 до 50 символов')
        if type(validated.auto_join_channel_id) is not int or validated.auto_join_channel_id<0:
            raise ValueError('Некорректный голосовой канал')
        self.backup(path)
        atomic_json(path,raw)

    def backup(self, path):
        if path.exists():
            directory=self.root/'backups'; directory.mkdir(exist_ok=True)
            (directory/(path.stem+'-'+time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6]+path.suffix)).write_bytes(path.read_bytes())

    def persona(self):
        path=self.root/'PERSONA.md'
        return read_shared(path,'utf-8-sig') if path.exists() else ''

    def save_persona(self, text):
        if not text.strip() or len(text)>10000: raise ValueError('Персона: от 1 до 10000 символов')
        path=self.root/'PERSONA.md'; self.backup(path)
        temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
        try:
            temporary.write_text(text,encoding='utf-8'); replace_file(temporary,path)
        finally: temporary.unlink(missing_ok=True)

    def connection(self, name, readonly=False):
        path=self.root/'data'/(name+'.sqlite3')
        if not path.exists(): return None
        db=sqlite3.connect(path.as_uri()+('?mode=ro' if readonly else '?mode=rw'),uri=True,timeout=2)
        db.row_factory=sqlite3.Row
        if not readonly: db.execute('PRAGMA secure_delete=ON')
        return db

    def memory_rows(self, kind='notes', guild=0):
        if kind not in ('notes','people','aliases','examples'): raise ValueError('Неизвестная таблица')
        db=self.connection('learning' if kind=='examples' else 'memory',True)
        if db is None: return []
        try:
            where=' WHERE t.guild=?' if guild else ''
            args=(int(guild),) if guild else ()
            query='SELECT t.rowid AS record_id,t.* FROM '+kind+' t'+where+' ORDER BY t.rowid DESC LIMIT 2000'
            rows=[dict(row) for row in db.execute(query,args)]
            if kind=='notes':
                people={(r['guild'],r['uid']):r['nick'] for r in db.execute('SELECT * FROM people')}
                for row in rows:
                    row['author_name']='Владелец · панель' if row['source']==0 else people.get((row['guild'],row['source']),str(row['source']))
                    row['target_name']=people.get((row['guild'],row['target']),row['alias'] or 'Общее')
            elif kind=='people':
                people={(r['guild'],r['uid']):r['nick'] for r in rows}
                for row in rows:
                    aliases=db.execute('SELECT name,source,confirmed FROM aliases WHERE guild=? AND uid=?',
                        (row['guild'],row['uid'])).fetchall()
                    row['remembered_names']='; '.join(r['name']+' · '+('сам представился' if r['confirmed'] else
                        'сообщил '+people.get((row['guild'],r['source']),str(r['source']))) for r in aliases)
            return rows
        finally: db.close()

    def mutate_memory(self, action, payload):
        allowed={'memory_add','memory_delete','memory_forget','memory_clear','learning_delete','learning_clear'}
        if action not in allowed: raise ValueError('Неизвестная операция с памятью')
        guild=int(payload.get('guild',0))
        if guild<=0: raise ValueError('Выберите сервер для удаления')
        if action=='memory_add':
            from bot.memory import MemoryStore, operational_instruction, normalized
            text=str(payload.get('content','')).strip()
            if not 1<=len(text)<=400: raise ValueError('Заметка: от 1 до 400 символов')
            if operational_instruction(text) or re.search(r'парол|токен|\bip\b|айпи|секретн\w* ключ',text,re.I):
                raise ValueError('Сюда сохраняются сведения. Характер изменяйте в разделе «Персона»; секреты не сохраняются.')
            memory=MemoryStore(self.root/'data/memory.sqlite3')
            try:
                slot='owner:'+hashlib.sha256(normalized(text).encode()).hexdigest()[:24]
                if not memory.save(guild,0,slot,text): raise ValueError('Память заполнена')
            finally: memory.close()
            return
        names=['memory','learning'] if action in ('memory_forget','memory_clear') else [
            'learning' if action.startswith('learning_') else 'memory']
        for name in names:
            db=self.connection(name)
            if db is None: continue
            try:
                with db:
                    if action in ('memory_delete','learning_delete'):
                        table='examples' if name=='learning' else 'notes'
                        db.execute('DELETE FROM '+table+' WHERE guild=? AND rowid=?',(guild,int(payload['record_id'])))
                    elif action=='memory_forget':
                        uid=int(payload['uid'])
                        if name=='learning':
                            db.execute('DELETE FROM examples WHERE guild=? AND (author=? OR context_author=?)',(guild,uid,uid))
                        else:
                            db.execute('DELETE FROM notes WHERE guild=? AND (source=? OR target=?)',(guild,uid,uid))
                            db.execute('DELETE FROM aliases WHERE guild=? AND (source=? OR uid=?)',(guild,uid,uid))
                            db.execute('DELETE FROM people WHERE guild=? AND uid=?',(guild,uid))
                    else:
                        for table in (['examples'] if name=='learning' else ['notes','aliases','people']):
                            db.execute('DELETE FROM '+table+' WHERE guild=?',(guild,))
            finally: db.close()

    def logs(self, token=''):
        path=self.root/'logs/serega.log'
        if not path.exists(): return 'Журнал появится после первого запуска.'
        with path.open('rb') as stream:
            stream.seek(max(0,path.stat().st_size-60000)); text=stream.read().decode('utf-8',errors='replace')
        return redact(text,token)
