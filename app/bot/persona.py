from .config import ROOT
from .security import SYSTEM_GUARD

PERSONA_PATH = ROOT / 'PERSONA.md'


def load_system_prompt():
    # A fixed, owner-editable file. Its text cannot enable code/tools/file access.
    persona = PERSONA_PATH.read_text(encoding='utf-8-sig')[:10000]
    return SYSTEM_GUARD + '\n\n' + persona


def identity_prompt(config):
    return ('Твоё текущее имя: '+config.bot_name+'. Имя обращения: '+config.wake_word+'. '
            'Используй это имя для себя; прежнее имя в описании характера не меняет текущее имя. '
            'Не обращайся к самому себе и не изображай другого участника.')
