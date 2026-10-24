import json
from pathlib import Path
from .runtime import run_step


def run_tuning(root,candidates,cancel,progress,script=None):
    root=Path(root);output=root/'reports/hardware-tuning.json'
    script=Path(script) if script else root/'tools/tune_runtime.py'
    run_step([root/'.venv/Scripts/python.exe',script,'--root',root,'--output',output,
              '--candidates',json.dumps(candidates)],root,root/'.venv/Scripts/python.exe',progress,cancel,1800)
    report=json.loads(output.read_text(encoding='utf-8-sig'))
    if not report.get('complete'):raise RuntimeError('Сравнение не завершено; рекомендация не применяется.')
    return report


def format_tuning(report):
    lines=[report.get('conditions','')]
    for row in report.get('rows',[]):
        result=row.get('error') or f"{row['passed']}/{row['total']} · до PCM {row['first_pcm_ms']/1000:.2f} с"
        lines.append(f"{row['model']} · {row['resource_level']}/100 · {result}")
    best=report.get('recommendation')
    lines.append('Рекомендация: '+best['model'] if best else 'Подходящий вариант по этим проверкам не найден.')
    return '\n\n'.join(lines)
