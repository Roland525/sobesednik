import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import urllib.request

from tools.process_tree import WindowsJob
from bot.runtime import InstanceLock,AlreadyRunningError
from .credentials import load_token
from .protocol import Protocol
from .storage import redact


def child_env():
    env=os.environ.copy()
    for key in ('PYTHONHOME','PYTHONPATH'): env.pop(key,None)
    env.update(PYTHONUTF8='1',PYTHONUNBUFFERED='1')
    return env


class OwnedProcess:
    def __init__(self, command, root, python, env=None, stdout=None):
        self.job=WindowsJob() if os.name=='nt' else None
        self.child=None
        try:
            self.child=subprocess.Popen([str(python),str(Path(root)/'tools/process_tree.py'),'--bootstrap'],
                cwd=str(root),env=env or child_env(),stdin=subprocess.PIPE,stdout=stdout,
                stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace',
                creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0,
                start_new_session=os.name!='nt')
            if self.job: self.job.assign(self.child)
            self.child.stdin.write(json.dumps([str(x) for x in command])); self.child.stdin.close()
        except BaseException:
            self.close(); raise

    def poll(self):
        return self.child.poll() if self.child else 1

    def close(self):
        if self.job:
            self.job.close(); self.job=None
        elif self.child and self.child.poll() is None:
            self.child.terminate()
        if self.child:
            self.child.wait(timeout=20)


def run_step(command, root, python, progress, cancel, timeout=3600, env=None):
    process=OwnedProcess(command,root,python,env,subprocess.PIPE)
    lines=queue.Queue()
    def read():
        for line in process.child.stdout: lines.put(line.rstrip())
    reader=threading.Thread(target=read,daemon=True); reader.start(); start=time.monotonic()
    try:
        while process.poll() is None or not lines.empty() or reader.is_alive():
            if cancel.is_set(): raise InterruptedError('Операция отменена')
            if time.monotonic()-start>timeout: raise TimeoutError('Этап выполнялся слишком долго. Повторите установку.')
            try: progress(lines.get(timeout=.1))
            except queue.Empty: pass
        if process.poll()!=0: raise RuntimeError('Этап завершился с ошибкой. Подробности выше в журнале установки.')
    finally: process.close()


class Runtime:
    def __init__(self, root):
        self.root=Path(root); self.protocol=Protocol(root)
        self.process=self.server=None; self.log_handle=None; self.server_log=None

    @property
    def python(self): return self.root/'.venv/Scripts/python.exe'

    def installed(self):
        return self.python.is_file() and (self.root/'.desktop-ready.json').is_file()

    def bot_command(self): return [str(self.python),'main.py']

    def running(self):
        if (self.process and self.process.poll() is None) or self.protocol.status(): return True
        try:
            with InstanceLock(self.root/'.serega-supervisor.lock'): pass
        except AlreadyRunningError: return True
        return False

    def ensure_ollama(self, cancel=None):
        def available():
            try:
                with urllib.request.urlopen('http://127.0.0.1:11434/api/tags',timeout=2) as response:
                    json.load(response); return True
            except (OSError,ValueError): return False
        if available(): return
        if self.server:
            self.server.close(); self.server=None
        if self.server_log: self.server_log.close(); self.server_log=None
        executable=self.root/'tools/ollama/ollama.exe'
        if not executable.exists(): raise RuntimeError('Ollama ещё не установлена. Завершите первую настройку.')
        env=child_env()
        env.update(OLLAMA_HOST='127.0.0.1:11434',OLLAMA_MODELS=str(self.root/'models/ollama'),
                   OLLAMA_NO_CLOUD='1',OLLAMA_NUM_PARALLEL='1',OLLAMA_MAX_LOADED_MODELS='1',OLLAMA_FLASH_ATTENTION='1')
        (self.root/'logs').mkdir(exist_ok=True)
        self.server_log=(self.root/'logs/desktop-ollama.log').open('a',encoding='utf-8')
        self.server=OwnedProcess([executable,'serve'],self.root,self.python,env,self.server_log)
        for _ in range(60):
            if cancel and cancel.is_set(): raise InterruptedError('Операция отменена')
            if available(): return
            if self.server.poll() is not None: break
            time.sleep(.3)
        self.server.close(); self.server=None
        self.server_log.close(); self.server_log=None
        raise RuntimeError('Ollama не запустилась. Смотрите журнал desktop-ollama.log.')

    def start(self):
        if self.running(): raise RuntimeError('Бот уже запущен')
        if self.process: self.process.close(); self.process=None
        if self.log_handle: self.log_handle.close(); self.log_handle=None
        if not self.installed(): raise RuntimeError('Сначала завершите установку моделей')
        token=load_token(self.root)
        if not token: raise ValueError('Сначала сохраните токен в разделе Discord')
        self.ensure_ollama()
        env=child_env(); env['DISCORD_TOKEN']=token
        (self.root/'logs').mkdir(exist_ok=True)
        self.log_handle=(self.root/'logs/desktop-startup.log').open('a',encoding='utf-8')
        self.process=OwnedProcess(self.bot_command(),self.root,self.python,env,self.log_handle)

    def stop(self, timeout=40):
        if not self.running():
            if self.process: self.process.close(); self.process=None
            if self.log_handle: self.log_handle.close(); self.log_handle=None
            return
        (self.root/'.serega.stop').write_text('stop',encoding='ascii')
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            if self.process and self.process.poll() is not None: break
            if not self.process and not self.running(): break
            time.sleep(.2)
        if self.process:
            self.process.close(); self.process=None
        elif self.running():
            raise RuntimeError('Не удалось подтвердить остановку. Новая копия не будет запущена.')
        (self.protocol.directory/'state.json').unlink(missing_ok=True)
        if self.log_handle: self.log_handle.close(); self.log_handle=None

    def close(self):
        self.stop()
        if self.server: self.server.close(); self.server=None
        if self.server_log: self.server_log.close(); self.server_log=None


def sample_resources():
    import psutil
    ram=psutil.virtual_memory()
    value={'cpu':psutil.cpu_percent(),'ram_percent':ram.percent,'ram_gb':ram.used/1024**3,
           'ram_total_gb':ram.total/1024**3,'gpu':None,'vram_gb':None,'vram_total_gb':None}
    try:
        result=subprocess.run(['nvidia-smi','--query-gpu=utilization.gpu,memory.used,memory.total',
            '--format=csv,noheader,nounits'],capture_output=True,text=True,timeout=3,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        if result.returncode==0:
            gpu,used,total=[float(x.strip()) for x in result.stdout.splitlines()[0].split(',')]
            value.update(gpu=gpu,vram_gb=used/1024,vram_total_gb=total/1024)
    except (OSError,ValueError,subprocess.TimeoutExpired,IndexError): pass
    return value
