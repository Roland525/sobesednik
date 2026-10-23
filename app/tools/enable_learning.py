"""Explicit optional setup action; preserve credentials and a private backup."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bot.config import Config, ROOT
from tools.run_learning_cycle import atomic_json, prerequisites, disk_ready


def main():
    Config.load()
    if not prerequisites() or not disk_ready():
        raise RuntimeError('Complete setup_learning.bat and leave 55 GB free before enabling learning.')
    path = ROOT / 'config.json'
    original = path.read_bytes()
    raw = json.loads(original.decode('utf-8-sig'))
    backup = ROOT / 'backups' / ('config-before-learning-' + datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S') + '.json')
    backup.parent.mkdir(exist_ok=True)
    backup.write_bytes(original)
    raw.update(learning_capture_enabled=True, learning_auto_enabled=True)
    atomic_json(path, raw)
    print('Learning capture and idle maintenance enabled; private config backup saved.')


if __name__ == '__main__':
    main()
