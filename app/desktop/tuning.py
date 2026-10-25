import json
from pathlib import Path
from .runtime import run_step


def release_candidates(candidates):
    from .models import request
    names={c['model'].casefold() for c in candidates}
    try:
        with request('http://127.0.0.1:11434','/api/ps',timeout=2) as response:loaded=json.load(response).get('models',[])
        for item in loaded:
            if item.get('name','').casefold() in names:
                with request('http://127.0.0.1:11434','/api/generate',{'model':item['name'],'keep_alive':0},timeout=5):pass
    except (OSError,ValueError):pass


def run_tuning(root,candidates,cancel,progress,script=None):
    root=Path(root);output=root/'reports/hardware-tuning.json'
    script=Path(script) if script else root/'tools/tune_runtime.py'
    try:
        run_step([root/'.venv/Scripts/python.exe',script,'--root',root,'--output',output,
                  '--candidates',json.dumps(candidates)],root,root/'.venv/Scripts/python.exe',progress,cancel,1800)
    finally:release_candidates(candidates)
    report=json.loads(output.read_text(encoding='utf-8-sig'))
    if not report.get('complete'):raise RuntimeError('Сравнение не завершено; рекомендация не применяется.')
    return report


def format_tuning(report):
    lines=[report.get('conditions','')]
    for row in report.get('rows',[]):
        result=row.get('error') or f"{row['passed']}/{row['total']} · до PCM {row['first_pcm_ms']/1000:.2f} с"
        mode={'auto':'Автоматически','cpu':'Только процессор'}.get(row.get('compute_mode'),'режим не указан')
        lines.append(f"{row['model']} · {mode} · {result}")
    best=report.get('recommendation')
    lines.append('Рекомендация: '+best['model'] if best else 'Подходящий вариант по этим проверкам не найден.')
    return '\n\n'.join(lines)
