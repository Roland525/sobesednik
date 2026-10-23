import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import struct
import threading
import urllib.request
import zipfile

from .storage import atomic_json, install_payload, Profile
from .runtime import Runtime, run_step

PYTHON_URL='https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.zip'
PYTHON_SHA256='4ba90a4ab8990891033d37ff04d2047fdae8948d0d2729a68d3a6a17c585b681'


class Cancelled(InterruptedError): pass


def download_file(url, destination, digest, progress, cancel):
    path=Path(destination)
    if cancel.is_set(): raise Cancelled('Установка отменена')
    if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest()==digest: return
    path.parent.mkdir(parents=True,exist_ok=True); partial=path.with_suffix(path.suffix+'.part')
    try:
        request=urllib.request.Request(url,headers={'User-Agent':'SobesednikSetup/1.0'})
        with urllib.request.urlopen(request,timeout=30) as source, partial.open('wb') as target:
            total=int(source.headers.get('Content-Length',0)) if hasattr(source,'headers') else 0
            size=0; checksum=hashlib.sha256()
            while True:
                if cancel.is_set(): raise Cancelled('Установка отменена')
                block=source.read(1024*1024)
                if not block: break
                target.write(block); checksum.update(block); size+=len(block)
                progress(size,total)
        if checksum.hexdigest()!=digest: raise ValueError('Контрольная сумма Python не совпала. Повторите загрузку.')
        partial.replace(path)
    finally: partial.unlink(missing_ok=True)


def extract_zip(archive, target):
    target=Path(target).resolve()
    with zipfile.ZipFile(archive) as z:
        for info in z.infolist():
            p=PurePosixPath(info.filename)
            if p.is_absolute() or '..' in p.parts or '\\' in info.filename or ':' in info.filename:
                raise ValueError('Недопустимый путь внутри архива Python')
            if info.file_size>200_000_000: raise ValueError('Слишком большой файл в архиве')
            path=target/info.filename
            if not path.resolve().is_relative_to(target): raise ValueError('Путь распаковки выходит за папку')
        z.extractall(target)


class Installer:
    def __init__(self, root, payload, progress, cancel=None, runtime=None):
        self.root=Path(root); self.payload=Path(payload); self.progress=progress
        self.cancel=cancel or threading.Event(); self.runtime=runtime or Runtime(root)

    def run(self, stt_model='large-v3-turbo'):
        if stt_model not in ('small','large-v3-turbo'): raise ValueError('Неизвестная модель распознавания')
        if struct.calcsize('P')!=8: raise RuntimeError('Для приложения нужна 64-битная Windows')
        if self.runtime.running(): raise RuntimeError('Остановите бота перед установкой или обновлением')
        if shutil.disk_usage(self.root).free<20*1024**3:
            raise RuntimeError('Нужно минимум 20 ГБ свободного места для моделей и установки')
        # A previous failed run may still own an Ollama bootstrap inside .venv.
        self.runtime.close_server()
        (self.root/'.desktop-ready.json').unlink(missing_ok=True)
        self.progress(2,'Подготовка','Проверяем исходники и сохраняем существующие данные')
        install_payload(self.payload,self.root)
        runtime_dir=self.root/'runtime'; python=runtime_dir/'python.exe'
        if not python.exists() or not (runtime_dir/'.complete').exists():
            self.progress(5,'Python','Скачиваем отдельный Python; системный Python не меняется')
            archive=self.root/'downloads/python-3.11.9-amd64.zip'
            download_file(PYTHON_URL,archive,PYTHON_SHA256,
                lambda done,total:self.progress(5+int(10*done/total) if total else 5,
                    'Python',f'{done//1048576} / {total//1048576} МБ'),self.cancel)
            extract_zip(archive,runtime_dir)
            (runtime_dir/'.complete').write_text(PYTHON_SHA256,encoding='ascii')
        def step(percent,title,args,timeout=3600,env=None):
            if self.cancel.is_set(): raise Cancelled('Установка отменена')
            self.progress(percent,title,'')
            run_step(args,self.root,python,lambda line:self.progress(percent,title,line),
                     self.cancel,timeout,env)
        venv=self.runtime.python
        if venv.is_file():
            # A retry must not overwrite an existing Python launcher.
            step(18,'Проверка окружения',[venv,'-c',
                'import sys, pathlib, pip; assert pathlib.Path(sys.prefix).resolve() == pathlib.Path(sys.argv[1]).resolve()',
                str(self.root/'.venv')],60)
        else:
            step(18,'Окружение',[python,'-m','venv',str(self.root/'.venv')],180)
        step(24,'Зависимости',[venv,'-m','pip','install','-r','requirements.txt'])
        Profile(self.root).save_settings({'whisper_model':'models/faster-whisper-'+stt_model})
        step(42,'Голосовые модели',[venv,'tools/download_models.py','--ollama','--tts-engine','supertonic',
                                  '--stt-model',stt_model],14400)
        self.progress(70,'Ollama','Запускаем локальный сервер моделей')
        self.runtime.ensure_ollama(self.cancel)
        config=json.loads((self.root/'config.json').read_text(encoding='utf-8-sig'))
        model=config.get('ollama_model','huihui_ai/qwen3-abliterated:8b')
        request=urllib.request.Request('http://127.0.0.1:11434/api/pull',
            data=json.dumps({'model':model,'stream':True}).encode(),headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(request,timeout=120) as response:
            for line in response:
                if self.cancel.is_set(): raise Cancelled('Установка отменена')
                data=json.loads(line)
                if data.get('error'): raise RuntimeError('Ollama: '+str(data['error'])[:200])
                total=data.get('total',0); done=data.get('completed',0)
                self.progress(70+int(24*done/total) if total else 70,'Разговорная модель',
                    str(data.get('status',''))+(f' · {done//1048576}/{total//1048576} МБ' if total else ''))
        step(96,'Проверка',[venv,'main.py','--check'],180)
        atomic_json(self.root/'.desktop-ready.json',{'schema':1,'stt_model':stt_model,'model':model})
        self.progress(100,'Готово','Откройте Discord, сохраните токен и выберите голосовой канал')
