"""Optional setup-only download. Never called by an inference/training cycle."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = 'huihui-ai/Huihui-Qwen3-8B-abliterated-v2'
REVISION = '7d89db76029281fd8f3e6698a8e30738608105a9'


def main():
    os.environ['HF_HUB_OFFLINE'] = '0'
    os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
    from huggingface_hub import snapshot_download
    snapshot_download(REPOSITORY, revision=REVISION, local_dir=ROOT / 'models/training-qwen3-8b-v2',
                      allow_patterns=['*.json', '*.safetensors', '*.txt', '*.model', 'README.md', 'LICENSE*'])
    print('Pinned local Qwen3 training base downloaded.')


if __name__ == '__main__':
    main()
