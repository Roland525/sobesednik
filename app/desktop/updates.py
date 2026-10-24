"""User-initiated Windows updates from this project's public GitHub release."""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import urllib.request
from urllib.parse import urlsplit
import uuid

from .storage import atomic_json
from .version import VERSION

REPO = 'https://github.com/Roland525/sobesednik'
LATEST = 'https://api.github.com/repos/Roland525/sobesednik/releases/latest'
MAX_INSTALLER = 250 * 1024**2


@dataclass(frozen=True)
class UpdateInfo:
    version: str
    url: str
    size: int
    sha256: str
    page: str


def version_tuple(value):
    match=re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)',str(value))
    return tuple(map(int,match.groups())) if match else None


def asset_url(url,tag,name):
    expected=REPO+'/releases/download/'+tag+'/'+name
    if url!=expected: raise ValueError('Обновление должно находиться в официальном GitHub-репозитории.')
    return url


def open_url(url):
    request=urllib.request.Request(url,headers={'User-Agent':'Sobesednik/'+VERSION,
                                               'Accept':'application/vnd.github+json' if url==LATEST else '*/*'})
    response=urllib.request.urlopen(request,timeout=20)
    final=urlsplit(response.geturl())
    if final.scheme!='https' or final.hostname not in {
            'api.github.com','github.com','release-assets.githubusercontent.com','objects.githubusercontent.com'}:
        response.close(); raise ValueError('Недопустимый адрес загрузки обновления.')
    return response


def read_url(url,limit=1024*1024):
    with open_url(url) as source:
        data=source.read(limit+1)
    if len(data)>limit: raise ValueError('Слишком большой ответ сервера обновлений.')
    return data


def release_update(value,current=VERSION):
    if not isinstance(value,dict): raise ValueError('Не удалось прочитать выпуск GitHub.')
    tag=value.get('tag_name',''); version=version_tuple(tag); installed=version_tuple(current)
    if not installed: raise ValueError('Неизвестная версия приложения.')
    if value.get('draft') or value.get('prerelease') or not version or version<=installed: return None
    number='.'.join(map(str,version)); name='Sobesednik-Setup-'+number+'.exe'
    assets=value.get('assets',[])
    matches=[item for item in assets if item.get('name')==name]
    if len(matches)!=1: raise ValueError('В новом выпуске пока нет установщика Windows. Повторите проверку позже.')
    item=matches[0]; url=asset_url(item.get('browser_download_url'),tag,name)
    size=item.get('size',0)
    if type(size) is not int or not 0<size<=MAX_INSTALLER: raise ValueError('Некорректный размер установщика.')
    digest=item.get('digest') or ''
    sha=digest[7:] if re.fullmatch(r'sha256:[0-9a-fA-F]{64}',digest) else ''
    if not sha:
        checks=next((a for a in assets if a.get('name') in ('SHA256SUMS-'+number+'.txt','SHA256SUMS.txt')),None)
        if checks:
            content=read_url(asset_url(checks.get('browser_download_url'),tag,checks['name']),16384).decode('utf-8-sig')
            for line in content.splitlines():
                match=re.fullmatch(r'([0-9a-fA-F]{64})\s+\*?'+re.escape(name),line.strip())
                if match: sha=match[1]; break
    if not sha: raise ValueError('Выпуск не содержит контрольную сумму SHA-256. Установка не начата.')
    return UpdateInfo(number,url,size,sha.lower(),REPO+'/releases/tag/'+tag)


def check_update(current=VERSION):
    return release_update(json.loads(read_url(LATEST)),current)


def verified(path,info):
    if not path.is_file() or path.stat().st_size!=info.size: return False
    checksum=hashlib.sha256()
    with path.open('rb') as stream:
        while block:=stream.read(1024*1024): checksum.update(block)
    return checksum.hexdigest()==info.sha256


