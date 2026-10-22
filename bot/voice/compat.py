"""Narrow, version-checked adapter for voice-recv 0.5.3a180 + discord.py 2.7.1.

voice-recv decrypts the transport but omits DAVE before Opus decoding. Never
pass failed E2EE decryptions to the decoder as if they were plaintext.
"""
import importlib.metadata
from collections import deque
import logging
import time

_installed = False
stats = {'dave_decrypted': 0, 'dave_dropped': 0, 'opus_bad': 0, 'non_audio_dropped': 0,
         'jitter_preserved': 0}


def install():
    global _installed
    if _installed:
        return
    expected = {'discord.py': '2.7.1', 'discord-ext-voice-recv': '0.5.3a180'}
    for name, version in expected.items():
        if importlib.metadata.version(name) != version:
            raise RuntimeError(f'Для DAVE-адаптера требуется {name}=={version}. Запустите setup.bat.')
    import davey
    from discord.opus import Decoder, OpusError
    from discord.ext.voice_recv.router import PacketRouter
    from discord.ext.voice_recv.opus import PacketDecoder
    original_feed = PacketRouter.feed_rtp
    original_decode = PacketDecoder._decode_packet
    original_ready = PacketDecoder._flag_ready_state
    original_reset = PacketDecoder.reset
    original_destroy = PacketDecoder.destroy
    logger = logging.getLogger('serega.voice')
    last_warning = [0.0]

    def feed(self, packet):
        # This pinned client uses RTP payload 0x78 for Opus. Camera/video/RTX
        # packets must never enter the audio DAVE decryptor or Opus decoder.
        if packet.payload != 0x78:
            stats['non_audio_dropped'] += 1
            return
        vc = self.reader.voice_client
        connection = vc._connection
        session = connection.dave_session
        if connection.dave_protocol_version and session:
            user_id = vc._get_id_from_ssrc(packet.ssrc)
            if not user_id:
                stats['dave_dropped'] += 1
                return
            try:
                packet.decrypted_data = session.decrypt(int(user_id), davey.MediaType.audio,
                                                        bytes(packet.decrypted_data))
                stats['dave_decrypted'] += 1
            except Exception:
                stats['dave_dropped'] += 1
                now = time.monotonic()
                if now - last_warning[0] > 10:
                    logger.warning('DAVE: пакет не расшифрован и пропущен (возможен переход ключей)')
                    last_warning[0] = now
                return
        return original_feed(self, packet)

    def decode(self, packet):
        try:
            return original_decode(self, packet)
        except OpusError:
            stats['opus_bad'] += 1
            self._decoder = None if self.sink.wants_opus() else Decoder()
            return packet, bytes(Decoder.FRAME_SIZE)

    def next_packet(self, timeout):
        pending = getattr(self, '_serega_pending_packets', None)
        if pending:
            return pending.popleft()
        packet = self._buffer.pop(timeout=timeout)
        if packet is None:
            # voice-recv flushes the jitter buffer at a sequence gap and then
            # returns only packets[0], discarding the rest. Keep all received
            # packets in order; the heap itself bounds this tail to maxsize.
            packets = self._buffer.flush() if self._buffer else []
            if not packets:
                return None
            self._serega_pending_packets = deque(packets[1:])
            stats['jitter_preserved'] += len(packets) - 1
            return packets[0]
        return packet if packet else self._make_fakepacket()

    def ready(self):
        if getattr(self, '_serega_pending_packets', None):
            self.router.waiter.register(self)
        else:
            original_ready(self)

    def reset(self):
        self._serega_pending_packets = deque()
        return original_reset(self)

    def destroy(self):
        self._serega_pending_packets = deque()
        return original_destroy(self)

    PacketRouter.feed_rtp = feed
    PacketDecoder._decode_packet = decode
    PacketDecoder._get_next_packet = next_packet
    PacketDecoder._flag_ready_state = ready
    PacketDecoder.reset = reset
    PacketDecoder.destroy = destroy
    _installed = True
