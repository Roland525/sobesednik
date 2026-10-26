"""Local Ollama model discovery and explicit, cancellable downloads."""
import json
import math
from pathlib import Path
import re
import urllib.request

CATALOG = (
    ('huihui_ai/qwen3-abliterated:4b-instruct-2507-q4_K_M', 'Qwen 3 Instruct 2507 · 4B · ~2,5 ГБ'),
    ('huihui_ai/qwen3-abliterated:4b', 'Qwen 3 · 4B · ~2,5 ГБ'),
    ('huihui_ai/qwen3-abliterated:8b', 'Qwen 3 · 8B · ~5 ГБ'),
    ('huihui_ai/qwen3.5-abliterated:9B', 'Qwen 3.5 · 9B · ~6,6 ГБ'),
    ('huihui_ai/qwen3.5-abliterated:4B', 'Qwen 3.5 · 4B · ~3,3 ГБ'),
    ('huihui_ai/qwen3-abliterated:14b', 'Qwen 3 · 14B · ~9 ГБ'),
    ('huihui_ai/qwen3-abliterated:30b-a3b-instruct-2507-q4_K_M', 'Qwen 3 Instruct · 30B-A3B · ~19 ГБ'),
    ('huihui_ai/qwen3.5-abliterated:27b-q4_K', 'Qwen 3.5 · 27B · ~17 ГБ'),
    ('huihui_ai/qwen3.5-abliterated:35b-a3b-q4_K', 'Qwen 3.5 · 35B-A3B · ~24 ГБ'),
    ('huihui_ai/qwen3-abliterated:32b-q4_K_M', 'Qwen 3 · 32B · ~20 ГБ'),
)

MODEL_TITLES = {
    'huihui_ai/qwen3-abliterated:4b-instruct-2507-q4_k_m': 'Быстрые ответы · Qwen 3 4B Instruct',
    'huihui_ai/qwen3-abliterated:4b': 'Быстрые ответы · Qwen 3 4B',
    'huihui_ai/qwen3-abliterated:8b': 'Универсальная · Qwen 3 8B',
    'huihui_ai/qwen3.5-abliterated:9b': 'Дополнительная · Qwen 3.5 9B',
    'huihui_ai/qwen3.5-abliterated:4b': 'Компактная · Qwen 3.5 4B',
    'huihui_ai/qwen3-abliterated:14b': 'Для 16 ГБ VRAM · Qwen 3 14B',
    'huihui_ai/qwen3-abliterated:30b-a3b-instruct-2507-q4_k_m': 'Диалог на мощном ПК · Qwen 3 30B-A3B',
    'huihui_ai/qwen3.5-abliterated:27b-q4_k': 'Для 24 ГБ VRAM · Qwen 3.5 27B',
    'huihui_ai/qwen3.5-abliterated:35b-a3b-q4_k': 'Для 32 ГБ VRAM · Qwen 3.5 35B-A3B',
    'huihui_ai/qwen3-abliterated:32b-q4_k_m': 'Для 32 ГБ VRAM · Qwen 3 32B',
}

# Publisher sizes describe downloads, not required VRAM. These conservative
# GPU guides leave space for the app's bounded context, speech recognition and
# desktop; they are not measurements or hard compatibility limits.
MODEL_GUIDANCE = {
    'huihui_ai/qwen3-abliterated:14b': (16,
        'Плотная модель для сравнения на более сложных вопросах. Требует больше памяти и вычислений, чем 8B; размер не гарантирует более точный ответ.'),
    'huihui_ai/qwen3-abliterated:30b-a3b-instruct-2507-q4_k_m': (24,
        'Кандидат для диалога на мощном ПК: 30,5 млрд параметров всего, около 3,3 млрд активны при генерации. MoE и ответы без отдельного обдумывания снижают объём вычислений; в памяти всё равно нужны все веса.'),
    'huihui_ai/qwen3.5-abliterated:27b-q4_k': (24,
        'Большая плотная модель Qwen 3.5 для сравнения качества на сложных вопросах. Вычисляет всю модель; ради минимальной задержки сначала сравните с 30B-A3B Instruct.'),
    'huihui_ai/qwen3.5-abliterated:35b-a3b-q4_k': (32,
        'MoE для мощных видеокарт: 35 млрд параметров всего, около 3 млрд активны при генерации. Требует памяти под все веса. Обдумывание можно отключить во вкладке «Общение» ради меньшей задержки.'),
    'huihui_ai/qwen3-abliterated:32b-q4_k_m': (32,
        'Большая плотная модель для сравнения качества. При генерации работает вся модель; обычно это больше вычислений, чем у MoE 30B-A3B. Для живого диалога сравните задержку перед выбором.'),
}


