"""Small, reproducible local text checks; never reads conversation or persona data."""
import json
import hashlib
import os
import platform
import re
import statistics
import time

from bot.config import compute_settings,normalize_compute
from .models import installed_models,request

CASES=(
    ('arithmetic','Сколько будет 17 + 25? Ответь только числом.','42'),
    ('context','У Лены синий шарф. У Олега красный шарф. Какого цвета шарф Лены? Ответь только одним словом.','синий'),
    ('selection','Выбери единственное животное из списка: стол, кошка, камень. Ответь только выбранным словом.','кошка'),
    ('instruction','Напиши только слово «готово», без пояснений.','готово'),
)


def report_key(model,compute_mode):
    compute_settings(compute_mode)
    identity=json.dumps(['synthetic-exact-v2',model.casefold(),compute_mode],ensure_ascii=False)
    return hashlib.sha256(identity.encode()).hexdigest()[:12]


def passes(answer,expected):
    # Only harmless whitespace, surrounding quotes and trailing punctuation vary.
    return answer.strip().strip('«»"\' .!\n\r\t').casefold()==expected.casefold()


def check_cancel(cancel):
    if cancel.is_set():raise InterruptedError('Проверка модели отменена. Частичный результат не считается завершённым тестом.')


def generate(host,model,prompt,options,cancel):
    check_cancel(cancel)
    start=time.monotonic();answer='';first_sentence=None;complete=False
    payload={'model':model,'messages':[{'role':'user','content':prompt}],
             'stream':True,'think':False,'options':options,'keep_alive':'2m'}
    # Bounds a blocked network read, including cancellation during model loading.
    with request(host,'/api/chat',payload,timeout=20) as response:
        for line in response:
            check_cancel(cancel)
            if time.monotonic()-start>120:raise TimeoutError('Тестовый ответ занял больше 120 секунд.')
            item=json.loads(line)
            if item.get('error'):raise RuntimeError('Ollama: '+str(item['error'])[:300])
            answer+=item.get('message',{}).get('content','')
            if first_sentence is None and answer.strip() and re.search(r'[.!?](?:\s|$)|\n',answer.lstrip()):
                first_sentence=time.monotonic()-start
            if item.get('done'):
                complete=True;break
    check_cancel(cancel)
    if not complete or not answer.strip():raise RuntimeError('Ollama вернула незавершённый или пустой тестовый ответ.')
    elapsed=time.monotonic()-start
    return answer,first_sentence if first_sentence is not None else elapsed,elapsed


def run_benchmark(cfg,cancel,progress=lambda _:None):
    check_cancel(cancel)
    host='http://127.0.0.1:11434';model=cfg['ollama_model']
    if model.casefold() not in {name.casefold() for name in installed_models(host)}:
        raise ValueError('Для теста разрешена только установленная локальная модель.')
    cfg=normalize_compute(cfg)
    mode=cfg['compute_mode'];resources=compute_settings(mode)
    options={key.removeprefix('ollama_'):resources[key] for key in ('ollama_num_gpu','ollama_num_thread')}
    options.update(num_ctx=4096,num_predict=128,temperature=0,seed=42)
    rows=[]
    for repeat in range(2):
        for name,prompt,expected in CASES:
            check_cancel(cancel);progress(f'{len(rows)+1}/{len(CASES)*2} · {model} · {name}')
            answer,first,total=generate(host,model,prompt,options,cancel)
            rows.append({'case':name,'repeat':repeat+1,'prompt':prompt,'expected':expected,
                         'answer':answer,'pass':passes(answer,expected),
                         'first_sentence_seconds':round(first,3),'total_seconds':round(total,3)})
    check_cancel(cancel)
    try:
        with request(host,'/api/ps') as response:loaded=json.load(response).get('models',[])
    except (OSError,ValueError):loaded=[]
    return {'schema':2,'suite':'synthetic-exact-v2','created_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),
            'model':model,'compute_mode':mode,'resource_level':cfg.get('resource_level',70),'resource_settings':resources,
            'options':options,'think':False,'cpu':platform.processor(),'logical_cpu_count':os.cpu_count(),
            'system':platform.platform(),'ollama_loaded_models':loaded,
            'passed':sum(row['pass'] for row in rows),'total':len(rows),
            'median_first_sentence_seconds':round(statistics.median(row['first_sentence_seconds'] for row in rows),3),
            'warmup_performed':False,'first_run_may_load_model':True,'cases':rows}


def format_report(report):
    selected=next((item for item in report['ollama_loaded_models'] if
        item.get('name','').casefold()==report['model'].casefold()),{})
    gpu_bytes=selected.get('size_vram')
    gpu='неизвестно' if gpu_bytes is None else f'{gpu_bytes/1024**3:.2f} ГиБ'
    mode='Только процессор' if report['compute_mode']=='cpu' else 'Автоматически · GPU при поддержке Ollama'
    return (f"{report['model']}\nПроверок по точному правилу: {report['passed']}/{report['total']}"
        f" · медиана до первого предложения: {report['median_first_sentence_seconds']:.2f} с\n"
        f"CPU: {report['cpu'] or 'не определён'} · логических потоков: {report['logical_cpu_count']}\n"
        f"Ускорение: {mode} · потоки модели: {report['options']['num_thread']}"
        f" · GPU в Ollama (num_gpu): {report['options']['num_gpu']} · память модели на GPU: {gpu}\n"
        'Обдумывание выключено, temperature=0, seed=42, контекст 4096, максимум 128 токенов. '
        'Отдельного прогрева нет; первая попытка может включать загрузку модели. Распознавание и синтез не измерялись. '
        'Сравнивайте отчёты при одинаковой нагрузке и настройках. Это 4 коротких задания, не общий рейтинг.')
