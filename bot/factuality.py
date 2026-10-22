"""Clarify missing context and require current evidence for current events."""
import re
from .memory import MEMORY_MARKER, MEMORY_OPERATION


def missing_context_reply(messages):
    """Clarify a narrow set of unresolved references without guessing a fact.

    Human history disables this shortcut; assistant guesses are not evidence.
    This intentionally does not attempt general coreference resolution.
    """
    def normalize(content):
        content = re.sub(r'^\[[^\n]*\]:\s*', '', content)
        return ' '.join(re.findall(r'\w+', content.lower().replace('ё', 'е')))

    if not messages or messages[-1].get('role') != 'user':
        return None
    greeting = r'(?:(?:привет|здравствуй|здравствуйте|здарова|серега|сережа|как дела|ты тут|ты здесь|да|нет|ага)\s*)+'
    for item in messages[:-1]:
        if item.get('content', '').startswith((MEMORY_MARKER, MEMORY_OPERATION)):
            continue
        value = normalize(item.get('content', ''))
        if item.get('role') == 'user' and value and not re.fullmatch(greeting, value):
            return None
    value = re.sub(r'\s+', ' ', normalize(messages[-1].get('content', '')))
    abstract_word = (r'(?:это|так|уже|еще|точно|вообще|снова|все|не|же|пока|'
                     r'сделал[аи]?|сделает|сделают|поступил[аи]?|закончил[аи]?|'
                     r'пришел|пришла|пришли|придет|придут|готов[аы]?|работает|знает)')
    person = (r'(?:а\s+)?(?:(?:почему|зачем|когда)\s+)?(?:он|она|они)\s+' +
              abstract_word + r'(?:\s+' + abstract_word + r')*')
    if re.fullmatch(person, value):
        return 'О ком речь и что именно нужно выяснить?'
    if re.fullmatch(r'(?:а\s+)?(?:это (?:правда|точно|работает|правильно|нормально)|'
                    r'что (?:ты )?(?:думаешь|скажешь) об этом|что ты об этом думаешь|'
                    r'как тебе это|что это|как это работает|я правильно сделал[а]?|'
                    r'у меня получилось|с этим все в порядке)', value):
        return 'О чём именно ты спрашиваешь?'
    return None


def freshness_reply(text):
    value = text.lower().replace('ё', 'е')
    if re.search(r'\b(?:придумай|сочини|вообрази)\b', value):
        return None
    # A supplied excerpt can be analysed as supplied text. This does not make
    # it independently verified news, and never grants any additional tools.
    supplied = re.search(r'(?:текст (?:патча|обновления)|патчноуты|из патчноутов|вот изменения)\s*:', value)
    if supplied and len(value[supplied.end():].split()) >= 10:
        return None
    subject = re.search(r'патч|обновлен|новост|релиз|версии|версию|версия', value)
    temporal = re.search(r'свеж|последн|\bнов(?:ый|ое|ом|ые|ая|ого|ей|ых)\b|сегодня|вчера|сейчас|'
                         r'на этой неделе|недавн|актуальн', value)
    specific_patch = re.search(r'патч\w*\s+(?:номер\s+)?\d+\.\d+', value)
    if not subject or not (temporal or specific_patch or 'новости' in value):
        return None
    if re.search(r'\bdota\s*2?\b|\bдот(?:а|ы|у|е|ой)\b', value):
        return ('У меня нет проверенных свежих патчноутов Dota 2, поэтому изменения и новых героев '
                'придумывать не буду. Пришли текст обновления — разберу, что изменилось и как это влияет на игру.')
    return ('У меня нет актуального проверенного источника об этом обновлении или событии. '
            'Пришли его текст — разберу факты и последствия, без выдуманных подробностей.')
