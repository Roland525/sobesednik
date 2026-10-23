"""Original local promo audio. No private conversation or cloud synthesis."""
import asyncio
import json
import logging
from pathlib import Path
import sys
import wave
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bot.config import Config, ROOT
from bot.services.tts_service import TTSService


SCENES = [
    ('hook', 5., 'В вашем войсе не хватает ещё одного собеседника?'),
    ('intro', 5., 'Я на связи. О чём поговорим?'),
    ('memory', 7., 'Запомнил. Твоя любимая игра — Дота два.'),
    ('local', 7., 'Распознавание, ответы и голос работают на твоём компьютере. Без платных API.'),
    ('stop', 3., 'Собеседник, заткнись.'),
    ('cta', 6., 'Забирай проект на Гитхабе. Инструкция внутри.'),
]


def save(path, samples):
    with wave.open(str(path), 'wb') as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(48000)
        output.writeframes((np.clip(samples, -.98, .98)*32767).astype('<i2').tobytes())


async def main():
    folder = ROOT / 'artifacts/tiktok'
    folder.mkdir(parents=True, exist_ok=True)
    config = Config(tts_engine='supertonic', tts_supertonic_voice='M3',
                    tts_supertonic_steps=8, tts_speed=1.06)
    tts = TTSService(logging.getLogger(), config)
    timeline, clips, cursor = [], [], 0.
    try:
        await asyncio.to_thread(tts.load)
        for name, minimum, text in SCENES:
            pcm = await tts.generate_audio(text)
            samples = np.frombuffer(pcm, dtype='<i2').astype(np.float32).reshape(-1, 2)/32768
            seconds = len(samples)/48000
            duration = max(minimum, seconds+.8)
            save(folder / (name + '.wav'), samples)
            timeline.append({'name': name, 'start': cursor, 'end': cursor+duration,
                             'speech_start': cursor+.3, 'speech_end': cursor+.3+seconds, 'text': text})
            clips.append((cursor+.3, samples))
            cursor += duration
            print(name, round(seconds, 2), flush=True)
    finally:
        tts.close()
    rate = 48000
    times = np.arange(round(cursor*rate), dtype=np.float32)/rate
    # Original soft pulse, no samples from commercial music.
    beat = np.mod(times, .6)
    music = .035*np.sin(2*np.pi*55*times)*np.exp(-beat*18)
    music += .008*np.sin(2*np.pi*220*times)*np.exp(-beat*9)
    envelope = np.ones_like(music)
    for start, samples in clips:
        left, right = round(start*rate), min(len(music), round(start*rate)+len(samples))
        envelope[max(0,left-4800):min(len(music),right+4800)] = .2
    track = np.repeat((music*envelope)[:, None], 2, axis=1)
    for start, samples in clips:
        left = round(start*rate)
        track[left:left+len(samples)] += samples*.92
    save(folder / 'soundtrack.wav', track)
    (folder / 'timeline.json').write_text(json.dumps({'duration': cursor, 'scenes': timeline,
        'audio': 'Local Supertonic M3,8steps,speed1.06; original synthetic pulse; staged script'},
        ensure_ascii=False, indent=2), encoding='utf-8')
    print('Local promo soundtrack saved.', flush=True)


if __name__ == '__main__':
    asyncio.run(main())
