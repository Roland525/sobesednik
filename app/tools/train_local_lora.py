"""Offline QLoRA training. Optional isolated environment; runtime does not import this.

Experimental until a real GPU run and held-out quality comparison pass.
This creates a candidate adapter and never replaces the live model.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bot.config import ROOT
from bot.runtime import InstanceLock


from bot.training_data import training_texts, prepare_samples
from bot.training_memory import prepare_low_memory_qlora


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', type=Path, default=ROOT / 'data/training/examples.jsonl')
    parser.add_argument('--base', type=Path, default=ROOT / 'models/training-qwen3-8b-v2')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--steps', type=int, default=40)
    parser.add_argument('--sequence-length', type=int, default=256)
    parser.add_argument('--min-examples', type=int, default=16)
    args = parser.parse_args()
    if args.output.exists():
        raise RuntimeError('Choose a new candidate directory; existing adapters are never overwritten.')
    if not 1 <= args.steps <= 1000 or not 64 <= args.sequence_length <= 512 or args.min_examples < 2:
        raise ValueError('Invalid training bounds')
    base_config_path = args.base / 'config.json'
    base_config = json.loads(base_config_path.read_text(encoding='utf-8'))
    if base_config.get('model_type') != 'qwen3':
        raise ValueError('This trainer expects the Qwen3 text architecture')
    base_metadata_path = args.base / '.cache/huggingface/download/config.json.metadata'
    base_revision = base_metadata_path.read_text(encoding='utf-8').splitlines()[0] if base_metadata_path.is_file() else None
    rows = [json.loads(line) for line in args.data.read_text(encoding='utf-8').splitlines() if line.strip()]
    if len(rows) < args.min_examples:
        raise RuntimeError(f'Need at least {args.min_examples} examples; found {len(rows)}.')
    # No downloads or telemetry inside a training run.
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1')
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    from peft import LoraConfig, get_peft_model
    if not torch.cuda.is_available():
        raise RuntimeError('CUDA PyTorch is required in the isolated training environment.')
    torch.manual_seed(73)
    torch.set_num_threads(4)
    dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
    tokenizer = AutoTokenizer.from_pretrained(args.base, local_files_only=True, trust_remote_code=False)
    training, heldout, data_stats = prepare_samples(rows, tokenizer, args.sequence_length, args.min_examples)
    model = AutoModelForCausalLM.from_pretrained(args.base, local_files_only=True, trust_remote_code=False,
        quantization_config=BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type='nf4',
            bnb_4bit_use_double_quant=True, bnb_4bit_compute_dtype=dtype), device_map={'': 0}, torch_dtype=dtype)
    model = prepare_low_memory_qlora(model)
    model = get_peft_model(model, LoraConfig(r=8, lora_alpha=16, lora_dropout=0,
                                            target_modules=['q_proj', 'v_proj'], task_type='CAUSAL_LM'))
    physical_cuda_bytes = torch.cuda.get_device_properties(0).total_memory
    print(f'Frozen vocabulary dtype={model.get_input_embeddings().weight.dtype}; '
          f'CUDA allocated={torch.cuda.memory_allocated()}; physical={physical_cuda_bytes}', flush=True)
    def batch(sample):
        ids = torch.tensor([sample[0]], device='cuda')
        return {'input_ids': ids, 'attention_mask': torch.ones_like(ids),
                'labels': torch.tensor([sample[1]], device='cuda')}
    def loss_on(examples):
        model.eval()
        with torch.no_grad(), torch.autocast('cuda', dtype=dtype):
            losses = [float(model(**batch(example)).loss) for example in examples]
        if not all(math.isfinite(value) for value in losses):
            raise RuntimeError('Non-finite held-out loss; candidate was not saved.')
        return sum(losses) / len(losses)
    before = loss_on(heldout)
    parameters = [p for p in model.parameters() if p.requires_grad]
    optimizer = torch.optim.AdamW(parameters, lr=1e-4)
    scaler = torch.amp.GradScaler('cuda', enabled=dtype == torch.float16)
    model.train()
    started, losses = time.perf_counter(), []
    for step in range(args.steps):
        optimizer.zero_grad(set_to_none=True)
        with torch.autocast('cuda', dtype=dtype):
            loss = model(**batch(training[step % len(training)])).loss
        if not torch.isfinite(loss):
            raise RuntimeError('Non-finite loss; candidate was not saved.')
        scaler.scale(loss).backward()
        scaler.unscale_(optimizer)
        torch.nn.utils.clip_grad_norm_(parameters, 1)
        scaler.step(optimizer)
        scaler.update()
        if torch.cuda.max_memory_allocated() > physical_cuda_bytes:
            raise RuntimeError('Training exceeded physical VRAM; reduce sequence length before retrying.')
        losses.append(float(loss.detach()))
        print(f'Step {step+1}/{args.steps}, loss={losses[-1]:.4f}', flush=True)
    after = loss_on(heldout)
    changed = sum(float(p.detach().float().norm()) for name, p in model.named_parameters() if 'lora_B' in name)
    if not changed > 0:
        raise RuntimeError('Adapter weights did not change.')
    args.output.mkdir(parents=True)
    model.save_pretrained(args.output, safe_serialization=True)
    tokenizer.save_pretrained(args.output)
    report = {'scope': 'candidate only; not deployed or verified for general answer quality',
              'base': str(args.base), 'base_revision': base_revision,
              'base_config_sha256': hashlib.sha256(base_config_path.read_bytes()).hexdigest(),
              'data_validation': data_stats, 'steps': args.steps, 'train_examples': len(training), 'heldout_examples': len(heldout),
              'heldout_loss_before': before, 'heldout_loss_after': after,
              'train_loss_first': losses[0], 'train_loss_last': losses[-1], 'lora_B_norm': changed,
              'elapsed_seconds': time.perf_counter()-started,
              'peak_cuda_bytes': torch.cuda.max_memory_allocated(),
              'physical_cuda_bytes': physical_cuda_bytes,
              'frozen_vocabulary_dtype': str(model.get_input_embeddings().weight.dtype),
              'dataset_sha256': hashlib.sha256(args.data.read_bytes()).hexdigest()}
    (args.output / 'training-report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print('Candidate adapter saved. Run a separate answer-quality comparison before deployment.')


if __name__ == '__main__':
    # Real process lock: training cannot start while the Discord runtime holds it.
    with InstanceLock(ROOT / '.serega.lock'):
        main()
