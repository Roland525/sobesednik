"""Run the pinned upstream converter locally, with bounded CPU use."""
import argparse
import os
from pathlib import Path
import runpy
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bot.config import ROOT


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--vocab-only', action='store_true')
    args = parser.parse_args()
    converter = ROOT / 'tools/llama-cpp/src/llama.cpp-b11827/convert_hf_to_gguf.py'
    if not converter.is_file():
        raise RuntimeError('Run tools/download_llama_converter.py first.')
    if args.output.exists():
        raise RuntimeError('Output exists; choose a new candidate file.')
    os.environ.update(OMP_NUM_THREADS='4', MKL_NUM_THREADS='4', HF_HUB_OFFLINE='1',
                      TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1')
    import torch
    torch.set_num_threads(4)
    sys.path.insert(0, str(converter.parent))
    sys.argv = [str(converter), str(args.model), '--outfile', str(args.output),
                '--outtype', 'bf16', '--use-temp-file']
    if args.vocab_only:
        sys.argv.append('--vocab-only')
    runpy.run_path(str(converter), run_name='__main__')


if __name__ == '__main__':
    main()