def download_update(info,root,progress,cancel):
    if cancel.is_set(): raise InterruptedError('Загрузка обновления отменена.')
    folder=Path(root)/'downloads/updates'/info.version; folder.mkdir(parents=True,exist_ok=True)
    path=folder/('Sobesednik-Setup-'+info.version+'.exe')
    if verified(path,info): return path
    partial=folder/(path.name+'.'+uuid.uuid4().hex+'.part')
    try:
        count=0; digest=hashlib.sha256(); last=-1
        with open_url(info.url) as source, partial.open('wb') as target:
            while block:=source.read(256*1024):
                if cancel.is_set(): raise InterruptedError('Загрузка обновления отменена.')
                count+=len(block)
                if count>info.size or count>MAX_INSTALLER: raise ValueError('Размер обновления не совпадает с выпуском.')
                target.write(block); digest.update(block)
                percent=int(count*100/info.size)
                if percent!=last: progress(percent,'Обновление приложения',f'Скачано {count/1024**2:.1f} из {info.size/1024**2:.1f} МБ'); last=percent
        if cancel.is_set(): raise InterruptedError('Загрузка обновления отменена.')
        if count!=info.size or digest.hexdigest()!=info.sha256: raise ValueError('Контрольная сумма обновления не совпала. Установщик не запущен.')
        partial.replace(path)
        return path
    finally: partial.unlink(missing_ok=True)


def helper_script(config_path):
    literal=str(config_path).replace("'","''")
    return r'''$ErrorActionPreference = 'Stop'
$cfg = Get-Content -LiteralPath '__CONFIG__' -Raw -Encoding UTF8 | ConvertFrom-Json
$report = Join-Path $cfg.root 'reports\app-update-result.json'
try {
    Wait-Process -Id $cfg.parent_pid -Timeout 60 -ErrorAction SilentlyContinue
    if (Get-Process -Id $cfg.parent_pid -ErrorAction SilentlyContinue) { throw 'Previous application did not exit.' }
    $hash = (Get-FileHash -LiteralPath $cfg.installer -Algorithm SHA256).Hash
    if ($hash.ToLowerInvariant() -ne $cfg.sha256) { throw 'Installer checksum mismatch.' }
    $setupArgs = @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART',
                   ('/DIR="' + $cfg.install_dir + '"'),('/LOG="' + $cfg.log + '"'))
    $setup = Start-Process -FilePath $cfg.installer -ArgumentList $setupArgs -WindowStyle Hidden -Wait -PassThru
    if ($setup.ExitCode -ne 0) { throw ('Installer exit code: ' + $setup.ExitCode) }
    @{ok=$true;version=$cfg.version} | ConvertTo-Json | Set-Content -LiteralPath $report -Encoding UTF8
    $appArgs = @('--data-dir',('"' + $cfg.root + '"'))
    if ($cfg.resume_channel -ge 0) { $appArgs += @('--resume-after-update',[string]$cfg.resume_channel) }
    Start-Process -FilePath $cfg.app -ArgumentList $appArgs -WindowStyle Normal
} catch {
    @{ok=$false;version=$cfg.version;error=$_.Exception.Message} | ConvertTo-Json | Set-Content -LiteralPath $report -Encoding UTF8
    if (Test-Path -LiteralPath $cfg.app) {
        Start-Process -FilePath $cfg.app -ArgumentList @('--data-dir',('"' + $cfg.root + '"')) -WindowStyle Normal
    }
}
'''.replace('__CONFIG__',literal)


def start_installer(info,installer,root,app,parent_pid,resume_channel=-1):
    installer,root,app=Path(installer).resolve(),Path(root).resolve(),Path(app).resolve()
    if not installer.is_relative_to(root/'downloads/updates') or not verified(installer,info):
        raise ValueError('Установщик обновления не прошёл проверку.')
    (root/'reports').mkdir(exist_ok=True)
    suffix=uuid.uuid4().hex; config=installer.parent/('handoff-'+suffix+'.json')
    script=config.with_suffix('.ps1')
    atomic_json(config,{'parent_pid':int(parent_pid),'installer':str(installer),'app':str(app),
                        'install_dir':str(app.parent),'root':str(root),'resume_channel':int(resume_channel),
                        'sha256':info.sha256,'version':info.version,'log':str(root/'logs/app-update-install.log')})
    (root/'logs').mkdir(exist_ok=True)
    script.write_text(helper_script(config),encoding='utf-8-sig')
    powershell=Path(os.environ.get('SystemRoot','C:/Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe'
    return subprocess.Popen([str(powershell),'-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(script)],
                            stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0,close_fds=True)
