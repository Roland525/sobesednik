"""Export an explicit source allowlist, never the working tree or Git history."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

ROOT_FILES = ['.gitignore', '.gitattributes', '.env.example', 'config.example.json', 'LICENSE', 'NOTICES.md',
              'PERSONA.md', 'readme.md', 'requirements.txt', 'requirements-training.txt',
              'requirements-media.txt', 'main.py', 'setup.bat', 'setup_learning.bat', 'start.bat', 'stop.bat']
TOOLS = ['setup.ps1', 'setup_learning.ps1', 'configure_token.ps1', 'ensure_ollama.ps1',
         'download_models.py', 'download_learning_base.py', 'download_llama_converter.py',
         'enable_learning.py', 'process_tree.py', 'export_learning.py', 'train_local_lora.py',
         'merge_lora_candidate.py', 'convert_candidate_gguf.py', 'run_learning_cycle.py',
         'evaluate_answers.py', 'evaluate_local_lora.py', 'check_training_preparation.py',
         'create_promo_audio.py', 'render_promo.py', 'build_release.py']


def release_files():
    files = [Path(name) for name in ROOT_FILES]
    files += [Path('tools') / name for name in TOOLS]
    files += [p.relative_to(ROOT) for directory in ('bot', 'tests', 'tests_training')
              for p in (ROOT / directory).rglob('*.py')]
    files += [Path('bot/vendor/README.md'), Path('bot/vendor/SUPERTONIC_LICENSE'),
              Path('docs/LOCAL_LEARNING.md'), Path('.github/workflows/tests.yml')]
    return sorted(set(files), key=str)


def audit(folder, files, secrets=(), private_ids=()):
    for path in files:
        payload = (folder / path).read_bytes()
        text = payload.decode('utf-8-sig')
        if any(value and value.encode('utf-8') in payload for value in secrets):
            raise RuntimeError('Private credential found in ' + str(path))
        if any(str(value) in text for value in private_ids if value):
            raise RuntimeError('Private Discord identifier found in ' + str(path))
        if re.search(r'gh[pousr]_[A-Za-z0-9]{25,}|[A-Za-z0-9_-]{22,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{25,}', text):
            raise RuntimeError('Possible credential found in ' + str(path))
        if re.search(r'C:[/\\]Users[/\\]super\b', text, re.I):
            raise RuntimeError('Private absolute path found in ' + str(path))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    destination = args.output.resolve()
    if not destination.is_relative_to(ROOT.parent.resolve()) or destination == ROOT or ROOT in destination.parents:
        raise ValueError('Choose a new sibling release folder inside this task workspace.')
    if destination.exists():
        raise RuntimeError('Release destination already exists; existing files are never overwritten.')
    from bot.config import Config
    config = Config.load()
    files = release_files()
    audit(ROOT, files, [config.discord_token], [config.auto_join_channel_id])
    destination.mkdir(parents=True)
    for path in files:
        target = destination / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / path, target)
        target.write_bytes(target.read_bytes().replace(b'\r\n', b'\n'))
    audit(destination, files, [config.discord_token], [config.auto_join_channel_id])
    manifest = {str(path).replace('\\','/'): hashlib.sha256((destination/path).read_bytes()).hexdigest() for path in files}
    (destination / 'RELEASE_MANIFEST.json').write_text(json.dumps({'schema': 1, 'files': manifest}, indent=2), encoding='utf-8')
    print(json.dumps({'output': str(destination), 'files': len(files), 'credentials_audit': 'passed',
                      'bytes': sum((destination/path).stat().st_size for path in files)}))


if __name__ == '__main__':
    main()
