"""Explicit, bounded local transcript tracing for a requested debug run."""
import json
import os
import time
import copy
import uuid
from collections import deque
from .config import ROOT
from .security import safe_output


def memory_evidence(messages):
    from .memory import MEMORY_MARKER
    rows=[]
    for message in messages:
        if not message.get('content','').startswith(MEMORY_MARKER):continue
        try:data=json.loads(message['content'][len(MEMORY_MARKER):])
        except (ValueError,TypeError):continue
        for note in data.get('notes',[])[:6]:
            rows.append({'content':str(note.get('content',''))[:400],
                         'source':str(note.get('source_nick','участник'))[:64]})
        for person in data.get('people',[])[:4]:
            if person.get('real_name'):
                rows.append({'content':str(person.get('nick','участник'))+' — '+str(person['real_name']),
                             'source':'сам представился' if person.get('confirmed') else 'другой участник'})
    return rows[:10]


class TurnJournal:
    """Bounded local UI evidence, independent of debug tracing and model thoughts."""
    def __init__(self,secrets=(),limit=24):
        self.secrets=secrets;self.rows=deque(maxlen=limit)

    def clean(self,value):
        if isinstance(value,str):return safe_output(value,self.secrets)[:3000]
        if isinstance(value,dict):return {k:self.clean(v) for k,v in value.items()}
        if isinstance(value,list):return [self.clean(v) for v in value[:24]]
        return value

    def heard(self,uid,name,text,reason):
        rid=uuid.uuid4().hex
        self.rows.append(self.clean({'id':rid,'user_id':uid,'name':name,'heard':text,'reason':reason,
                                    'status':'heard','answer':'','memories':[],'at':time.time(),'timings':{}}))
        return rid

    def update(self,rid,**fields):
        row=next((row for row in self.rows if row['id']==rid),None)
        if row is not None:row.update(self.clean(fields))

    def snapshot(self):return copy.deepcopy(list(self.rows))

    def clear(self):self.rows.clear()


class TurnTrace:
    def __init__(self, config):
        self.config = config
        self.until = time.monotonic() + min(180, max(0, int(os.environ.get('SEREGA_TRACE_SECONDS', '0'))))
        self.count = 0
        self.path = ROOT / 'logs/debug-turns.jsonl'
        if self.active:
            self.path.parent.mkdir(exist_ok=True)
            self.path.write_text('', encoding='utf-8')

    @property
    def active(self):
        return self.count < 150 and time.monotonic() < self.until

    def record(self, stage, **data):
        if not self.active:
            return
        row = {'at': round(time.time(), 3), 'stage': stage, **data}
        payload = safe_output(json.dumps(row, ensure_ascii=False), [self.config.discord_token])
        try:
            with self.path.open('a', encoding='utf-8') as output:
                output.write(payload + '\n')
            self.count += 1
        except OSError:
            self.until = 0
