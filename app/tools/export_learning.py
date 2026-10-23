"""Export private examples without participant IDs; never publish this output."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bot.config import ROOT
from bot.learning import LearningCorpus

parser = argparse.ArgumentParser()
parser.add_argument('--guild', required=True, type=int)
parser.add_argument('--output', type=Path, default=ROOT / 'data/training/examples.jsonl')
args = parser.parse_args()
corpus = LearningCorpus(ROOT / 'data/learning.sqlite3')
try:
    count = corpus.export(args.guild, args.output)
    print(f'Exported {count} private training examples.')
finally:
    corpus.close()
