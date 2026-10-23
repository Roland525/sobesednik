import logging
from logging.handlers import RotatingFileHandler
import re
from .config import ROOT


class Redact(logging.Filter):
    def __init__(self, secrets=()):
        super().__init__()
        self.secrets = tuple(value for value in secrets if value and len(value) > 5)

    def filter(self, record):
        message = record.getMessage()
        for value in self.secrets:
            message = message.replace(value, '[SECRET]')
        message = re.sub(r'(Bot|Bearer)\s+[A-Za-z0-9_.-]{20,}', r'\1 [SECRET]', message)
        record.msg, record.args = message, ()
        return True


class SecretFormatter(logging.Formatter):
    def __init__(self, template, secrets=()):
        super().__init__(template)
        self.secrets = [value for value in secrets if value and len(value) > 5]

    def format(self, record):
        # Also sanitize exception tracebacks, which logging.Filter cannot see.
        message = super().format(record)
        for value in self.secrets:
            message = message.replace(value, '[SECRET]')
        return re.sub(r'(Bot|Bearer)\s+[A-Za-z0-9_.-]{20,}', r'\1 [SECRET]', message)


def setup_logger(secrets=()):
    directory = ROOT / 'logs'
    directory.mkdir(exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()
    formatter = SecretFormatter('%(asctime)s %(levelname)s %(name)s: %(message)s', secrets)
    for handler in (logging.StreamHandler(), RotatingFileHandler(
            directory / 'serega.log', maxBytes=3_000_000, backupCount=3, encoding='utf-8')):
        handler.setFormatter(formatter)
        handler.addFilter(Redact(secrets))
        root.addHandler(handler)
    # The dependency's DEBUG output can contain transport secret keys/raw audio.
    logging.getLogger('discord.ext.voice_recv').setLevel(logging.WARNING)
    logging.getLogger('httpx').setLevel(logging.WARNING)
    metrics = logging.getLogger('serega.metrics')
    metrics.propagate = False
    metrics.handlers.clear()
    handler = RotatingFileHandler(directory / 'latency.log', maxBytes=2_000_000, backupCount=2, encoding='utf-8')
    handler.setFormatter(formatter)
    metrics.addHandler(handler)
    return logging.getLogger('serega'), metrics
