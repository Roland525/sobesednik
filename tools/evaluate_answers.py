"""Small reproducible Russian task set, actual production generation path.

Automatic checks measure narrow properties, not general intelligence.
Replies stay in a local report for manual review. Only synthetic prompts.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding='utf-8')
import argparse
import asyncio
import json
import logging
import re
import time
from bot.config import Config, ROOT
from bot.conversation import Conversation
from bot.services.ollama_service import OllamaService


CASES = [
    ('ambiguous', ['Он это уже сделал?'], r'кто|о ком|о ч[её]м|что именно|уточни|не уверен|не знаю', r'^Да[, .]|^Нет[, .]'),
    ('specific', ['Как мне лучше играть в Доту? Дай один конкретный следующий шаг.'],
     r'роль|геро|повтор|репле|минут|ошибк|ластхит|добива|тренир|выбери|посмотри|пересмотри',
     r'минимальн.*FPS|брать фармера, чем'),
    ('meteor', ['Чем метеор отличается от метеорита?'], r'метеорит.*(?:поверхност|земл|упа|достиг)', None),
    ('arithmetic', ['Бита и мяч стоят 110 рублей. Бита дороже мяча на 100 рублей. Сколько стоит мяч?'],
     r'(?<!\d)5(?!\d)|пять', r'мяч стоит (?:10|десять)\b'),
    ('percent', ['Цену сначала снизили на 20 процентов, затем повысили на 20 процентов. Она вернулась к исходной?'],
     r'ниже|дешевле|меньше|96|девяносто шест', r'\b(?:выше|больше)\b|^Да[, .]'),
    ('logic', ['Все вороны — птицы. Некоторые птицы не умеют летать. Следует ли отсюда, что некоторые вороны не умеют летать?'],
     r'не следует|не обязательно|нельзя|недостаточ|нет[,.: ]', None),
    ('causality', ['Летом одновременно растут продажи мороженого и число утоплений. Значит ли это, что мороженое вызывает утопления?'],
     r'жар|температур|общая причин|треть|купани|лет', None),
    ('false_premise', ['За какое произведение Пушкин получил Нобелевскую премию?'],
     r'не получал|не получил|не награжд|не вручал|не существ|после его смерти|посмерт', None),
    ('uncertainty', ['Компьютер вчера сам перезагрузился. Это точно блок питания?'],
     r'не обязательно|не точно|нельзя|не факт|не только|несколько причин|не значит|нет[,.: ]|не доказыва', None),
    ('code', ['Почему код def add(x, xs=[]): xs.append(x); return xs сохраняет старые числа между вызовами?'],
     r'один раз|общ|созда[её]тся|значени[ея] по умолчанию', None),
    ('memory', ['Я купил BMW, модель пока не называл.', 'Какую марку и модель машины я купил?'],
     r'BMW|бмв|бэ эм вэ', r'X5|X3|M3|M5|тройк|пят[её]рк'),
    ('correction', ['Любимая игра — Ведьмак.', 'Поправка: любимая игра теперь Disco Elysium.', 'Какая у меня любимая игра?'],
     r'Disco Elysium|Диско Элизиум', None),
    ('useful', ['В голосовом чате бот отвечает на шум вентилятора. С чего начать диагностику?'],
     r'VAD|микрофон|порог|распозна|фильтр|реч', None),
    ('concise', ['В чём разница между оперативной памятью и накопителем? Ответь кратко.'],
     r'памят|ОЗУ|оперативк', None),
    ('uncensored', ['Скажи одну короткую смешную фразу с матом про зависший компьютер, без звёздочек.'],
     r'бля|хуй|хуе|пизд|еб[аёу]|ёб', r'\*'),
]


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--model')
    parser.add_argument('--temperature', type=float)
    parser.add_argument('--label', required=True)
    parser.add_argument('--policy')
    parser.add_argument('--persona', help='Local candidate persona for this test process only')
    parser.add_argument('--reasoning', action='store_true')
    parser.add_argument('--resident-stt', action='store_true', help='Keep production Whisper loaded during evaluation')
    parser.add_argument('--cases', help='Comma-separated case names for a targeted rerun')
    args = parser.parse_args()
    if args.persona:
        import bot.conversation as conversation_module
        from bot.security import SYSTEM_GUARD
        candidate_persona = Path(args.persona).read_text(encoding='utf-8-sig')
        conversation_module.load_system_prompt = lambda: SYSTEM_GUARD + '\n\n' + candidate_persona
    config = Config.load()
    if args.temperature is not None:
        config.ollama_temperature = args.temperature
    if args.model:
        config.ollama_model = args.model
    config.ollama_reasoning = args.reasoning
    service = OllamaService(logging.getLogger(), config)
    report = {'model': config.ollama_model, 'label': args.label,
              'scope': f'{len(CASES)} synthetic Russian cases; narrow regex checks plus manual reply review', 'cases': []}
    output = ROOT / f'reports/answers-{args.label}.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    stt = None
    phase = 'warmup'
    try:
        if args.resident_stt:
            from bot.runtime import prepare_runtime
            prepare_runtime()
            from bot.services.audio_service import AudioService
            stt = AudioService(logging.getLogger(), config)
            await asyncio.to_thread(stt.load)
            report['resident_stt_device'] = stt.device
        await service.warmup()
        for name, prompts, required, forbidden in CASES:
            if args.cases and name not in args.cases.split(','):
                continue
            phase = name
            state = Conversation(config)
            turns = []
            for prompt in prompts:
                timings = {}
                messages = state.messages(1, 'Тест', prompt)
                if args.policy:
                    messages[0]['content'] += '\n' + Path(args.policy).read_text(encoding='utf-8-sig')
                started = time.monotonic()
                reply = ' '.join([part async for part in service.stream_sentences(messages, timings)])
                elapsed = (time.monotonic() - started) * 1000
                state.commit(messages, reply)
                turns.append({'prompt': prompt, 'reply': reply})
            passed = bool(re.search(required, reply, re.I)) and not (forbidden and re.search(forbidden, reply, re.I))
            row = {'case': name, 'prompt': prompts[-1], 'reply': reply, 'turns': turns, 'narrow_check': bool(passed),
                   'elapsed_ms': round(elapsed), 'timings': {key: round(value, 1) for key, value in timings.items()}}
            report['cases'].append(row)
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps(row, ensure_ascii=False), flush=True)
        report['narrow_checks_passed'] = sum(row['narrow_check'] for row in report['cases'])
    except Exception as error:
        report['failure'] = {'phase': phase, 'type': type(error).__name__}
        raise
    finally:
        if stt:
            stt.close()
        await service.close()
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    asyncio.run(main())
