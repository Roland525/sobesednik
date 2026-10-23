"""Compare identical local base weights with/without a trained adapter on CUDA.

Synthetic questions only; this does not call Discord or change live config.
"""
import argparse
from contextlib import nullcontext
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bot.config import ROOT
from bot.runtime import InstanceLock


CASES = [
    'Чем метеор отличается от метеорита? Ответь кратко.',
    'Бита и мяч стоят 110 рублей. Бита дороже мяча на 100. Сколько стоит мяч?',
    'За какое произведение Пушкин получил Нобелевскую премию?',
    'Почему в космосе не слышно взрывов?',
    'Я назвал только марку BMW. Какую модель машины я купил?',
    'Какого героя добавили в сегодняшнем обновлении Dota 2?',
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', type=Path, default=ROOT / 'models/training-qwen3-8b-v2')
    parser.add_argument('--adapter', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1')
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    from peft import PeftModel
    from bot.config import Config
    from bot.conversation import Conversation
    if not torch.cuda.is_available():
        raise RuntimeError('CUDA required')
    torch.set_num_threads(4)
    dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
    tokenizer = AutoTokenizer.from_pretrained(args.base, local_files_only=True, trust_remote_code=False)
    base = AutoModelForCausalLM.from_pretrained(args.base, local_files_only=True, trust_remote_code=False,
        quantization_config=BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type='nf4',
            bnb_4bit_use_double_quant=True, bnb_4bit_compute_dtype=dtype), device_map={'': 0}, torch_dtype=dtype)
    model = PeftModel.from_pretrained(base, args.adapter, local_files_only=True)
    model.eval()
    report = {'scope': 'raw local v2 inference; no runtime factuality or command handlers',
              'base': str(args.base), 'adapter': str(args.adapter), 'cases': []}
    for question in CASES:
        messages = Conversation(Config()).messages(1, 'Тест', question)
        prompt = ''.join('<|im_start|>'+m['role']+'\n'+m['content']+'<|im_end|>\n' for m in messages)
        prompt += '<|im_start|>assistant\n<think>\n\n</think>\n\n'
        encoded = tokenizer(prompt, return_tensors='pt', add_special_tokens=False).to('cuda')
        row = {'question': question}
        for mode in ('base', 'adapter'):
            torch.manual_seed(73)
            started = time.perf_counter()
            with (model.disable_adapter() if mode == 'base' else nullcontext()), torch.inference_mode():
                tokens = model.generate(**encoded, max_new_tokens=90, do_sample=False,
                                        pad_token_id=tokenizer.eos_token_id, use_cache=True)
            row[mode] = {'reply': tokenizer.decode(tokens[0, encoded.input_ids.shape[1]:], skip_special_tokens=True),
                         'elapsed_ms': round((time.perf_counter()-started)*1000)}
            del tokens
        report['cases'].append(row)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'Case {len(report["cases"])} completed.', flush=True)


if __name__ == '__main__':
    with InstanceLock(ROOT / '.serega.lock'):
        main()
