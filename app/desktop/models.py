"""Local Ollama model discovery and explicit, cancellable downloads."""
import json
import re
import urllib.request

CATALOG = (
    ('huihui_ai/qwen3-abliterated:4b-instruct-2507-q4_K_M', 'Qwen 3 Instruct 2507 · 4B · ~2,5 ГБ'),
    ('huihui_ai/qwen3-abliterated:4b', 'Qwen 3 · 4B · ~2,5 ГБ'),
    ('huihui_ai/qwen3-abliterated:8b', 'Qwen 3 · 8B · ~5 ГБ'),
    ('huihui_ai/qwen3.5-abliterated:9B', 'Qwen 3.5 · 9B · ~6 ГБ'),
    ('huihui_ai/qwen3.5-abliterated:4B', 'Qwen 3.5 · 4B · ~3,3 ГБ'),
)


def model_title(name):
    known={key.casefold():title.rsplit(' · ',1)[0] for key,title in CATALOG}
    if name.casefold() in known:return known[name.casefold()]
    if name.casefold().startswith('sobesednik-learned:'):return 'Собеседник · обученный Qwen 3 · 8B'
    return name.rsplit('/',1)[-1].split(':',1)[0].replace('-',' ').replace('_',' ').capitalize()


def model_detail(name,size=None,details=None):
    details=details or {}
    match=re.search(r':(\d+(?:\.\d+)?)b',name,re.I)
    parameters=details.get('parameter_size') or (match[1]+'B' if match else '')
    params=f'Параметры: {parameters} (B — миллиарды).' if parameters else 'Число параметров не сообщено Ollama.'
    estimate=next((title.rsplit(' · ',1)[-1] for key,title in CATALOG if key.casefold()==name.casefold()),'')
    disk=f'Установлено: {size/1024**3:.2f} ГиБ.' if size else f'Загрузка: {estimate} (приблизительно).' if estimate else 'Размер Ollama пока неизвестен.'
    quant=f" Квантизация: {details['quantization_level']}." if details.get('quantization_level') else ''
    return params+' '+disk+quant+' Для работы нужна дополнительная RAM/VRAM под контекст. Меньше параметров — обычно меньше расход памяти; качество проверяйте локальным тестом.'

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
