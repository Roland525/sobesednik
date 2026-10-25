"""Private corrections and conservative, reproducible regression checks."""
import json
import re
import sqlite3
import time
from .security import safe_output
from .conversation import addressed


def purge_feedback(db,guild,uid=None):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='feedback'").fetchone():return
    db.execute('DELETE FROM feedback WHERE guild=?'+(' AND uid=?' if uid is not None else ''),
               (guild,uid) if uid is not None else (guild,))


def general_example(question,answer,memories):
    from .learning import LearningCorpus
    from .memory import _ALIASES
    cleaner=object.__new__(LearningCorpus);cleaner.secrets=()
    text=question+' '+answer
    # Conservative exclusion: personal facts stay attributed in memory, never
    # become an anonymous answer target for everyone on the server.
    if re.search(r'сколько\s+лет|возраст|день\s+рожд|зарплат|диагноз|телефон|адрес|'
                 r'\b(?:любит|ненавидит|жив[её]т|работает|зовут|предпочитает)\b',text,re.I):return False
    if any(word.casefold().replace('ё','е') in _ALIASES for word in re.findall(r'\w+',text)):return False
    return (not memories and bool(cleaner.clean(question) and cleaner.clean(answer)) and not re.search(
        r'\b(?:я|меня|мне|мой|моя|моё|мои|мы|нас|наш|наша|ты|тебя|тебе|твой|зовут|живёт|живет|работает)\b',question+' '+answer,re.I))


class FeedbackStore:
    def __init__(self,path,secrets=()):
        # Capture may be disabled: still initialize the shared corpus schema so
        # its existing desktop browse/clear operations remain valid.
        from .learning import LearningCorpus
        corpus=LearningCorpus(path,secrets);corpus.close()
        path.parent.mkdir(parents=True,exist_ok=True);self.db=sqlite3.connect(path,timeout=2)
        self.db.row_factory=sqlite3.Row;self.secrets=secrets
        self.db.executescript('''CREATE TABLE IF NOT EXISTS feedback (
            id INTEGER PRIMARY KEY, guild INTEGER NOT NULL, uid INTEGER NOT NULL, turn_id TEXT NOT NULL,
            kind TEXT NOT NULL, heard TEXT NOT NULL, question TEXT NOT NULL, answer TEXT NOT NULL,
            original_answer TEXT NOT NULL, created REAL NOT NULL, trainable INTEGER NOT NULL,
            UNIQUE(guild,turn_id,kind));''')
        self.db.execute('PRAGMA secure_delete=ON')

    def close(self):self.db.close()

    def add(self,guild,turn,kind,correction,wake_word='Серёга'):
        if kind not in ('question','answer') or not isinstance(correction,str):raise ValueError('Некорректное исправление')
        correction=correction.strip()
        if not 1<=len(correction)<=1200 or safe_output(correction,self.secrets)!=correction:
            raise ValueError('Исправление: 1–1200 символов, без секретов')
        if not turn.get('user_id') or not re.fullmatch(r'[a-f0-9]{32}',turn.get('id','')):
            raise ValueError('Выберите реплику человека из текущего разговора')
        question=addressed(turn.get('question') or turn['heard'],wake_word)[1]
        previous=self.db.execute("SELECT question FROM feedback WHERE guild=? AND turn_id=? AND kind='question'",(guild,turn['id'])).fetchone()
        if previous:question=previous['question']
        if kind=='question':question=correction
        answer=correction if kind=='answer' else ''
        trainable=kind=='answer' and general_example(question,answer,turn.get('memories'))
        invalidated=self.db.execute("SELECT * FROM feedback WHERE guild=? AND turn_id=? AND kind='answer'",(guild,turn['id'])).fetchone()
        with self.db:
            if invalidated and kind=='question':self.db.execute('DELETE FROM feedback WHERE id=?',(invalidated['id'],))
            self.db.execute('''INSERT INTO feedback(guild,uid,turn_id,kind,heard,question,answer,original_answer,created,trainable)
                VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(guild,turn_id,kind) DO UPDATE SET
                question=excluded.question,answer=excluded.answer,created=excluded.created,trainable=excluded.trainable''',
                (guild,turn['user_id'],turn['id'],kind,safe_output(turn['heard'],self.secrets),question,answer,
                 safe_output(turn.get('answer',''),self.secrets),time.time(),int(trainable)))
            self.db.execute('DELETE FROM feedback WHERE id NOT IN (SELECT id FROM feedback ORDER BY id DESC LIMIT 1000)')
        record=dict(self.db.execute('SELECT * FROM feedback WHERE guild=? AND turn_id=? AND kind=?',(guild,turn['id'],kind)).fetchone())
        record['invalidated']=dict(invalidated) if invalidated else None
        return record

    def cases(self,guild):
        return [dict(row) for row in self.db.execute("SELECT id,question,answer FROM feedback WHERE guild=? AND trainable=1 AND kind='answer' ORDER BY id DESC LIMIT 32",(guild,))]


def matches_expected(answer,expected):
    numbers=dict(zip('0123456789',['ноль','один','два','три','четыре','пять','шесть','семь','восемь','девять']))
    def words(text):
        return [numbers.get(word,word) for word in re.findall(r'\w+',text.casefold().replace('ё','е'))]
    actual,wanted=words(answer),words(expected)
    if actual[:1]==['ответ'] and wanted[:1]!=['ответ']:actual=actual[1:]
    # Word overlap cannot establish factual equivalence: changing just a city,
    # a number or the subject can reverse a mostly identical answer. This is an
    # automatic weight-promotion gate, so uncertain paraphrases must fail closed.
    return bool(wanted) and actual==wanted


def feedback_gate(baseline,candidate):
    old={row['id']:row for row in baseline};new={row['id']:row for row in candidate}
    if set(old)!=set(new):return ['incomplete_feedback_evaluation']
    return ['feedback_regression'] if any(row.get('pass') is not True for row in new.values()) else []


async def evaluate_feedback(host,model,cases):
    import httpx
    rows=[]
    async with httpx.AsyncClient(timeout=60,trust_env=False) as client:
        for case in cases:
            response=await client.post(host+'/api/chat',json={'model':model,'stream':False,'think':False,
                'messages':[{'role':'system','content':'Отвечай кратко и точно по-русски. Не добавляй вступление.'},
                            {'role':'user','content':case['question']}],
                'options':{'temperature':0,'num_predict':260,'num_ctx':4096,'seed':42}})
            response.raise_for_status();payload=response.json()
            from .security import assert_no_tools
            assert_no_tools(payload)
            answer=payload.get('message',{}).get('content','')
            rows.append({'id':case['id'],'answer':answer,'expected':case['answer'],
                         'pass':matches_expected(answer,case['answer'])})
    return rows
