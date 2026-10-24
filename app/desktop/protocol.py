"""Per-user local file IPC, signed and limited to explicit operations."""
import hashlib
import hmac
import json
import secrets
import time
import uuid
from pathlib import Path
from .storage import atomic_json, read_shared

OPERATIONS={'join','leave','mute','reset','replace_context','memory_add','memory_delete','memory_forget',
            'memory_clear','learning_delete','learning_clear'}


class Protocol:
    def __init__(self, root):
        self.root=Path(root); self.directory=self.root/'temp/desktop'
        self.commands=self.directory/'commands'; self.responses=self.directory/'responses'
        self.commands.mkdir(parents=True,exist_ok=True); self.responses.mkdir(exist_ok=True)
        key=self.directory/'key'
        try:
            with key.open('x',encoding='ascii') as stream: stream.write(secrets.token_hex(32))
        except FileExistsError: pass
        self.key=key.read_text(encoding='ascii').strip().encode()
        if len(self.key)!=64: raise ValueError('Повреждён ключ локального управления')

    def signature(self, value):
        unsigned={k:v for k,v in value.items() if k!='signature'}
        return hmac.new(self.key,json.dumps(unsigned,sort_keys=True,ensure_ascii=True).encode(),hashlib.sha256).hexdigest()

    def request(self, op, payload, created=None):
        if op not in OPERATIONS: raise ValueError('Недопустимая команда панели')
        rid=uuid.uuid4().hex
        value={'id':rid,'operation':op,'payload':payload,'created':time.time() if created is None else created}
        value['signature']=self.signature(value); atomic_json(self.commands/(rid+'.json'),value)
        return rid

    def valid(self, value):
        try:
            return (len(value['id'])==32 and all(x in '0123456789abcdef' for x in value['id'])
                    and value['operation'] in OPERATIONS and isinstance(value['payload'],dict)
                    and 0<=time.time()-float(value['created'])<120
                    and hmac.compare_digest(value['signature'],self.signature(value)))
        except (KeyError,TypeError,ValueError): return False

    def status(self):
        try:
            data=json.loads(read_shared(self.directory/'state.json'))
            if time.time()-float(data.get('updated',0))>8: return {}
            return data
        except (OSError,ValueError,TypeError): return {}

    def response(self, rid):
        path=self.responses/(rid+'.json')
        if not path.exists(): return None
        try: return json.loads(read_shared(path))
        finally: path.unlink(missing_ok=True)
