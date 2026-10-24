"""Isolated calibration process: resident STT, synthetic speech, LLM and TTS."""
import argparse
import asyncio
from dataclasses import fields
import json
import logging
from pathlib import Path
import statistics
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from bot.config import Config,resource_settings
from bot.runtime import prepare_runtime
from bot.tuning import choose_recommendation
from desktop.model_benchmark import CASES,passes
from desktop.models import installed_models
from desktop.storage import atomic_json


async def measure(root,candidates,output):
    import numpy as np
    from scipy.signal import resample_poly
    from bot.services.audio_service import AudioService
    from bot.services.tts_service import TTSService
    from bot.services.ollama_service import OllamaService
    raw=json.loads((root/'config.json').read_text(encoding='utf-8-sig'))
    cfg=Config(**{f.name:raw[f.name] for f in fields(Config) if f.name in raw})
    cfg.discord_token=''
    for key in ('whisper_model','wake_model','tts_model','tts_supertonic_model'):
        setattr(cfg,key,str(root/getattr(cfg,key)))
    cfg.validate();local={name.casefold() for name in installed_models(cfg.ollama_host)}
    if not candidates or len(candidates)>12 or any(c['model'].casefold() not in local for c in candidates):
        raise ValueError('Выберите до 12 вариантов установленных локальных моделей.')
    logger=logging.getLogger('calibration');stt=AudioService(logger,cfg);tts=TTSService(logger,cfg)
    rows=[];speech=[]
    print('Подготовка распознавания и голоса; бот должен быть остановлен.',flush=True)
    try:
        stt.load();tts.load()
        for name,prompt,expected in CASES:
            pcm=await tts.generate_audio(prompt)
            mono=np.frombuffer(pcm,dtype='<i2').reshape(-1,2).mean(axis=1)
            audio=resample_poly(mono,1,3).astype('<i2').tobytes()
            start=time.perf_counter();recognized=await stt.transcribe(audio,active=True)
            speech.append((name,prompt,recognized,expected,(time.perf_counter()-start)*1000))
        for index,candidate in enumerate(candidates):
            print(f"{index+1}/{len(candidates)}: {candidate['model']} · ресурсы {candidate['resource_level']}",flush=True)
            options=resource_settings(candidate['resource_level'])
            for key,value in options.items():setattr(cfg,key,value)
            cfg.ollama_model=candidate['model'];cfg.ollama_reasoning=False
            service=OllamaService(logger,cfg);cases=[]
            try:
                # Separate model loading from warm pipeline response measurements.
                await service.warmup()
                for repeat in range(2):
                    for name,prompt,recognized,expected,stt_ms in speech:
                        if not recognized:raise RuntimeError('Синтетическая речь не распознана: '+name)
                        start=time.perf_counter();first=None;answer=''
                        async for part in service.stream_sentences([
                            {'role':'system','content':'Отвечай кратко и точно на русском. Выполняй формат, указанный в вопросе.'},
                            {'role':'user','content':recognized}]):
                            answer+=(' ' if answer else '')+part
                            if first is None:
                                pcm=await tts.generate_audio(part)
                                if not pcm:raise RuntimeError('Синтезатор не вернул звук')
                                first=(time.perf_counter()-start)*1000+stt_ms+cfg.speech_end_delay*1000
                        cases.append({'case':name,'repeat':repeat+1,'recognized':recognized,'expected':expected,
                                      'answer':answer,'pass':passes(answer,expected),'first_pcm_ms':first})
                row={**candidate,'passed':sum(c['pass'] for c in cases),'total':len(cases),
                     'first_pcm_ms':round(statistics.median(c['first_pcm_ms'] for c in cases),1),'cases':cases}
            except Exception as error:row={**candidate,'error':type(error).__name__,'passed':0,'total':len(cases)}
            finally:
                try:await service.client.post('/api/generate',json={'model':candidate['model'],'keep_alive':0})
                except Exception:pass
                await service.close()
            rows.append(row)
            atomic_json(output,{'complete':False,'rows':rows})
        report={'complete':True,'created':time.time(),'rows':rows,'recommendation':choose_recommendation(rows),
                'conditions':'4 синтетические озвученные фразы; распознавание замерено один раз, ответы дважды. Whisper остаётся в памяти. Загрузка модели исключена; задержка endpoint включена. Без личной персоны/истории и без сетевой передачи Discord. Это не гарантия задержки живого разговора.'}
        atomic_json(output,report)
    finally:stt.close();tts.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--candidates',required=True)
    args=parser.parse_args();prepare_runtime()
    asyncio.run(measure(args.root,json.loads(args.candidates),args.output))
