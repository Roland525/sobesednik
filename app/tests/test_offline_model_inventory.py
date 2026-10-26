import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from desktop import models

class OfflineModelInventoryTests(unittest.TestCase):
    def test_complete_local_model_is_listed_without_launching_a_server(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);blobs=root/'blobs';blobs.mkdir()
            weights=b'synthetic model';config=json.dumps({'model_type':'14B','model_family':'qwen3','file_type':'Q4_K_M'}).encode()
            def blob(data):
                digest='sha256:'+hashlib.sha256(data).hexdigest()
                (blobs/digest.replace(':','-')).write_bytes(data)
                return {'digest':digest,'size':len(data)}
            manifest={'config':blob(config),'layers':[blob(weights)]}
            path=root/'manifests/registry.ollama.ai/huihui_ai/qwen3-abliterated/14b'
            path.parent.mkdir(parents=True);path.write_text(json.dumps(manifest))
            rows=models.local_model_info(root)
            self.assertEqual(len(rows),1)
            self.assertEqual(rows[0]['name'],'huihui_ai/qwen3-abliterated:14b')
            self.assertEqual(rows[0]['size'],len(weights)+len(config))
            self.assertEqual(rows[0]['details']['parameter_size'],'14B')
            (blobs/manifest['layers'][0]['digest'].replace(':','-')).unlink()
            self.assertEqual(models.local_model_info(root),[])

    def test_unknown_corrupt_and_unsafe_manifests_cannot_break_inventory(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            self.assertEqual(models.local_model_info(root),[])
            base=root/'manifests/registry.ollama.ai/library/test';base.mkdir(parents=True)
            (base/'bad').write_text('bad JSON')
            (base/'unsafe').write_text(json.dumps({'config':{'digest':'../../outside','size':1},'layers':[]}))
            self.assertEqual(models.local_model_info(root),[])
