from dataclasses import dataclass, fields, field
import json
import os
from pathlib import Path
from urllib.parse import urlsplit
import ipaddress

ROOT = Path(__file__).resolve().parents[1]

RESPONSE_PROFILES = {
    'fast': {'ollama_reasoning': True, 'ollama_reasoning_budget': 256, 'ollama_num_gpu': -1, 'ollama_num_ctx': 4096,
             'whisper_beam_size': 1, 'speech_end_delay': .30, 'tts_supertonic_steps': 6},
    'balanced': {'ollama_reasoning': True, 'ollama_reasoning_budget': 256, 'ollama_num_gpu': -1, 'ollama_num_ctx': 4096,
                 'whisper_beam_size': 2, 'speech_end_delay': .38, 'tts_supertonic_steps': 8},
    'thoughtful': {'ollama_reasoning': True, 'ollama_reasoning_budget': 384,
                   'ollama_num_gpu': -1, 'ollama_num_ctx': 6144,
                   'whisper_beam_size': 3, 'speech_end_delay': .42, 'tts_supertonic_steps': 8},
}


@dataclass
class Config:
    discord_token: str = field(default='', repr=False)
    bot_name: str = 'Собеседник'
    ollama_host: str = 'http://127.0.0.1:11434'
    ollama_model: str = 'huihui_ai/qwen3-abliterated:8b'
    ollama_num_ctx: int = 4096
    ollama_num_gpu: int = -1
    ollama_num_predict: int = 260
    ollama_temperature: float = 0.4
    ollama_top_p: float = 0.9
    ollama_keep_alive: str = '10m'
    ollama_timeout: float = 40.0
    ollama_reasoning: bool = False
    ollama_reasoning_budget: int = 256
    response_profile: str = 'custom'
    dota_patch_source_enabled: bool = False
    memory_enabled: bool = True
    learning_capture_enabled: bool = False
    learning_auto_enabled: bool = False
    max_response_chars: int = 1000
    history_turns: int = 8
    history_chars: int = 5000
    history_ttl: float = 900.0
    wake_word: str = 'собеседник'
    wake_timeout: float = 20.0
    followup_timeout: float = 35.0
    wake_model: str = 'models/vosk-model-small-ru-0.22'
    whisper_model: str = 'models/faster-whisper-large-v3-turbo'
    whisper_device: str = 'auto'
    whisper_compute_type: str = 'int8_float16'
    whisper_cpu_threads: int = 4
    whisper_timeout: float = 12.0
    whisper_beam_size: int = 3
    tts_model: str = 'models/ru_RU-denis-medium.onnx'
    tts_engine: str = 'piper'
    tts_supertonic_model: str = 'models/supertonic-3'
    tts_supertonic_voice: str = 'M3'
    tts_supertonic_steps: int = 8
    tts_speed: float = 1.0
    tts_noise_scale: float = 0.45
    tts_noise_w_scale: float = 0.6
    tts_volume: float = 0.85
    vad_mode: int = 2
    min_audio_rms: int = 60
    min_speech_duration: float = 0.12
    speech_end_delay: float = 0.42
    max_utterance_seconds: float = 15.0
    max_audio_queue_size: int = 6
    max_audio_age: float = 60.0
    max_speakers: int = 32
    listen_only_joiner: bool = False
    echo_guard_seconds: float = 0.25
    sync_guild_commands: bool = True
    auto_join_channel_id: int = 0
    enable_prefix_commands: bool = False
    conversation_mode: str = 'participant'
    ambient_cooldown: float = 12.0
    ambient_pause: float = 1.0
    rare_cooldown: float = 180.0
    initiative_enabled: bool = True
    initiative_delay: float = 30.0
    initiative_cooldown: float = 120.0
    initiative_session_ttl: float = 300.0

    def path(self, value: str) -> Path:
        path = Path(value)
        return path if path.is_absolute() else ROOT / path

    def validate(self):
        for item in fields(self):
            if item.type is str and not isinstance(getattr(self, item.name), str):
                raise ValueError(f'{item.name} должен быть строкой')
        if self.learning_auto_enabled and not self.learning_capture_enabled:
            raise ValueError('Автообучение требует learning_capture_enabled.')
        if self.response_profile not in ('custom', *RESPONSE_PROFILES):
            raise ValueError('Неизвестный режим скорости ответа')
        host = urlsplit(self.ollama_host)
        try:
            loopback = host.hostname == 'localhost' or ipaddress.ip_address(host.hostname or '').is_loopback
        except ValueError:
            loopback = False
        if host.scheme != 'http' or not loopback or host.username or host.password:
            raise ValueError('ollama_host должен указывать на локальный http://127.0.0.1:11434')
        ranges = {
            'wake_timeout': (1, 600), 'speech_end_delay': (0.15, 3),
            'followup_timeout': (5, 180),
            'max_utterance_seconds': (1, 60), 'max_audio_queue_size': (1, 32),
            'tts_speed': (0.5, 2), 'tts_volume': (0.1, 2), 'vad_mode': (0, 3),
            'tts_noise_scale': (0, 1), 'tts_noise_w_scale': (0, 1),
            'tts_supertonic_steps': (4, 16),
            'history_turns': (1, 30), 'history_chars': (500, 12000),
            'ollama_num_ctx': (512, 8192), 'ollama_num_predict': (16, 512),
            'ollama_num_gpu': (-1, 128), 'whisper_timeout': (2, 60),
            'ollama_timeout': (5, 180), 'whisper_cpu_threads': (1, 16),
            'ollama_reasoning_budget': (128, 1024),
            'whisper_beam_size': (1, 5), 'max_response_chars': (40, 2000),
            'max_audio_age': (3, 120), 'max_speakers': (1, 100),
            'min_speech_duration': (0.06, 2), 'min_audio_rms': (0, 5000),
            'echo_guard_seconds': (0, 2), 'history_ttl': (30, 86400),
            'ambient_cooldown': (5, 120), 'ambient_pause': (.4, 4),
            'rare_cooldown': (120, 900),
            'initiative_delay': (15, 180), 'initiative_cooldown': (30, 900),
            'initiative_session_ttl': (60, 1800),
        }
        for name, (low, high) in ranges.items():
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
                raise ValueError(f'Некорректная настройка {name}: допустимо {low}..{high}')
        for name in ('vad_mode', 'max_audio_queue_size', 'history_turns', 'ollama_num_ctx',
                     'ollama_num_predict', 'whisper_cpu_threads', 'whisper_beam_size',
                     'max_response_chars', 'max_speakers', 'history_chars', 'ollama_reasoning_budget', 'ollama_num_gpu', 'tts_supertonic_steps'):
            if type(getattr(self, name)) is not int:
                raise ValueError(f'{name} должен быть целым числом')
        if self.whisper_device not in ('auto', 'cpu', 'cuda'):
            raise ValueError('whisper_device: auto, cpu или cuda')
        if self.tts_engine not in ('piper', 'supertonic'):
            raise ValueError('tts_engine: piper или supertonic')
        if self.tts_supertonic_voice not in ('M1', 'M2', 'M3', 'M4', 'M5'):
            raise ValueError('tts_supertonic_voice: M1..M5')
        if not self.ollama_model or self.ollama_model.endswith('-cloud') or ':cloud' in self.ollama_model:
            raise ValueError('Разрешены только локально установленные модели Ollama')
        for name in ('listen_only_joiner', 'sync_guild_commands', 'enable_prefix_commands', 'initiative_enabled', 'ollama_reasoning', 'dota_patch_source_enabled', 'memory_enabled', 'learning_capture_enabled', 'learning_auto_enabled'):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f'{name} должен быть true или false')
        if self.conversation_mode not in ('participant', 'summon', 'strict', 'rare'):
            raise ValueError('conversation_mode: participant, summon, strict или rare')

    @classmethod
    def load(cls, path=ROOT / 'config.json'):
        path = Path(path)
        env_file = path.with_name('.env')
        if env_file.exists():
            for line in env_file.read_text(encoding='utf-8-sig').splitlines():
                if '=' in line and not line.lstrip().startswith('#'):
                    key, value = line.split('=', 1)
                    os.environ.setdefault(key.strip(), value.strip().strip('"\''))
        data = json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else {}
        if not isinstance(data, dict):
            raise ValueError('config.json должен содержать JSON-объект')
        # Old credentials/options stay on disk. No cloud options are used.
        known = {field.name for field in fields(cls)}
        config = cls(**{key: value for key, value in data.items() if key in known})
        token = os.environ.get('DISCORD_TOKEN', config.discord_token)
        if not isinstance(token, str):
            raise ValueError('discord_token должен быть строкой')
        config.discord_token = token.strip()
        config.validate()
        return config
