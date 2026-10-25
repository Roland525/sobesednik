"""Explicit setup-only downloads. Runtime inference never downloads models."""
from pathlib import Path, PurePosixPath
import argparse
import hashlib
import json
import os
import shutil
import stat
import urllib.request
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]
VOSK_REQUIRED = ('am/final.mdl', 'conf/mfcc.conf', 'conf/model.conf',
                 'graph/disambig_tid.int', 'graph/Gr.fst', 'graph/HCLr.fst',
                 'graph/phones/word_boundary.int', 'ivector/final.dubm',
                 'ivector/final.ie', 'ivector/final.mat', 'ivector/global_cmvn.stats',
                 'ivector/online_cmvn.conf', 'ivector/splice.conf')
OLLAMA_REQUIRED = ('ollama.exe', 'lib/ollama/ggml-base.dll')
COMPLETE = '.component-complete.json'


def archive_members(archive, prefix=''):
    """Validate the entire ZIP before writing any component files."""
    members = {}; seen = set(); total = 0
    for info in archive.infolist():
        path = PurePosixPath(info.filename)
        if (path.is_absolute() or '..' in path.parts or '\\' in info.filename
                or ':' in info.filename or stat.S_ISLNK(info.external_attr >> 16)):
            raise ValueError('Unsafe component archive path')
        if prefix:
            if not path.parts or path.parts[0] != prefix:
                raise ValueError('Unexpected component archive directory')
            path = PurePosixPath(*path.parts[1:])
        if info.is_dir(): continue
        name = path.as_posix()
        if (not path.parts or name == COMPLETE or name.casefold() in seen
                or any(part.endswith((' ', '.')) for part in path.parts)):
            raise ValueError('Invalid or duplicate component archive member')
        seen.add(name.casefold()); total += info.file_size
        if len(seen) > 20000 or total > 16 * 1024**3:
            raise ValueError('Component archive is too large')
        members[name] = info
    if not members: raise ValueError('Empty component archive')
    return members


def component_ready(target, required, cached=None, prefix=''):
    target = Path(target)
    if not all((target/name).is_file() and (target/name).stat().st_size > 0 for name in required):
        return False
    marker = target/COMPLETE
    try:
        if marker.exists():
            files = json.loads(marker.read_text(encoding='utf-8'))['files']
            if not isinstance(files, dict) or not files: return False
            for name, size in files.items():
                relative = PurePosixPath(name)
                if (relative.is_absolute() or '..' in relative.parts or '\\' in name or ':' in name
                        or not (target/name).is_file() or (target/name).stat().st_size != size):
                    return False
            return set(required).issubset(files)
        # Old installations have no marker. Their retained ZIP provides a full inventory.
        if cached and Path(cached).is_file():
            with zipfile.ZipFile(cached) as archive:
                members = archive_members(archive, prefix)
                return all((target/name).is_file() and (target/name).stat().st_size == info.file_size
                           for name, info in members.items())
        return True
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile):
        return False


def extract_component(archive_path, target, required, prefix=''):
    """Publish only a fully extracted component; retries ignore abandoned stages."""
    target = Path(target); target.parent.mkdir(parents=True, exist_ok=True)
    stage = target.with_name(target.name+'.stage-'+uuid.uuid4().hex)
    previous = target.with_name(target.name+'.previous-'+uuid.uuid4().hex)
    try:
        with zipfile.ZipFile(archive_path) as archive:
            members = archive_members(archive, prefix)
            if not set(required).issubset(members): raise ValueError('Incomplete component archive')
            stage.mkdir()
            for name, info in members.items():
                path = stage/name; path.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, path.open('wb') as destination:
                    shutil.copyfileobj(source, destination, 1024 * 1024)
            files = {name: info.file_size for name, info in members.items()}
            (stage/COMPLETE).write_text(json.dumps({'schema': 1, 'files': files}), encoding='utf-8')
            if not component_ready(stage, required): raise ValueError('Incomplete extracted component')
        if target.exists(): target.rename(previous)
        try: stage.rename(target)
        except BaseException:
            if previous.exists() and not target.exists(): previous.rename(target)
            raise
    except zipfile.BadZipFile:
        # A corrupt cached download must not poison every subsequent retry.
        Path(archive_path).unlink(missing_ok=True)
        raise
    finally:
        # These are unique, locally generated siblings, never paths supplied by the ZIP.
        if stage.exists(): shutil.rmtree(stage)
        if previous.exists() and target.exists(): shutil.rmtree(previous)


