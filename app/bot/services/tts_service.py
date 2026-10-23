import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import math
import re
import numpy as np


class TTSService:
    """Local CPU voices with persistent ONNX sessions and Discord-ready PCM."""
    def __init__(self, logger, config):
        self.logger, self.config = logger, config
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='serega-tts')
        self.voice = None

    def load(self):
        if self.config.tts_engine == 'supertonic':
            from .supertonic_voice import SupertonicVoice
            self.voice = SupertonicVoice.load(self.config.path(self.config.tts_supertonic_model),
                                               self.config.tts_supertonic_voice)
            self.synthesize('Готов.')
            self.logger.info('TTS Supertonic %s: CPU, speed %.2f, steps %d',
                             self.config.tts_supertonic_voice, self.config.tts_speed,
                             self.config.tts_supertonic_steps)
            return
        from piper import PiperVoice
        from piper.config import PiperConfig
        import onnxruntime as ort
        model = self.config.path(self.config.tts_model)
        if not model.exists():
            raise RuntimeError('Нет локального голоса Piper. Выполните setup.bat.')
        settings = ort.SessionOptions()
        settings.intra_op_num_threads = 4
        settings.inter_op_num_threads = 1
        self.voice = PiperVoice(
            session=ort.InferenceSession(str(model), sess_options=settings, providers=['CPUExecutionProvider']),
            config=PiperConfig.from_dict(json.loads(model.with_suffix('.onnx.json').read_text(encoding='utf-8'))))
        self.synthesize('Готов.')
        self.logger.info('TTS Piper Denis: CPU, speed %.2f', self.config.tts_speed)

    def synthesize(self, text):
        from scipy.signal import resample_poly
        text = prepare_speech(text)
        if self.config.tts_engine == 'supertonic':
            audio, rate = self.voice.synthesize(text, speed=self.config.tts_speed,
                                               steps=self.config.tts_supertonic_steps)
        else:
            from piper import SynthesisConfig
            chunks = list(self.voice.synthesize(text, SynthesisConfig(
                length_scale=1 / self.config.tts_speed, noise_scale=self.config.tts_noise_scale,
                noise_w_scale=self.config.tts_noise_w_scale)))
            if not chunks:
                raise RuntimeError('Piper вернул пустое аудио')
            rate = self.voice.config.sample_rate
            pause = np.zeros(round(rate * .10), dtype=np.float32)
            audio = np.concatenate([part for chunk in chunks for part in (chunk.audio_float_array, pause)])
        if audio.size == 0 or not np.isfinite(audio).all():
            raise RuntimeError('Синтезатор вернул пустое или некорректное аудио')
        factor = math.gcd(rate, 48000)
        audio = resample_poly(audio, 48000 // factor, rate // factor)
        audio = np.clip(audio * self.config.tts_volume * 32767, -32768, 32767).astype('<i2')
        return np.repeat(audio[:, None], 2, axis=1).tobytes()

    async def generate_audio(self, text):
        return await asyncio.get_running_loop().run_in_executor(self.executor, self.synthesize, text)

    def close(self):
        self.executor.shutdown(wait=False, cancel_futures=True)


def prepare_speech(text):
    """Pronunciation cleanup only; no profanity/content censorship."""
    from num2words import num2words
    text = re.sub(r'\[([^\]]+)\]\(https?://[^)]+\)', r'\1', text)
    text = re.sub(r'https?://\S+', 'ссылка', text)
    text = text.replace('_', ' ')
    text = re.sub(r'[`*#]', '', text)
    # Expand technical abbreviations before handing them to Russian phonemes.
    # Word boundaries keep names and larger words unchanged.
    abbreviations = {
        'FPS': 'эф пи эс', 'GPU': 'джи пи ю', 'CPU': 'си пи ю',
        'SSD': 'эс эс ди', 'HDD': 'эйч ди ди', 'RAM': 'рэм',
        'MMR': 'эм эм эр', 'ММР': 'эм эм эр', 'ОЗУ': 'о зэ у',
        'USB': 'ю эс би', 'Wi-Fi': 'вай фай', 'Dota': 'Дота',
    }
    for abbreviation, spoken in abbreviations.items():
        text = re.sub(r'\b' + re.escape(abbreviation) + r'\b', spoken, text, flags=re.I)
    text = re.sub(r'\bPython\b', 'Пайтон', text, flags=re.I)
    text = re.sub(r'\bBMW\b', 'бэ эм вэ', text, flags=re.I)
    text = re.sub(r'\bDiscord\b', 'Дискорд', text, flags=re.I)
    text = re.sub(r'\bИИ\b', 'искусственный интеллект', text)
    def quantity(match):
        value = int(match['value'])
        unit = match['unit'].lower()
        forms = {'%': ('процент', 'процента', 'процентов'),
                 'гб': ('гигабайт', 'гигабайта', 'гигабайт'),
                 'gb': ('гигабайт', 'гигабайта', 'гигабайт'),
                 'мб': ('мегабайт', 'мегабайта', 'мегабайт'),
                 'mb': ('мегабайт', 'мегабайта', 'мегабайт')}
        index = 2 if 11 <= value % 100 <= 14 else (0 if value % 10 == 1 else (1 if value % 10 in (2, 3, 4) else 2))
        return num2words(value, lang='ru') + ' ' + forms[unit][index]
    text = re.sub(r'(?<![\w.,])(?P<value>\d{1,6})\s*(?P<unit>%|[гм]б\b|[gm]b\b)', quantity, text, flags=re.I)
    # Parentheses should be a spoken pause rather than an abrupt aside.
    text = re.sub(r'\s*\(([^()]*)\)', r', \1, ', text)
    text = re.sub(r'(?<![\w.,])\d{1,6}(?!\w|[.,]\d)', lambda m: num2words(int(m[0]), lang='ru'), text)
    text = text.replace('—', ', ').replace('…', '.')
    text = re.sub(r'([!?.,])\1+', r'\1', text)
    text = re.sub(r',\s*([.!?])', r'\1', text)
    return re.sub(r'\s+', ' ', text).strip()
