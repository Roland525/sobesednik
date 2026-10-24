"""Local-only Supertonic adapter. Downloads belong exclusively to setup tools."""
import numpy as np


class SupertonicVoice:
    def __init__(self, engine, style):
        self.engine, self.style = engine, style

    @staticmethod
    def required_files(model, speaker):
        return [model / 'onnx' / name for name in (
            'tts.json', 'unicode_indexer.json', 'duration_predictor.onnx',
            'text_encoder.onnx', 'vector_estimator.onnx', 'vocoder.onnx',
        )] + [model / 'voice_styles' / f'{speaker}.json']

    @classmethod
    def load(cls, model, speaker, threads=4):
        if any(not path.is_file() for path in cls.required_files(model, speaker)):
            raise RuntimeError('Нет локальных файлов Supertonic. Выполните tools/download_models.py --tts-engine supertonic.')
        import onnxruntime as ort
        from bot.vendor.supertonic_helper import (
            TextToSpeech, load_cfgs, load_text_processor, load_onnx_all, load_voice_style,
        )
        options = ort.SessionOptions()
        options.intra_op_num_threads = threads
        options.inter_op_num_threads = 1
        directory = str(model / 'onnx')
        engine = TextToSpeech(load_cfgs(directory), load_text_processor(directory),
                              *load_onnx_all(directory, options, ['CPUExecutionProvider']))
        style = load_voice_style([str(model / 'voice_styles' / f'{speaker}.json')])
        return cls(engine, style)

    def synthesize(self, text, *, speed, steps):
        audio, duration = self.engine(text, 'ru', self.style, total_step=steps, speed=speed)
        rate = self.engine.sample_rate
        if not np.isfinite(duration[0]) or duration[0] <= 0:
            raise RuntimeError('Supertonic вернул некорректную длительность')
        return audio[0, :round(rate * float(duration[0]))], rate
