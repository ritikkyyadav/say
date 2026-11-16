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



class FocusTests(unittest.TestCase):
    """What the person dictates in, and what each choice implies."""

    def test_the_four_focuses_exist(self):
        self.assertEqual(
            [g['id'] for g in languages.groups()], ['english', 'european', 'south_asian', 'any']
        )

    def test_every_focus_code_is_a_known_language(self):
        known = {code for code, _ in languages.LANGUAGES}
        for group in languages.FOCUS_GROUPS:
            for code in group['codes']:
                self.assertIn(code, known, (group['id'], code))

    def test_english_is_fixed_and_the_others_detect(self):
        self.assertEqual(languages.default_code_for('english'), 'en')
        for focus in ('european', 'south_asian', 'any'):
            self.assertIsNone(languages.default_code_for(focus), focus)

    def test_hindi_and_urdu_are_in_the_south_asian_focus(self):
        codes = [o['code'] for o in languages.options_for('south_asian')]
        self.assertIn('hi', codes)
        self.assertIn('ur', codes)
        self.assertIn('en', codes)

    def test_the_recommended_models_are_in_the_catalogue(self):
        import model_catalog

        for group in languages.FOCUS_GROUPS:
            self.assertTrue(model_catalog.is_known(str(group['recommended_model'])), group['id'])
        self.assertFalse(languages.applies_to(languages.recommended_model('english')))
        self.assertTrue(languages.applies_to(languages.recommended_model('south_asian')))

    def test_unknown_focus_is_refused_and_current_falls_back(self):
        with self.assertRaises(languages.BadLanguage):
            languages.normalise_focus('martian')
        saved = (config.LANGUAGE_FOCUS, config.LANGUAGE)
        try:
            config.LANGUAGE_FOCUS = 'martian'
            config.LANGUAGE = 'en'
            self.assertEqual(languages.current_focus(), 'english')
            config.LANGUAGE = None
            self.assertEqual(languages.current_focus(), 'any')
            config.LANGUAGE_FOCUS = 'South_Asian'
            self.assertEqual(languages.current_focus(), 'south_asian')
        finally:
            config.LANGUAGE_FOCUS, config.LANGUAGE = saved

if __name__ == '__main__':
    unittest.main()
