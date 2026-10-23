"""Thread-safe bounded Discord ingress; each speaker has an independent VAD."""
import asyncio
import audioop
from collections import deque
from dataclasses import dataclass, field
import queue
import time
import webrtcvad
from discord.ext import voice_recv


@dataclass
class Utterance:
    member: object
    pcm: bytes  # mono PCM16, 16 kHz
    started: float
    ended: float
    queued: float
    vad_ms: float


@dataclass
class Speaker:
    vad: object
    member: object
    preroll: deque = field(default_factory=lambda: deque(maxlen=8))
    votes: deque = field(default_factory=lambda: deque(maxlen=3))
    frames: list = field(default_factory=list)
    resampler: object = None
    last_packet: float = 0
    last_voice: float = 0
    started: float = 0
    voiced: int = 0
    vad_ms: float = 0
    timestamp: int | None = None
    last_control_at: float = 0


class TranscriptionSink(voice_recv.AudioSink):
    def __init__(self, config, output, logger, target_user_id=None, control_callback=None):
        super().__init__()
        self.config, self.output, self.logger = config, output, logger
        self.target_user_id = target_user_id
        self.control_callback = control_callback
        self.ingress = queue.Queue(maxsize=600)
        self.speakers = {}
        self.closed = False
        self.dropped_frames = self.dropped_utterances = self.received_frames = 0
        self.completed_utterances = 0
        self.last_frame_at = 0

    def wants_opus(self):
        return False

    def write(self, user, data):
        if self.closed or user is None or getattr(user, 'bot', False):
            return
        if self.target_user_id and user.id != self.target_user_id:
            return
        pcm = getattr(data, 'pcm', b'')
        if len(pcm) != 3840:
            return
        try:
            self.ingress.put_nowait((user, pcm, time.monotonic(), getattr(data.packet, 'timestamp', None)))
            self.received_frames += 1
            self.last_frame_at = time.monotonic()
        except queue.Full:
            self.dropped_frames += 1

    def feed(self, user, pcm, now, timestamp=None):
        """Called only on the event loop, never from the receive thread."""
        state = self.speakers.get(user.id)
        if state is None:
            if len(self.speakers) >= self.config.max_speakers:
                self.dropped_frames += 1
                return
            state = self.speakers[user.id] = Speaker(webrtcvad.Vad(self.config.vad_mode), user)
        if state.last_packet and now - state.last_packet >= self.config.speech_end_delay:
            self.finish(state, now)
            state.preroll.clear()
            state.votes.clear()
        # Fill short RTP gaps only; never allocate based on unchecked timestamps.
        if timestamp is not None and state.timestamp is not None and state.frames:
            gap = (timestamp - state.timestamp) % 2**32
            missing = min(10, max(0, gap // 960 - 1)) if gap < 24000 else 0
            state.frames.extend([bytes(640)] * missing)
        state.timestamp, state.last_packet = timestamp, now
        left = audioop.tomono(pcm, 2, 1, 0)
        right = audioop.tomono(pcm, 2, 0, 1)
        mono = left if audioop.rms(left, 2) >= audioop.rms(right, 2) else right
        mono, state.resampler = audioop.ratecv(mono, 2, 1, 48000, 16000, state.resampler)
        mono = mono[:640].ljust(640, b'\x00')
        start = time.perf_counter()
        voice = audioop.rms(mono, 2) >= self.config.min_audio_rms and state.vad.is_speech(mono, 16000)
        state.vad_ms += (time.perf_counter() - start) * 1000
        state.votes.append(voice)
        if not state.frames:
            state.preroll.append(mono)
            if sum(state.votes) < 2:
                return
            state.frames = list(state.preroll)
            state.preroll.clear()
            state.started = now - len(state.frames) * .02
            state.voiced = sum(state.votes)
        else:
            state.frames.append(mono)
            state.voiced += int(voice)
        if voice:
            state.last_voice = now
        if (self.control_callback and state.voiced >= 12
            and now - state.last_control_at >= .25):
            # Keep the complete bounded utterance, including quote/negation
            # context. An open microphone must not disable stop after 3 s.
            state.last_control_at = now
            self.control_callback(state.member, b''.join(state.frames), now)
        if now - state.last_voice >= self.config.speech_end_delay or len(state.frames) * .02 >= self.config.max_utterance_seconds:
            self.finish(state, now)

    def finish(self, state, now):
        if state.frames and state.voiced * .02 >= self.config.min_speech_duration:
            # Keep 160 ms of trailing silence, remove the rest of the endpoint wait.
            trim = max(0, int((now - state.last_voice - .16) / .02))
            frames = state.frames[:-trim] if trim and trim < len(state.frames) else state.frames
            utterance = Utterance(state.member, b''.join(frames), state.started,
                                  state.last_voice, now, state.vad_ms)
            try:
                self.output.put_nowait(utterance)
                self.completed_utterances += 1
            except asyncio.QueueFull:
                self.dropped_utterances += 1
                self.logger.warning('Очередь STT заполнена, фраза пропущена')
        state.frames = []
        state.voiced = 0
        state.vad_ms = 0
        state.timestamp = None
        state.votes.clear()

    async def pump(self):
        while not self.closed:
            for _ in range(100):
                try:
                    item = self.ingress.get_nowait()
                except queue.Empty:
                    break
                self.feed(*item)
            now = time.monotonic()
            for uid, state in list(self.speakers.items()):
                if state.frames and now - state.last_voice >= self.config.speech_end_delay:
                    self.finish(state, now)
                if now - state.last_packet > 30:
                    self.speakers.pop(uid, None)
            await asyncio.sleep(.02)

    def cleanup(self):
        # May be called by AudioReader's stopper thread or __del__.
        self.closed = True

    def clear_buffers(self):
        self.speakers.clear()
        while True:
            try:
                self.ingress.get_nowait()
            except queue.Empty:
                break
