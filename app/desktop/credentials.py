"""Windows user-bound encrypted credentials; no plaintext fallback."""
import ctypes as c
from ctypes import wintypes as w
import os
import json
from pathlib import Path


def _crypt(data, decrypt=False):
    if os.name!='nt': raise RuntimeError('Хранилище токена доступно только в Windows')
    class Blob(c.Structure):
        _fields_=[('size',w.DWORD),('data',c.POINTER(c.c_ubyte))]
    buffer=c.create_string_buffer(data); source=Blob(len(data),c.cast(buffer,c.POINTER(c.c_ubyte))); output=Blob()
    crypt=c.WinDLL('crypt32',use_last_error=True); kernel=c.WinDLL('kernel32',use_last_error=True)
    fn=crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    fn.argtypes=[c.POINTER(Blob),c.c_void_p,c.c_void_p,c.c_void_p,c.c_void_p,w.DWORD,c.POINTER(Blob)]
    fn.restype=w.BOOL
    kernel.LocalFree.argtypes=[c.c_void_p]; kernel.LocalFree.restype=c.c_void_p
    if not fn(c.byref(source),None,None,None,None,1,c.byref(output)):
        raise RuntimeError('Windows не смогла открыть защищённый токен. Введите его заново.')
    try: return c.string_at(output.data,output.size)
    finally: kernel.LocalFree(output.data)


def save_token(root, token):
    token=token.strip()
    if len(token)<30 or any(x.isspace() for x in token): raise ValueError('Скопируйте токен бота полностью')
    path=Path(root)/'discord-token.bin'; temporary=path.with_suffix('.tmp')
    temporary.write_bytes(_crypt(token.encode())); temporary.replace(path)


def load_token(root):
    path=Path(root)/'discord-token.bin'
    if not path.exists():
        # Existing source installations remain usable. Saving through the UI
        # adds DPAPI storage without deleting the owner's original files.
        env=Path(root)/'.env'
        if env.exists():
            for line in env.read_text(encoding='utf-8-sig').splitlines():
                if '=' in line and not line.lstrip().startswith('#'):
                    key,value=line.split('=',1)
                    if key.strip()=='DISCORD_TOKEN' and value.strip(): return value.strip().strip('"\'')
        config=Path(root)/'config.json'
        if config.exists():
            token=json.loads(config.read_text(encoding='utf-8-sig')).get('discord_token','')
            return token.strip() if isinstance(token,str) else ''
        return ''
    return _crypt(path.read_bytes(),True).decode()
