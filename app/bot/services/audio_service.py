import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import logging
import re
import time
import numpy as np

from ..conversation import addressed,wake_aliases


class AudioService:
    """Small Vosk CPU gate; CUDA Whisper only for addressed/active utterances."""
    def __init__(self, logger: logging.Logger, config):
        self.logger, self.config = logger, config
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='serega-stt')
        self.control_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='serega-stop')
        self.lock = asyncio.Lock()
        self.device = 'unloaded'
        self.model = self.wake_model = None
        self.last_ms = {}
        self.wake_end = 0.0
        self.wake_start = 0.0
        self.grammar = ''
        self.control_grammar = ''
        self._control_gate = self._control_verifier = None
        self.decode_future = None
        self.wake_gate_available=True

    def load(self):
        from vosk import Model, KaldiRecognizer, SetLogLevel
        import ctranslate2
        from faster_whisper import WhisperModel
        SetLogLevel(-1)
        self.wake_model = Model(str(self.config.path(self.config.wake_model)))
        name_words=self.config.wake_word.casefold().replace('ё','е').split()
        self.wake_gate_available=len(name_words)==1 and all(self.wake_model.vosk_model_find_word(word)>=0 for word in name_words)
        aliases=[word for name in wake_aliases(self.config.wake_word) for word in name.split()]
        words = [*aliases, 'эй', 'слушай', 'алло', 'привет', 'ну']
        words = [word for word in words if self.wake_model.vosk_model_find_word(word) >= 0]
        self.grammar = json.dumps([*words, '[unk]'], ensure_ascii=False)
        control_words = [*aliases, 'эй', 'заткнись', 'замолчи', 'помолчи', 'молчи',
                         'завали', 'ебало', 'закрой', 'рот', 'пожалуйста', 'нахуй',
                         'не', 'он', 'мне', 'сказал', 'значит', 'блять', 'блядь', 'уже', 'ну',
                         'можешь', 'помолчать', 'замолчать', 'заткнуться']
        control_words = [word for word in control_words if self.wake_model.vosk_model_find_word(word) >= 0]
        self.control_grammar = json.dumps([*control_words, '[unk]'], ensure_ascii=False)
        self._control_gate = KaldiRecognizer(self.wake_model, 16000, self.control_grammar)
        self._control_verifier = KaldiRecognizer(self.wake_model, 16000)
        self._control_verifier.SetWords(True)
        requested = self.config.whisper_device
        self.device = ('cuda' if ctranslate2.get_cuda_device_count() else 'cpu') if requested == 'auto' else requested
        compute = self.config.whisper_compute_type if self.device == 'cuda' else 'int8'
        path = self.config.path(self.config.whisper_model)
        if not (path / 'model.bin').exists():
            raise RuntimeError('Нет локальной STT-модели. Выполните setup.bat.')
        try:
            self.model = WhisperModel(str(path), device=self.device, compute_type=compute,
                                      cpu_threads=self.config.whisper_cpu_threads, num_workers=1,
                                      local_files_only=True)
            # A real inference probes CUDA/cuBLAS/cuDNN, not just device enumeration.
            list(self.model.transcribe(np.zeros(16000, dtype=np.float32), language='ru', beam_size=1,
                                       max_new_tokens=32)[0])
        except Exception:
            if requested != 'auto' or self.device != 'cuda':
                raise
            self.logger.warning('CUDA STT не прошла пробный inference; переход на CPU int8', exc_info=True)
            self.device, compute = 'cpu', 'int8'
            self.model = WhisperModel(str(path), device='cpu', compute_type=compute,
                                      cpu_threads=self.config.whisper_cpu_threads, local_files_only=True)
        self.logger.info('STT %s: %s/%s; Vosk CPU', path.name, self.device, compute)

    def wake_transcript(self, pcm):
        from vosk import KaldiRecognizer
        recognizer = KaldiRecognizer(self.wake_model, 16000, self.grammar)
        recognizer.SetWords(True)
        parts, words = [], []
        # Consume all endpoint results rather than losing a long phrase's prefix.
        for offset in range(0, len(pcm), 8000):
            if recognizer.AcceptWaveform(pcm[offset:offset + 8000]):
                result = json.loads(recognizer.Result())
                parts.append(result.get('text', ''))
                words.extend(result.get('result', []))
        result = json.loads(recognizer.FinalResult())
        parts.append(result.get('text', ''))
        words.extend(result.get('result', []))
        self.wake_end = 0.0
        for word in words:
            if addressed(word['word'], self.config.wake_word)[0] and word['conf'] >= .8:
                self.wake_start = word['start']
                self.wake_end = word['end']
                return ' '.join(parts).strip()
        return ''

    def full_transcript(self, pcm):
        from faster_whisper.vad import get_speech_timestamps, VadOptions
        audio = np.frombuffer(pcm, dtype='<i2').astype(np.float32) / 32768.0
        started = time.perf_counter()
        spans = get_speech_timestamps(audio, VadOptions(threshold=.6,
            min_speech_duration_ms=128, min_silence_duration_ms=100, speech_pad_ms=100))
        self.last_ms['speech_gate_ms'] = (time.perf_counter() - started) * 1000
        if not spans:
            self.last_ms['noise_rejected'] = 1
            return ''
        audio = np.concatenate([audio[span['start']:span['end']] for span in spans])
        audio = np.pad(audio, (1600, 2400))
        segments, _ = self.model.transcribe(
            audio, language='ru', task='transcribe', beam_size=self.config.whisper_beam_size,
            best_of=1, temperature=0, condition_on_previous_text=False, vad_filter=False,
            word_timestamps=False, no_speech_threshold=0.5, compression_ratio_threshold=2.6,
            max_new_tokens=min(224, max(48, int(len(audio) / 16000 * 12))), no_repeat_ngram_size=3)
        segments = list(segments)
        if segments:
            self.last_ms['stt_min_logprob'] = min(segment.avg_logprob for segment in segments)
            self.last_ms['stt_max_no_speech'] = max(segment.no_speech_prob for segment in segments)
        text = ' '.join(segment.text.strip() for segment in segments
                        if segment.avg_logprob > -.75 and segment.no_speech_prob < .4
                        and getattr(segment, 'compression_ratio', 0) < 2.4).strip()
        normalized = re.sub(r'[^\w\s]', '', text.lower().replace('ё', 'е')).strip()
        if (normalized in ('русская речь', 'спасибо за просмотр', 'продолжение следует')
            or re.match(r'^субтитры (?:создавал|сделал|подогнал|редактор)', normalized)):
            self.last_ms['hallucination_rejected'] = 1
            return ''
        return text

    async def transcribe(self, pcm, active=False):
        async with self.lock:
            loop = asyncio.get_running_loop()
            self.last_ms = {}
            if self.decode_future and not self.decode_future.done():
                self.last_ms['stt_busy_rejected'] = 1
                return ''
            wake_end = 0.0
            gate_used=not active and self.wake_gate_available and len(self.config.wake_word.split())==1
            if gate_used:
                start = time.perf_counter()
                gate_text = await loop.run_in_executor(self.executor, self.wake_transcript, pcm)
                self.last_ms['wake_ms'] = (time.perf_counter() - start) * 1000
                if not addressed(gate_text, self.config.wake_word)[0]:
                    return ''
                wake_end = self.wake_end
            start = time.perf_counter()
            text = await self.decode(pcm)
            # Constrained Vosk can confuse "телега" with "Серёга". Whisper must
            # confirm the vocative. For long questions Whisper may omit the name;
            # verify the audio around its Vosk timestamp and retain the question.
            if not active and text and not addressed(text, self.config.wake_word)[0]:
                if not gate_used:return ''
                prefix = pcm[int(max(0, self.wake_start - .18) * 32000):int((wake_end + .18) * 32000)]
                confirmed = await self.decode(prefix)
                if not addressed(confirmed, self.config.wake_word)[0]:
                    return ''
                misplaced = re.search(r'\bсер[её](?:га|жа|г|ж)\b\s*[,!?:.\-—]*\s*', text, re.I)
                if misplaced and misplaced.start() < 16:
                    text = text[misplaced.end():]
                text = self.config.wake_word.capitalize() + ', ' + text
            self.last_ms['stt_ms'] = (time.perf_counter() - start) * 1000
            return text

    async def decode(self, pcm):
        # Cancellation cannot stop native CTranslate2 work. Shield and remember
        # it, so later turns cannot accumulate behind a timed-out GPU call.
        self.decode_future = asyncio.get_running_loop().run_in_executor(self.executor, self.full_transcript, pcm)
        self.decode_future.add_done_callback(lambda future: future.exception() if not future.cancelled() else None)
        try:
            return await asyncio.wait_for(asyncio.shield(self.decode_future), self.config.whisper_timeout)
        except TimeoutError:
            self.last_ms['stt_timeout'] = 1
            self.logger.warning('Распознавание превысило %.1f с; старый результат будет отброшен', self.config.whisper_timeout)
            return ''

    def control_transcript(self, pcm):
        from vosk import KaldiRecognizer
        from ..controls import voice_control
        if not self.wake_model:
            return None
        if self._control_gate is None:
            self._control_gate = KaldiRecognizer(self.wake_model, 16000, self.control_grammar)
        else:
            self._control_gate.Reset()
        self._control_gate.AcceptWaveform(pcm)
        candidate = json.loads(self._control_gate.FinalResult()).get('text', '')
        # The grammar may mark a name or filler unknown. This is only a cheap
        # candidate gate; the complete unrestricted transcript below remains
        # authoritative for quotes, negation and the command itself.
        if voice_control(candidate.replace('[unk]', ' '), self.config.wake_word) != 'mute':
            return None
        # Full-vocabulary confirmation preserves negations and quotes. The
        # cheap gate alone can project unrelated speech onto stop words.
        if self._control_verifier is None:
            self._control_verifier = KaldiRecognizer(self.wake_model, 16000)
            self._control_verifier.SetWords(True)
        else:
            self._control_verifier.Reset()
        self._control_verifier.AcceptWaveform(pcm)
        result = json.loads(self._control_verifier.FinalResult())
        text = result.get('text', '')
        words = result.get('result', [])
        command_words = [word for word in words if word['word'] not in
                         ('серёга', 'серёжа', 'тюряга', 'терега', 'тиряга', 'тирега', 'эй', 'пожалуйста',
                          'блять', 'блядь', 'уже', 'ну', 'нахуй', 'можешь', self.config.wake_word.lower())]
        if not command_words or '[unk]' in text or min(word['conf'] for word in command_words) < .8:
            return None
        return 'mute' if voice_control(text, self.config.wake_word) == 'mute' else None

    async def recognize_control(self, pcm):
        return await asyncio.get_running_loop().run_in_executor(self.control_executor, self.control_transcript, pcm)

    def close(self):
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.control_executor.shutdown(wait=False, cancel_futures=True)
