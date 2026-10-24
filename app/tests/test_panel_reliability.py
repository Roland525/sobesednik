import json,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
from bot.config import Config
from bot.memory import MemoryStore,memory_request
from bot.conversation import Conversation
from desktop.storage import Profile,atomic_json

class PanelReliability(unittest.TestCase):
 def test_windows_replace_retries_and_retains_complete_file(self):
  with tempfile.TemporaryDirectory() as folder:
   p=Path(folder)/'state.json';atomic_json(p,{'old':1});real=Path.replace;calls=[]
   def replace(src,dest):
    calls.append(1)
    if len(calls)<3:
     self.assertEqual(json.loads(p.read_text()),{'old':1});raise PermissionError('reader holds file')
    return real(src,dest)
   with patch.object(Path,'replace',replace),patch('desktop.storage.time.sleep'):
    atomic_json(p,{'new':2})
   self.assertEqual(json.loads(p.read_text()),{'new':2});self.assertEqual(len(calls),3)
   self.assertFalse(list(Path(folder).glob('*.tmp')))
 def test_owner_note_survives_reopen_and_retrieves_in_correct_guild(self):
  with tempfile.TemporaryDirectory() as folder:
   p=Profile(folder);p.mutate_memory('memory_add',{'guild':1,'content':'По пятницам играем в шахматы'})
   self.assertEqual(len(Profile(folder).memory_rows('notes',1)),1)
   self.assertEqual(Profile(folder).memory_rows('notes',2),[])
   m=MemoryStore(Path(folder)/'data/memory.sqlite3');m.register(1,21,'Тестер')
   data=json.loads(m.context(1,21,'Когда играем в шахматы?'))
   self.assertEqual(data['notes'][0]['source_id'],0)
   self.assertIn('панель',data['notes'][0]['source_nick']);m.close()
 def test_explicit_voice_memory_survives_reopen_and_recall(self):
  with tempfile.TemporaryDirectory() as folder:
   path=Path(folder)/'m.db';m=MemoryStore(path)
   for prefix in ['Смотри, запомни: ','Я хочу, чтобы ты запомнил: ']:
    self.assertIsNotNone(memory_request(prefix+'моя любимая игра — шахматы'))
    result=m.handle(1,21,'Тестер',prefix+'моя любимая игра — шахматы')
    self.assertIn('Запомнил',result.reply)
   m.close();m=MemoryStore(path)
   self.assertIn('шахматы',m.handle(1,21,'Тестер','Какая моя любимая игра?').reply);m.close()
 def test_fast_profile_does_not_change_speech_speed_or_persona(self):
  with tempfile.TemporaryDirectory() as folder:
   p=Profile(folder);p.save_persona('Дружеский характер');p.save_settings({'tts_speed':1.06,'response_profile':'fast'})
   cfg=Config.load(Path(folder)/'config.json')
   self.assertTrue(cfg.ollama_reasoning);self.assertEqual(cfg.whisper_beam_size,1)
   self.assertEqual(cfg.tts_supertonic_steps,6);self.assertEqual(cfg.tts_speed,1.06)
   self.assertEqual(p.persona(),'Дружеский характер')
 def test_history_budget_also_applies_without_memory(self):
  c=Conversation(Config(ollama_num_ctx=512,ollama_num_predict=100))
  c.history.extend([{'role':'user','content':'old '*500},{'role':'assistant','content':'old answer'}])
  messages=c.messages(1,'Тестер','Сколько будет два плюс два?')
  self.assertEqual(len(messages),2);self.assertIn('два',messages[-1]['content'])
 def test_model_download_filters_cloud_models(self):
  from desktop.models import valid_model
  self.assertTrue(valid_model('huihui_ai/qwen3.5-abliterated:4B'))
  for model in ['https://remote','model:cloud','qwen-cloud','../escape']:self.assertFalse(valid_model(model))
 def test_shared_windows_reads_do_not_block_atomic_status_updates(self):
  import threading
  from desktop.storage import read_shared
  with tempfile.TemporaryDirectory() as folder:
   p=Path(folder)/'state.json';atomic_json(p,{'n':0});done=threading.Event();errors=[]
   def reader():
    while not done.is_set():
     try:json.loads(read_shared(p))
     except Exception as error:errors.append(type(error).__name__)
   thread=threading.Thread(target=reader);thread.start()
   try:
    for n in range(150):atomic_json(p,{'n':n,'context':['speech']*30})
   finally:done.set();thread.join()
   self.assertEqual(errors,[])
   self.assertEqual(json.loads(read_shared(p))['n'],149)
