import struct
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from bot.voice.compat import install


class ReceiveGapTests(unittest.TestCase):
    def decoder(self):
        install()
        from discord.ext.voice_recv.opus import PacketDecoder
        member = SimpleNamespace(id=1)
        voice = SimpleNamespace(guild=SimpleNamespace(get_member=lambda uid: member))
        router = SimpleNamespace(sink=SimpleNamespace(wants_opus=lambda: True, voice_client=voice), waiter=Mock())
        decoder = PacketDecoder(router, 99)
        decoder.set_user_id(1)
        return decoder

    def packet(self, sequence):
        from discord.ext.voice_recv.rtp import RTPPacket
        packet = RTPPacket(struct.pack('>BBHII', 0x80, 0x78, sequence, sequence * 960, 99) + b'\x00' * 20)
        packet.decrypted_data = b'opus'
        return packet

    def test_one_missing_rtp_packet_does_not_discard_later_received_audio(self):
        decoder = self.decoder()
        for sequence in (1, 2):
            decoder.push_packet(self.packet(sequence))
        self.assertEqual(decoder.pop_data().packet.sequence, 1)
        for sequence in (4, 5):
            decoder.push_packet(self.packet(sequence))
        actual = []
        for _ in range(4):
            data = decoder.pop_data()
            if data:
                actual.append(data.packet.sequence)
        self.assertEqual(actual, [2, 4, 5])

    def test_preserved_tail_keeps_reader_awake_and_reset_drops_old_audio(self):
        decoder = self.decoder()
        for sequence in (1, 2):
            decoder.push_packet(self.packet(sequence))
        decoder.pop_data()
        for sequence in (4, 5):
            decoder.push_packet(self.packet(sequence))
        decoder.pop_data()
        decoder.router.waiter.reset_mock()
        self.assertEqual(decoder.pop_data().packet.sequence, 4)
        decoder.router.waiter.register.assert_called_with(decoder)
        decoder.reset()
        for sequence in (40, 41):
            decoder.push_packet(self.packet(sequence))
        self.assertEqual(decoder.pop_data().packet.sequence, 40)

