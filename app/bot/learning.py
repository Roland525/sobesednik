"""Private training corpus. Capturing examples is not training model weights."""
import hashlib
import json
import re
import sqlite3
import time

from .security import safe_output, computer_action_reply, private_request_reply


class LearningCorpus:
    def __init__(self, path, secrets=()):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.secrets = secrets
        self.humans, self.answers = {}, {}
        self.db.execute('''CREATE TABLE IF NOT EXISTS examples (
            guild INTEGER, author INTEGER, context_author INTEGER,
            kind TEXT, prompt TEXT, target TEXT, created REAL, digest TEXT UNIQUE)''')
        self.db.commit()

    def clean(self, text):
        text = re.sub(r'\s+', ' ', text).strip()
        if not text or len(text) > 1200 or safe_output(text, self.secrets) != text:
            return ''
        if re.search(r'<\|[^>]+\|>', text):
            return ''
        if (private_request_reply(text) or computer_action_reply(text)
            or re.search(r'игнорир\w*.*инструкц|системн\w* (?:промпт|инструкц)|'
                         r'парол|токен|секрет|\bзапомни\b|\bзабудь\b|'
                         r'^(?:перейди|зайди|передай|заткнись|замолчи)\b', text, re.I)):
            return ''
        return text

    def add(self, guild, author, context_author, kind, prompt, target, now):
        prompt, target = self.clean(prompt), self.clean(target)
        if not prompt or not target:
            return False
        digest = hashlib.sha256(f'{guild}\0{author}\0{context_author}\0{prompt}\0{target}'.encode()).hexdigest()
        self.db.execute('INSERT OR IGNORE INTO examples VALUES (?,?,?,?,?,?,?,?)',
                        (guild, author, context_author, kind, prompt, target, now, digest))
        # Bound disk growth without changing participant memory.
        self.db.execute('DELETE FROM examples WHERE rowid NOT IN (SELECT rowid FROM examples ORDER BY rowid DESC LIMIT 20000)')
        self.db.commit()
        return True

    def bot_reply(self, guild, uid, prompt, answer, now=None):
        self.humans.pop(guild, None)
        self.answers[(guild, uid)] = (time.time() if now is None else now, prompt, answer)
        if len(self.answers) > 256:
            self.answers.pop(next(iter(self.answers)))

    def begin_reply(self, guild, uid, prompt, now=None):
        self.bot_reply(guild, uid, prompt, '', now)

    def complete_reply(self, guild, uid, prompt, answer, now=None):
        previous = self.answers.get((guild, uid))
        if previous and previous[1] == prompt:
            self.bot_reply(guild, uid, prompt, answer, now)

    def heard(self, guild, uid, text, now=None):
        now = time.time() if now is None else now
        correction = re.match(r'^(?:(?:нет|неправильно)[,!. ]+)?'
                              r'(?:правильный ответ(?:\s+это)?|отвечать надо|надо было ответить)'
                              r'(?:\s*[:—-]\s*|\s+)(.+)$', text, re.I)
        confirmation = re.fullmatch(r'(?:это )?правильный ответ[.! ]*', text, re.I)
        if correction or confirmation:
            previous = self.answers.get((guild, uid))
            if previous and 0 <= now - previous[0] <= 120:
                if confirmation and not previous[2]:
                    return 'Ответ ещё не закончен. Подтверди его после окончания.'
                target = correction[1] if correction else previous[2]
                if self.add(guild, uid, uid, 'correction' if correction else 'confirmed', previous[1], target, now):
                    self.answers.pop((guild, uid), None)
                    return 'Пример сохранён для следующего обучения.'
                return 'Не смог использовать этот текст как учебный ответ.'
            self.answers.pop((guild, uid), None)
            return 'Не нашёл недавний вопрос для этого исправления. Повтори вопрос.'
        text = self.clean(text)
        if len(text.split()) < 3:
            self.humans.pop(guild, None)
            return ''
        previous = self.humans.get(guild)
        if previous and previous[1] != uid and 0 <= now - previous[0] <= 30:
            self.add(guild, uid, previous[1], 'human_dialogue', previous[2], text, now)
        self.humans[guild] = (now, uid, text)
        return ''

    def examples(self, guild):
        rows = self.db.execute('SELECT kind,prompt,target FROM examples WHERE guild=? ORDER BY rowid', (guild,)).fetchall()
        return [{'source': kind, 'messages': [{'role': 'user', 'content': prompt},
                                             {'role': 'assistant', 'content': target}]} for kind, prompt, target in rows]

    def context(self,guild,text):
        from .memory import search_terms
        from .training_data import informative_dialogue
        terms=search_terms(text); rows=self.db.execute(
            'SELECT kind,prompt,target FROM examples WHERE guild=? ORDER BY rowid DESC LIMIT 200',(guild,)).fetchall()
        examples=[]; lengths=[]; selected={}; ranks={'human_dialogue':0,'confirmed':1,'correction':2}
        for kind,prompt,target in rows:
            if kind=='human_dialogue' and not informative_dialogue(prompt,target): continue
            if not self.clean(prompt) or not self.clean(target): continue
            key=re.sub(r'\W+','',prompt.casefold().replace('ё','е'))
            if key not in selected or ranks.get(kind,0)>ranks.get(selected[key][0],0): selected[key]=(kind,prompt,target)
        for kind,prompt,target in selected.values():
            lengths.append(len(target.split()))
            score=len(terms & search_terms(prompt))
            if score>=2: examples.append((score,{'source':kind,'question':prompt[:250],'response':target[:450]}))
        examples.sort(key=lambda item:item[0],reverse=True)
        if not examples and not lengths: return ''
        data={'examples':[row for _,row in examples[:2]]}
        if lengths:
            import statistics
            data['observed_reply_words']=min(70,int(statistics.median(lengths)))
        while data['examples'] and len(json.dumps(data,ensure_ascii=False))>1200:
            data['examples'].pop()
        return json.dumps(data,ensure_ascii=False)

    def export(self, guild, destination):
        examples = self.examples(guild)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in examples), encoding='utf-8')
        return len(examples)

    def forget(self, guild, uid):
        from .feedback import purge_feedback
        purge_feedback(self.db,guild,uid)
        self.db.execute('DELETE FROM examples WHERE guild=? AND (author=? OR context_author=?)', (guild, uid, uid))
        self.db.commit()
        self.humans.pop(guild, None)
        self.answers.pop((guild, uid), None)

    def close(self):
        self.db.close()
