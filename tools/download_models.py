"""Explicit setup-only downloads. Runtime inference never downloads models."""
from pathlib import Path
import argparse
import json
import os
import shutil
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]


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
    if not wake.is_dir():
        archive = models / 'vosk-model-small-ru-0.22.zip'
        download('https://alphacephei.com/vosk/models/vosk-model-small-ru-0.22.zip', archive)
        with zipfile.ZipFile(archive) as source:
            source.extractall(models)
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
    if args.ollama and not (ROOT / 'tools/ollama/ollama.exe').exists():
        # Use the official release manifest to select the Windows portable distribution.
        request = urllib.request.Request('https://api.github.com/repos/ollama/ollama/releases/latest',
                                         headers={'User-Agent': 'Serega-local-setup'})
        with urllib.request.urlopen(request, timeout=60) as response:
            release = json.load(response)
        asset = next(x for x in release['assets'] if x['name'] == 'ollama-windows-amd64.zip')
        archive = ROOT / 'tools/ollama-windows-amd64.zip'
        download(asset['browser_download_url'], archive)
        (ROOT / 'reports').mkdir(exist_ok=True)
        (ROOT / 'reports/ollama-release.json').write_text(
            json.dumps({'version': release['tag_name'], 'url': asset['browser_download_url']}, indent=2))
        with zipfile.ZipFile(archive) as source:
            source.extractall(ROOT / 'tools/ollama')
    print('Local models ready.', flush=True)


if __name__ == '__main__':
    main()