def model_title(name):
    if name.casefold() in MODEL_TITLES:return MODEL_TITLES[name.casefold()]
    if name.casefold().startswith('sobesednik-learned:'):return 'Собеседник · обученный Qwen 3 · 8B'
    return name.rsplit('/',1)[-1].split(':',1)[0].replace('-',' ').replace('_',' ').capitalize()


def model_summary(name):
    guidance=MODEL_GUIDANCE.get(name.casefold())
    if guidance:return guidance[1]
    if name.casefold()=='huihui_ai/qwen3-abliterated:4b-instruct-2507-q4_k_m':
        return 'Модель для прямых коротких ответов. Instruct — вариант без отдельного режима обдумывания.'
    if ':4b' in name.casefold():
        return 'Компактная модель: обычно занимает меньше памяти и подходит, если важен быстрый короткий ответ. Качество зависит от вопроса.'
    if name.casefold()=='huihui_ai/qwen3-abliterated:8b':
        return 'Универсальная модель для обычной беседы. Начните с неё, если памяти компьютера достаточно. Компактную можно попробовать ради скорости.'
    return 'Дополнительная локальная модель. Сравните ответы и скорость на своём компьютере: номер версии сам по себе не гарантирует лучшего результата.'


def model_hardware_hint(name,resources=None,compute_mode='auto'):
    guidance=MODEL_GUIDANCE.get(name.casefold())
    if not guidance:return 'Для быстрого ответа модель должна помещаться в видеопамять вместе с контекстом и распознаванием речи. Скорость проверьте локальным тестом.'
    baseline=f'Ориентир для быстрого диалога: от {guidance[0]} ГБ VRAM. Оценка с запасом под контекст и распознавание речи. '
    if compute_mode=='cpu':
        return baseline+'Выбран только процессор: большая модель будет отвечать медленнее. Для скорости выберите автоматическое использование видеокарты.'
    vram=(resources or {}).get('vram_total_gb')
    if not isinstance(vram,(int,float)) or not math.isfinite(vram) or vram<=0:
        return baseline+'Видеопамять не определена; проверьте объём вашей видеокарты перед загрузкой.'
    if vram < guidance[0]-.5:
        return baseline+f'Обнаружено {vram:.0f} ГБ: часть модели может уйти на CPU, ответы будут медленнее. Выберите меньшую модель, если важна задержка.'
    return baseline+f'Обнаружено {vram:.0f} ГБ — объём соответствует ориентиру. Скорость подтвердит локальная проверка; учитывайте другие программы и игры.'


