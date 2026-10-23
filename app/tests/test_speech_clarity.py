import unittest
from bot.services.tts_service import prepare_speech


class SpeechClarityTests(unittest.TestCase):
    def test_technical_abbreviations_are_spoken_but_names_are_preserved(self):
        self.assertEqual(prepare_speech('FPS, CPU, GPU, SSD, ММР.'),
                         'эф пи эс, си пи ю, джи пи ю, эс эс ди, эм эм эр.')
        self.assertEqual(prepare_speech('Dota 2, Wi-Fi, USB.'), 'Дота два, вай фай, ю эс би.')
        self.assertEqual(prepare_speech('RAMBO и FPSMeter.'), 'RAMBO и FPSMeter.')

    def test_units_and_percent_endings_preserve_quantity(self):
        self.assertEqual(prepare_speech('1%, 2%, 5%, 11%, 21%.'),
                         'один процент, два процента, пять процентов, одиннадцать процентов, двадцать один процент.')
        self.assertEqual(prepare_speech('16 ГБ и 512 MB.'),
                         'шестнадцать гигабайт и пятьсот двенадцать мегабайт.')
        self.assertIn('1.5', prepare_speech('1.5 ГБ'))
        self.assertIn('7.39', prepare_speech('Патч 7.39.'))

    def test_asides_and_identifiers_do_not_merge_words(self):
        self.assertEqual(prepare_speech('Возьми SSD (быстрый накопитель).'),
                         'Возьми эс эс ди, быстрый накопитель.')
        self.assertEqual(prepare_speech('два_слова. Блять!'), 'два слова. Блять!')
