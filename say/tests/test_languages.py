"""The language setting: validation and which models honour it."""
from __future__ import annotations

import unittest

import config
import languages


class NormaliseTests(unittest.TestCase):
    def test_none_and_auto_mean_automatic(self):
        for value in (None, '', 'auto', 'Automatic', ' none '):
            self.assertIsNone(languages.normalise(value), value)

    def test_codes_are_lowercased_and_regions_dropped(self):
        self.assertEqual(languages.normalise('EN'), 'en')
        self.assertEqual(languages.normalise('en-US'), 'en')
        self.assertEqual(languages.normalise('pt_BR'), 'pt')
        self.assertEqual(languages.normalise('yue'), 'yue')

    def test_garbage_is_refused(self):
        for value in ('english', 'e', '12', 'en us', 'x' * 9):
            with self.assertRaises(languages.BadLanguage, msg=value):
                languages.normalise(value)


class CatalogueTests(unittest.TestCase):
    def test_hindi_is_offered(self):
        codes = [o['code'] for o in languages.options()]
        self.assertIn('hi', codes)
        self.assertIn('en', codes)
        self.assertEqual(len(codes), len(set(codes)))

    def test_names(self):
        self.assertEqual(languages.name_for(None), 'Automatic')
        self.assertEqual(languages.name_for('hi'), 'Hindi')
        self.assertEqual(languages.name_for('xx'), 'XX')

    def test_current_tolerates_a_bad_setting(self):
        saved = config.LANGUAGE
        try:
            config.LANGUAGE = 'not a code'
            self.assertEqual(languages.current(), 'en')
            config.LANGUAGE = None
            self.assertIsNone(languages.current())
        finally:
            config.LANGUAGE = saved


class AppliesToTests(unittest.TestCase):
    def test_whisper_reads_it_and_moonshine_does_not(self):
        self.assertTrue(languages.applies_to('mlx-community/whisper-large-v3-turbo'))
        self.assertFalse(languages.applies_to('moonshine/medium-streaming-en'))
        self.assertFalse(languages.applies_to('nonsense/model'))


if __name__ == '__main__':
    unittest.main()
