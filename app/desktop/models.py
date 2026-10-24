"""Local Ollama model discovery and explicit, cancellable downloads."""
import json
import re
import urllib.request

CATALOG = (
    ('huihui_ai/qwen3-abliterated:4b-instruct-2507-q4_K_M', 'Qwen 3 Instruct 2507 · 4B · около 2,5 ГБ'),
    ('huihui_ai/qwen3-abliterated:4b', 'Qwen 3 · 4B · быстрая · около 2,5 ГБ'),
    ('huihui_ai/qwen3-abliterated:8b', 'Qwen 3 · 8B · около 5 ГБ'),
    ('huihui_ai/qwen3.5-abliterated:9B', 'Qwen 3.5 · 9B · требовательная · около 6 ГБ'),
    ('huihui_ai/qwen3.5-abliterated:4B', 'Qwen 3.5 · 4B · экспериментальная · около 3,3 ГБ'),
)

def valid_model(name):
    return (isinstance(name,str) and bool(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_./:-]{0,160}',name))
            and '..' not in name and '://' not in name
            and not name.lower().endswith('-cloud') and ':cloud' not in name.lower())

def request(host, endpoint, payload=None, timeout=15):
    data=None if payload is None else json.dumps(payload).encode()
    req=urllib.request.Request(host.rstrip('/')+endpoint,data=data,headers={'Content-Type':'application/json'})
    return urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req,timeout=timeout)

def installed_models(host):
    with request(host,'/api/tags') as response:
        values=json.load(response).get('models',[])
    return [item['name'] for item in values if valid_model(item.get('name'))
            and item.get('size',0)>0 and not item.get('remote_host') and not item.get('remote_model')]

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