def download(url, destination):
    destination = Path(destination)
    if destination.exists():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + '.part')
    print('Downloading', destination.name, flush=True)
    with urllib.request.urlopen(url, timeout=120) as response, partial.open('wb') as target:
        shutil.copyfileobj(response, target, 1024 * 1024)
    partial.replace(destination)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--ollama', action='store_true')
    parser.add_argument('--stt-model', choices=['small', 'large-v3-turbo'], default='large-v3-turbo')
    parser.add_argument('--tts-engine', choices=['piper', 'supertonic'])
    args = parser.parse_args()
    models = ROOT / 'models'
    models.mkdir(exist_ok=True)
    voice = 'ru_RU-denis-medium.onnx'
    base = 'https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/denis/medium/'
    for filename in (voice, voice + '.json', 'MODEL_CARD'):
        download(base + filename, models / ('piper-MODEL_CARD' if filename == 'MODEL_CARD' else filename))
    wake = models / 'vosk-model-small-ru-0.22'
    archive = models / 'vosk-model-small-ru-0.22.zip'
    if not component_ready(wake, VOSK_REQUIRED, archive, wake.name):
        download('https://alphacephei.com/vosk/models/vosk-model-small-ru-0.22.zip', archive)
        extract_component(archive, wake, VOSK_REQUIRED, wake.name)
    os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
    from huggingface_hub import snapshot_download
    engine = args.tts_engine
    if engine is None:
        settings = ROOT / 'config.json'
        if not settings.exists():
            settings = ROOT / 'config.example.json'
        engine = json.loads(settings.read_text(encoding='utf-8-sig')).get('tts_engine', 'piper') if settings.exists() else 'piper'
    if engine == 'supertonic':
        snapshot_download('supertone-oss-archive/supertonic-3',
                          revision='aafc6e32416a594460b32413efc49d7fe4ce6d46',
                          local_dir=models / 'supertonic-3',
                          allow_patterns=['onnx/*', 'voice_styles/M*.json', 'LICENSE', 'README.md'])
    repository = {'small': 'Systran/faster-whisper-small',
                  'large-v3-turbo': 'mobiuslabsgmbh/faster-whisper-large-v3-turbo'}[args.stt_model]
    snapshot_download(repository, local_dir=models / ('faster-whisper-' + args.stt_model),
                      allow_patterns=['*.json', '*.txt', '*.bin'])
    ollama = ROOT/'tools/ollama'
    if args.ollama and not component_ready(ollama, OLLAMA_REQUIRED, ROOT/'tools/ollama-windows-amd64.zip'):
        # Use the official release manifest to select the Windows portable distribution.
        request = urllib.request.Request('https://api.github.com/repos/ollama/ollama/releases/latest',
                                         headers={'User-Agent': 'Serega-local-setup'})
        with urllib.request.urlopen(request, timeout=60) as response:
            release = json.load(response)
        asset = next(x for x in release['assets'] if x['name'] == 'ollama-windows-amd64.zip')
        # A previous latest release's ZIP is not necessarily the release just selected.
        key = hashlib.sha256(asset['browser_download_url'].encode()).hexdigest()[:16]
        archive = ROOT / ('tools/ollama-windows-amd64-'+key+'.zip')
        download(asset['browser_download_url'], archive)
        extract_component(archive, ollama, OLLAMA_REQUIRED)
        (ROOT / 'reports').mkdir(exist_ok=True)
        (ROOT / 'reports/ollama-release.json').write_text(
            json.dumps({'version': release['tag_name'], 'url': asset['browser_download_url']}, indent=2))
    print('Local models ready.', flush=True)


if __name__ == '__main__':
    main()
