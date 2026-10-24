"""Explicit local regression check from the owner's saved corrections."""
import argparse,asyncio,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from bot.config import ROOT,Config
from bot.feedback import FeedbackStore,evaluate_feedback
from desktop.storage import atomic_json

async def main(args):
    cfg=Config.load();store=FeedbackStore(ROOT/'data/learning.sqlite3')
    try:cases=store.cases(args.guild)
    finally:store.close()
    from desktop.models import installed_models
    if args.model.casefold() not in {name.casefold() for name in installed_models(cfg.ollama_host)}:
        raise ValueError('Модель не установлена локально')
    rows=await evaluate_feedback(cfg.ollama_host,args.model,cases)
    atomic_json(args.output,{'model':args.model,'passed':sum(r['pass'] for r in rows),'total':len(rows),
        'rule':'Короткие ответы — точное совпадение; длинные — совпадение слов не менее 75%. Это проверка по правилу, не оценка смысла.',
        'cases':rows})

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--guild',type=int,required=True)
    p.add_argument('--model',required=True);p.add_argument('--output',type=Path,required=True)
    asyncio.run(main(p.parse_args()))
