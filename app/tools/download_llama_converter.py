"""Explicit setup-only download of pinned upstream CPU converter assets."""
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile

from download_models import download, ROOT

TAG = 'b11827'


def extract_checked(archive, destination, select=None):
    destination = destination.resolve()
    with ZipFile(archive) as source:
        for entry in source.infolist():
            if entry.is_dir():
                continue
            if select and not select(entry.filename):
                continue
            target = (destination / entry.filename).resolve()
            if not target.is_relative_to(destination):
                raise ValueError('Archive path outside converter directory')
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read(entry))


def main():
    target = ROOT / 'tools/llama-cpp'
    target.mkdir(parents=True, exist_ok=True)
    binary_name = f'llama-{TAG}-bin-win-cpu-x64.zip'
    binary_url = f'https://github.com/ggml-org/llama.cpp/releases/download/{TAG}/{binary_name}'
    source_url = f'https://github.com/ggml-org/llama.cpp/archive/refs/tags/{TAG}.zip'
    binary_zip, source_zip = target / binary_name, target / f'source-{TAG}.zip'
    download(binary_url, binary_zip)
    download(source_url, source_zip)
    extract_checked(binary_zip, target / 'bin')
    extract_checked(source_zip, target / 'src', select=lambda name: (
        name.endswith(('/convert_hf_to_gguf.py', '/convert_lora_to_gguf.py', '/LICENSE'))
        or '/gguf-py/' in name or '/conversion/' in name
        or name.endswith('/requirements/requirements-convert_hf_to_gguf.txt')))
    (target / 'download-manifest.json').write_text(json.dumps({
        'tag': TAG, 'binary_url': binary_url, 'source_url': source_url,
        'sha256': {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in (binary_zip, source_zip)}
    }, indent=2), encoding='utf-8')
    print('Pinned CPU converter downloaded; no model was changed.')


if __name__ == '__main__':
    main()
