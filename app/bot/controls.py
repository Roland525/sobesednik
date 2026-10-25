import re
from .conversation import addressed


def voice_control(text, wake_word='серёга', muted=False):
    hit, body = addressed(text, wake_word)
    value = re.sub(r'[^\w\s]', ' ', body.lower().replace('ё', 'е'))
    value = re.sub(r'\s+', ' ', value).strip()
    fillers = r'(?:пожалуйста|нахуй|блять|блядь|уже|ну)'
    value = re.sub(r'^(?:' + fillers + r'\s+)+|(?:\s+' + fillers + r')+$', '', value).strip()
    # Observed on synthetic Piper -> Vosk/Whisper. These aliases apply only
    # to the exact stop commands below, not to general conversation activation.
    stop_value = re.sub(r'^(?:тюряга|терега|тиряга|тирега)\s+', '', value)
    value = stop_value
    if hit and re.fullmatch(r'зав(?:л[еи]|али)бала', value):
        return 'mute'
    # Whisper turbo can hear the exact stop phrase as "завали и балы".
    # Keep this narrow; arbitrary discussion containing "завали" is not a command.
    if re.fullmatch(r'завали\s+(?:[еи]\s+)?(?:[еи]бало|бало|балы|баллы)', value):
        return 'mute'
    if value in ('заткнись', 'замолчи', 'молчи', 'помолчи', 'завали ебало', 'закрой рот',
                 'хватит говорить', 'не мешай', 'перестань говорить', 'прекрати говорить'):
        return 'mute'
    if re.fullmatch(r'можешь (?:помолчать|замолчать|заткнуться)', value):
        return 'mute'
    if (hit or muted) and value in ('говори', 'можешь говорить', 'продолжай', 'возвращайся',
                                     'не молчи', 'просыпайся', 'можно говорить'):
        return 'unmute'
    return None


def addresses_someone_else(text, names, wake_word='серёга'):
    if addressed(text, wake_word)[0]:
        return False
    value = text.lower().replace('ё', 'е')
    for name in names:
        name = re.sub(r'[^\w\s-]', '', name.lower().replace('ё', 'е')).strip()
        if len(name) >= 3 and re.match(r'^\s*(?:эй\s*[,!]?\s*)?' + re.escape(name) + r'\b', value):
            return True
        if len(name)>=3 and re.search(r',\s*'+re.escape(name)+r'\s*[.!?…]*\s*$',value):
            return True
    return False


def screen_request(text):
    value = text.lower().replace('ё', 'е')
    return bool((re.search(r'(?:демк\w*|демонстраци\w*|экран\w*|стрим\w*)', value) and
                 re.search(r'(?:посмотри|смотри|глянь|зайди|видишь|гляди|покаж|сколько)', value)) or
                re.search(r'(?:посмотри|глянь|видишь).*(?:выиграл|денег|баланс|сумм)', value))


SCREEN_UNAVAILABLE = ('Я не получаю кадры демонстрации Discord через этот голосовой клиент. '
                      'Сумму или содержимое экрана я сейчас не вижу и придумывать не буду.')
