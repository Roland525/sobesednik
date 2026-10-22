from .config import ROOT
from .security import SYSTEM_GUARD

PERSONA_PATH = ROOT / 'PERSONA.md'


def load_system_prompt():
    # A fixed, owner-editable file. Its text cannot enable code/tools/file access.
    persona = PERSONA_PATH.read_text(encoding='utf-8-sig')[:10000]
    return SYSTEM_GUARD + '\n\n' + persona
