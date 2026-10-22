"""Explicit, bounded local transcript tracing for a requested debug run."""
import json
import os
import time
from .config import ROOT
from .security import safe_output


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
