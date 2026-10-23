import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from bot.voice.compat import check_voice_versions


class VoiceVersionTests(unittest.TestCase):
    def test_installed_voice_versions(self):
        check_voice_versions()

    def test_archive_build_requires_tested_source(self):
        versions = {'discord.py': '2.7.1', 'discord-ext-voice-recv': '0.5.3a0'}
        for commit, accepted in [
            ('ac04ea7b0941112e83767cf1c1469b408fa06748', True),
            ('wrong-commit', False),
        ]:
            direct = json.dumps({'url': 'https://github.com/imayhaveborkedit/'
                                'discord-ext-voice-recv/archive/' + commit + '.zip'})
            with self.subTest(commit=commit), patch(
                'importlib.metadata.version', side_effect=versions.__getitem__
            ), patch('importlib.metadata.distribution', return_value=SimpleNamespace(
                read_text=lambda name: direct
            )):
                if accepted:
                    check_voice_versions()
                else:
                    with self.assertRaises(RuntimeError):
                        check_voice_versions()

    def test_unrelated_versions_are_rejected(self):
        for versions in [
            {'discord.py': '2.7.1', 'discord-ext-voice-recv': '0.5.2a179'},
            {'discord.py': '2.6.0', 'discord-ext-voice-recv': '0.5.3a180'},
        ]:
            with patch('importlib.metadata.version', side_effect=versions.__getitem__):
                with self.assertRaises(RuntimeError):
                    check_voice_versions()
