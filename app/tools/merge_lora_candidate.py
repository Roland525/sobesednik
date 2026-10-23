"""Merge a private LoRA candidate into a new CPU Safetensors directory.

Ollama 0.40 imports merged weights. This never changes live config.
"""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bot.config import ROOT
from bot.runtime import InstanceLock


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', type=Path, default=ROOT / 'models/training-qwen3-8b-v2')
    parser.add_argument('--adapter', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise RuntimeError('Output already exists; choose a new candidate directory.')
    settings = json.loads((args.adapter / 'adapter_config.json').read_text(encoding='utf-8'))
    if Path(settings['base_model_name_or_path']).resolve() != args.base.resolve():
        raise ValueError('Adapter was trained against different base weights.')
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1')
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
    from peft import PeftModel
    torch.set_num_threads(4)
    base = AutoModelForCausalLM.from_pretrained(args.base, local_files_only=True,
        trust_remote_code=False, torch_dtype=torch.bfloat16, device_map={'': 'cpu'}, low_cpu_mem_usage=True)
    model = PeftModel.from_pretrained(base, args.adapter, local_files_only=True,
                                     device_map={'': 'cpu'})
    merged = model.merge_and_unload(safe_merge=True)
    if any(value.device.type != 'cpu' for value in merged.parameters()):
        raise RuntimeError('Merged weights must be materialized on CPU before export.')
    merged.save_pretrained(args.output, safe_serialization=True, max_shard_size='2GB')
    tokenizer = AutoTokenizer.from_pretrained(args.base, local_files_only=True, trust_remote_code=False)
    tokenizer.save_pretrained(args.output)
    manifest = {'base': str(args.base.resolve()), 'adapter': str(args.adapter.resolve()),
                'status': 'merged candidate; not deployed', 'dtype': 'bfloat16',
                'training': json.loads((args.adapter / 'training-report.json').read_text(encoding='utf-8'))}
    (args.output / 'candidate-provenance.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print('Merged candidate saved; import and evaluate before deployment.', flush=True)


if __name__ == '__main__':
    with InstanceLock(ROOT / '.serega.lock'):
        main()
