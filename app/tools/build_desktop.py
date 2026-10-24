"""Reproducible allowlisted payload + native Windows desktop installer."""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def build_environment():
    env=os.environ.copy(); windows=Path(env.get('SystemRoot','C:/Windows'))
    env['PATH']=os.pathsep.join(map(str,[Path(sys.executable).parent,Path(sys.base_prefix),windows/'System32',windows]))
    for key in ('QT_PLUGIN_PATH','QT_QPA_PLATFORM_PLUGIN_PATH','PYTHONPATH','PYTHONHOME'):
        env.pop(key,None)
    return env


def prepare(build):
    from tools.build_release import release_files,audit
    for name in ['LGPL-3.0.txt','GPL-3.0.txt']:
        if not (ROOT/'packaging/licenses'/name).is_file(): raise RuntimeError('Missing Qt license: '+name)
    files=release_files(); audit(ROOT,files)
    content={p.as_posix():(ROOT/p).read_bytes().replace(b'\r\n',b'\n') for p in files}
    manifest={'schema':1,'files':{p:hashlib.sha256(data).hexdigest() for p,data in content.items()}}
    (ROOT/'RELEASE_MANIFEST.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    build.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(build/'backend.zip','w',zipfile.ZIP_DEFLATED) as archive:
        for p,data in content.items(): archive.writestr(p,data)
        archive.writestr('RELEASE_MANIFEST.json',json.dumps(manifest,indent=2))
    licenses=build/'licenses'; licenses.mkdir(exist_ok=True)
    for package in ('PySide6-Essentials','shiboken6','psutil','pyinstaller'):
        distribution=importlib.metadata.distribution(package)
        for item in distribution.files or []:
            if 'license' in item.name.lower() or '/licenses/' in str(item).lower():
                source=Path(distribution.locate_file(item))
                if source.is_file():
                    target=licenses/package/Path(str(item)).name
                    target.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(source,target)
    shutil.copy2(ROOT/'LICENSE',licenses/'Sobesednik-MIT.txt')
    shutil.copy2(ROOT/'NOTICES.md',licenses/'NOTICES.md')
    for source in (ROOT/'packaging/licenses').glob('*.txt'):
        shutil.copy2(source,licenses/source.name)
    from PySide6.QtGui import QGuiApplication,QImage,QPainter,QColor,QPen
    from PySide6.QtCore import Qt
    app=QGuiApplication.instance() or QGuiApplication([])
    icon=QImage(256,256,QImage.Format_ARGB32); icon.fill(QColor('#17251f'))
    painter=QPainter(icon); painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(Qt.NoPen); painter.setBrush(QColor('#284e42')); painter.drawRoundedRect(8,8,240,240,68,68)
    painter.setPen(QPen(QColor('#afffd9'),19,Qt.SolidLine,Qt.RoundCap))
    for x,height in [(68,51),(108,108),(148,156),(188,75)]: painter.drawLine(x,128-height//2,x,128+height//2)
    painter.end(); icon.save(str(build/'icon.png'))
    png=(build/'icon.png').read_bytes()
    (build/'icon.ico').write_bytes(struct.pack('<HHH',0,1,1)+struct.pack('<BBBBHHII',0,0,0,0,1,32,len(png),22)+png)
    print(json.dumps({'payload_files':len(files),'payload_bytes':(build/'backend.zip').stat().st_size}))


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--iscc',type=Path)
    parser.add_argument('--prepare-only',action='store_true'); parser.add_argument('--version',default='1.0.2')
    args=parser.parse_args(); build=ROOT/'.desktop-build'
    prepare(build)
    if args.prepare_only: return
    subprocess.run([sys.executable,'-m','PyInstaller','--noconfirm','--clean','--onedir','--windowed',
        '--name','Sobesednik','--icon',str(build/'icon.ico'),'--distpath',str(build/'frozen'),
        '--workpath',str(build/'pyinstaller'),'--specpath',str(build),
        '--add-data',str(build/'backend.zip')+';assets','--add-data',str(build/'licenses')+';licenses',
        '--add-data',str(build/'icon.png')+';assets',
        str(ROOT/'desktop_entry.py')],cwd=ROOT,check=True,env=build_environment())
    if args.iscc:
        subprocess.run([str(args.iscc),'/DAppVersion='+args.version,'/DBuildRoot='+str(build),
            str(ROOT/'packaging/installer.iss')],check=True,cwd=ROOT)
        assets=list((build/'installer').glob('*.exe'))
        (build/'installer/SHA256SUMS.txt').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.name+'\n' for p in assets),encoding='ascii')


if __name__=='__main__': main()
