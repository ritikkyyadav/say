"""Tests for Transcriber: hallucination filter, model swap, path resolution."""

import unittest


class HallucinationFilterTests(unittest.TestCase):
    def test_english_filter_includes_known_phrases(self):
        from transcriber import _build_active_filter

        en = _build_active_filter('en')
        self.assertIn('subscribe to my channel', en)
        self.assertIn('thanks for watching', en)
        self.assertIn('[blank_audio]', en)

    def test_unknown_language_falls_back_to_neutral_only(self):
        from transcriber import _build_active_filter

        zh = _build_active_filter('zh')
        # Includes language-neutral entries
        self.assertIn('[blank_audio]', zh)
        # Does NOT include English-only entries
        self.assertNotIn('subscribe to my channel', zh)

    def test_auto_detect_includes_all_languages(self):
        from transcriber import _build_active_filter

        union = _build_active_filter(None)
        self.assertIn('thanks for watching', union)
        self.assertIn('gracias', union)
        self.assertIn('merci', union)


class TranscriberApiTests(unittest.TestCase):
    def test_swap_model_marks_unloaded(self):
        from transcriber import Transcriber

        t = Transcriber('mlx-community/whisper-large-v3-turbo')
        # Pretend it was loaded
        t._loaded = True
        t.swap_model('mlx-community/whisper-small-mlx')
        self.assertFalse(t._loaded)
        self.assertEqual(t.model_path, 'mlx-community/whisper-small-mlx')

    def test_swap_model_noop_for_same_path(self):
        from transcriber import Transcriber

        t = Transcriber('mlx-community/whisper-large-v3-turbo')
        t._loaded = True
        t.swap_model('mlx-community/whisper-large-v3-turbo')
        self.assertTrue(t._loaded)  # unchanged

    def test_resolve_falls_back_to_repo_id(self):
        from transcriber import Transcriber

        t = Transcriber()
        # No bundled path on disk → returns the original requested string.
        self.assertEqual(
            t._resolve_model_path('mlx-community/whisper-large-v3-turbo'),
            'mlx-community/whisper-large-v3-turbo',
        )


if __name__ == '__main__':
    unittest.main()