def model_detail(name,size=None,details=None):
    details=details or {}
    match=re.search(r':(\d+(?:\.\d+)?)b',name,re.I)
    parameters=details.get('parameter_size') or (match[1]+'B' if match else '')
    params=f'Параметры: {parameters} (B — миллиарды).' if parameters else 'Число параметров не сообщено Ollama.'
    estimate=next((title.rsplit(' · ',1)[-1] for key,title in CATALOG if key.casefold()==name.casefold()),'')
    disk=f'Установлено: {size/1024**3:.2f} ГиБ.' if size else f'Загрузка: {estimate} (приблизительно).' if estimate else 'Размер Ollama пока неизвестен.'
    quant=f" Квантизация: {details['quantization_level']}." if details.get('quantization_level') else ''
    purpose=' '+model_summary(name)
    return (params+' '+disk+quant+purpose+
            ' Для контекста нужна дополнительная оперативная или видеопамять. Скорость зависит от компьютера, '+
            'длины ответа и размещения модели; проверяйте её локальным тестом. Число параметров не является оценкой интеллекта.')

def valid_model(name):
    return (isinstance(name,str) and bool(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_./:-]{0,160}',name))
            and '..' not in name and '://' not in name
            and not name.lower().endswith('-cloud') and ':cloud' not in name.lower())

def request(host, endpoint, payload=None, timeout=15):
    data=None if payload is None else json.dumps(payload).encode()
    req=urllib.request.Request(host.rstrip('/')+endpoint,data=data,headers={'Content-Type':'application/json'})
    return urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req,timeout=timeout)

def installed_model_info(host):
    with request(host,'/api/tags') as response:
        values=json.load(response).get('models',[])
    return [item for item in values if valid_model(item.get('name'))
            and item.get('size',0)>0 and not item.get('remote_host') and not item.get('remote_model')]


def installed_models(host):
    return [item['name'] for item in installed_model_info(host)]

def local_model_info(folder):
    """Read complete bundled model manifests without starting a model server."""
    root=Path(folder).resolve();base=root/'manifests/registry.ollama.ai'
    def blob(entry):
        digest=entry.get('digest','');size=entry.get('size')
        if not re.fullmatch(r'sha256:[a-f0-9]{64}',digest) or type(size) is not int or size<=0:
            raise ValueError('Invalid model blob')
        path=root/'blobs'/digest.replace(':','-')
        if not path.resolve().is_relative_to(root) or path.stat().st_size!=size:
            raise ValueError('Incomplete model blob')
        return path,size
    rows=[]
    for path in sorted(base.glob('*/*/*')):
        try:
            if not path.is_file() or not path.resolve().is_relative_to(root) or path.stat().st_size>1024**2:continue
            namespace,model,tag=path.relative_to(base).parts
            name=(model if namespace=='library' else namespace+'/'+model)+':'+tag
            if not valid_model(name):continue
            manifest=json.loads(path.read_text(encoding='utf-8'))
            config_path,config_size=blob(manifest['config'])
            if config_size>1024**2:continue
            config=json.loads(config_path.read_text(encoding='utf-8'))
            if config.get('remote_model') or config.get('remote_host'):continue
            layers=manifest['layers']
            if not isinstance(layers,list) or not layers or len(layers)>1000:continue
            size=config_size+sum(blob(entry)[1] for entry in layers)
            rows.append({'name':name,'size':size,'details':{
                'parameter_size':config.get('model_type',''),'family':config.get('model_family',''),
                'quantization_level':config.get('file_type','')}})
        except (OSError,ValueError,KeyError,TypeError,AttributeError):continue
    return rows


def pull_model(host, name, progress, cancel):
    if name not in dict(CATALOG): raise ValueError('Выберите модель из списка загрузки')
    with request(host,'/api/pull',{'model':name,'stream':True},timeout=60) as response:
        for line in response:
            if cancel.is_set(): raise InterruptedError('Загрузка отменена. Её можно продолжить позже.')
            value=json.loads(line)
            if value.get('error'): raise RuntimeError('Ollama: '+str(value['error'])[:300])
            total=value.get('total',0)
            percent=int(100*value.get('completed',0)/total) if total else 0
            progress(percent,'Загрузка модели',str(value.get('status','')))
    if name.casefold() not in {item.casefold() for item in installed_models(host)}:
        raise RuntimeError('Загрузка завершилась без установленной модели. Повторите попытку.')
