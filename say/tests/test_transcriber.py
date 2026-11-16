"""Tests for Transcriber: hallucination filter, model swap, path resolution."""

import unittest

import config
import transcriber


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

    def test_punctuation_variants_normalize_into_filter(self):
        from transcriber import _build_active_filter, _normalize_for_filter

        en = _build_active_filter('en')
        for variant in ('Thanks for watching.', 'Thank you for watching!', 'Please subscribe.'):
            self.assertIn(_normalize_for_filter(variant), en)

    def test_common_short_utterances_are_not_filtered(self):
        from transcriber import _build_active_filter, _normalize_for_filter

        en = _build_active_filter('en')
        for phrase in ('You', 'Thank you.', 'Thanks!', 'Bye.', 'Goodbye', 'Okay.', 'I love you.'):
            self.assertNotIn(_normalize_for_filter(phrase), en)

    def test_common_short_utterances_survive_transcription(self):
        import numpy as np

        old_language = config.LANGUAGE
        config.LANGUAGE = 'en'
        try:
            for phrase in ('Okay.', 'Thank you.', 'Bye.'):
                t = transcriber.Transcriber('mlx-community/whisper-small-mlx')
                t._loaded = True
                t._backend = 'whisper-mlx'
                t._transcribe_whisper = lambda _audio, prompt_override=None, result=phrase: result
                audio = np.full(config.SAMPLE_RATE, 0.05, dtype=np.float32)
                self.assertEqual(t.transcribe(audio), phrase)
        finally:
            config.LANGUAGE = old_language

    def test_moonshine_output_uses_shared_dictionary_repair(self):
        import numpy as np
        import postprocess

        old_terms = config.DICTIONARY
        config.DICTIONARY = ['Claude Code']
        try:
            class Line:
                text = 'cloud code'

            class Result:
                lines = [Line()]

            class Moonshine:
                def transcribe_without_streaming(self, audio_data, sample_rate=16000, flags=0):
                    self.sample_count = len(audio_data)
                    self.sample_rate = sample_rate
                    return Result()

            t = transcriber.Transcriber('moonshine/medium-streaming-en')
            t._loaded = True
            t._backend = 'moonshine'
            t._ms = Moonshine()
            audio = np.full(config.SAMPLE_RATE, 0.05, dtype=np.float32)
            decoded = t.transcribe(audio, prompt_override='Claude Code')
            self.assertEqual(decoded, 'cloud code')
            self.assertEqual(postprocess.apply(decoded), 'Claude Code.')
        finally:
            config.DICTIONARY = old_terms


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


class WhisperDecodeOptionsTests(unittest.TestCase):
    """The decoder kwargs are the whole punctuation fix — assert them explicitly.

    Measured on real dictations (say/tools/replay_bench.py): a well-punctuated
    `initial_prompt` takes unpunctuated transcripts from 58.3% to 0% and lifts
    sentence density from 0.85 to 5.75 per 100 words. Disabling
    `condition_on_previous_text` does NOT help — it drops density to 2.44 by
    discarding cross-window context. These tests pin both so a refactor cannot
    silently undo the fix.
    """

    def setUp(self):
        self._saved = {
            k: getattr(config, k, None)
            for k in ('CONDITION_ON_PREVIOUS_TEXT', 'STYLE_SEED_ENABLED',
                      'DICTIONARY', 'INITIAL_PROMPT', 'LANGUAGE')
        }
        config.CONDITION_ON_PREVIOUS_TEXT = False
        config.STYLE_SEED_ENABLED = True
        config.DICTIONARY = []
        config.INITIAL_PROMPT = ''

    def tearDown(self):
        for k, v in self._saved.items():
            setattr(config, k, v)

    def _capture_kwargs(self, seconds: float = 8.0):
        """Run a fake decode and return the kwargs it was handed.

        Defaults to a clip long enough to earn the style seed. Short clips
        deliberately get a reduced prompt — see
        `test_short_clips_do_not_get_the_style_seed`.
        """
        captured = {}

        class _FakeMLX:
            @staticmethod
            def transcribe(audio, **kwargs):
                captured.update(kwargs)
                return {'text': 'hello world'}

        t = transcriber.Transcriber('mlx-community/whisper-small-mlx')
        t._mlx_whisper = _FakeMLX
        t._resolved_path = 'mlx-community/whisper-small-mlx'
        t._transcribe_whisper([0.0] * int(16000 * seconds))
        return captured

    def test_conditioning_follows_config(self):
        self.assertFalse(self._capture_kwargs()['condition_on_previous_text'])
        config.CONDITION_ON_PREVIOUS_TEXT = True
        self.assertTrue(self._capture_kwargs()['condition_on_previous_text'])

    def test_shipped_default_keeps_conditioning_on(self):
        """Regression guard: the measured best config seeds the style AND keeps
        conditioning enabled. Turning it off looks intuitive and is worse."""
        # _saved holds the values captured before setUp overrode them, i.e.
        # exactly what config.py ships.
        self.assertTrue(self._saved['CONDITION_ON_PREVIOUS_TEXT'])
        self.assertTrue(self._saved['STYLE_SEED_ENABLED'])

    def test_style_seed_is_passed_as_initial_prompt(self):
        prompt = self._capture_kwargs().get('initial_prompt', '')
        self.assertIn('full stops', prompt)

    def test_dictionary_terms_reach_the_decoder(self):
        config.DICTIONARY = ['Claude Code']
        self.assertIn('Claude Code', self._capture_kwargs().get('initial_prompt', ''))

    def test_no_prompt_key_when_everything_is_disabled(self):
        config.STYLE_SEED_ENABLED = False
        self.assertNotIn('initial_prompt', self._capture_kwargs())

    def test_short_clips_do_not_get_the_style_seed(self):
        """A strong text prior with almost no audio behind it makes the
        decoder follow the prompt and loop. A 0.63 s clip once produced 222
        words of 'available'. Vocabulary still goes through — it is a short
        comma list, not two sentences of prose."""
        config.DICTIONARY = ['Claude Code']
        prompt = self._capture_kwargs(seconds=0.6).get('initial_prompt', '')
        self.assertNotIn('full stops', prompt)
        self.assertIn('Claude Code', prompt)


class RealtimeWarningTests(unittest.TestCase):
    """B8: a decode slower than real time is logged as a warning, by name."""

    def test_slow_decode_warns(self):
        import transcriber

        with self.assertLogs('say.transcriber', level='WARNING') as logs:
            rtf = transcriber.note_realtime(8.0, 35.7, 'whisper')
        self.assertGreater(rtf, transcriber.SLOW_RTF)
        self.assertTrue(any('below real time' in line for line in logs.output))

    def test_a_fast_decode_is_only_info(self):
        import transcriber

        with self.assertLogs('say.transcriber', level='INFO') as logs:
            transcriber.note_realtime(10.0, 1.4, 'whisper')
        self.assertFalse(any('WARNING' in line for line in logs.output))

    def test_tiny_clips_never_warn(self):
        import transcriber

        with self.assertLogs('say.transcriber', level='INFO') as logs:
            transcriber.note_realtime(0.4, 19.2, 'whisper')
        self.assertFalse(any('WARNING' in line for line in logs.output))


class SilenceGateTests(unittest.TestCase):
    """Silence is never handed to the decoder, which answers it with invented
    text. 33 of 484 stored insertions were of that kind ('Monica Monica.')."""

    def _transcriber(self):
        import numpy as np

        calls = []
        t = transcriber.Transcriber('mlx-community/whisper-small-mlx')
        t._loaded = True
        t._backend = 'whisper-mlx'

        def fake(audio, prompt_override=None):
            calls.append(len(audio))
            return 'hello world'

        t._transcribe_whisper = fake
        return t, calls, np

    def test_digital_silence_is_not_decoded(self):
        t, calls, np = self._transcriber()
        self.assertEqual(t.transcribe(np.zeros(16000 * 3, dtype=np.float32)), '')
        self.assertEqual(calls, [])

    def test_room_tone_below_the_floor_is_not_decoded(self):
        t, calls, np = self._transcriber()
        rng = np.random.default_rng(0)
        quiet = (rng.standard_normal(16000 * 3) * transcriber._SILENCE_RMS * 0.5)
        self.assertEqual(t.transcribe(quiet.astype(np.float32)), '')
        self.assertEqual(calls, [])

    def test_speech_level_audio_is_decoded(self):
        t, calls, np = self._transcriber()
        rng = np.random.default_rng(0)
        # 0.0084 is the 5th-percentile RMS of real stored dictations.
        voiced = (rng.standard_normal(16000 * 3) * 0.0084).astype(np.float32)
        self.assertEqual(t.transcribe(voiced), 'hello world')
        self.assertEqual(len(calls), 1)

    def test_a_long_clip_with_one_short_utterance_is_decoded(self):
        """Whole-piece RMS dilutes with silence, so the floor has to leave
        room for a short phrase inside a long hold."""
        t, calls, np = self._transcriber()
        rng = np.random.default_rng(0)
        clip = np.zeros(16000 * 30, dtype=np.float32)
        clip[:16000 * 2] = rng.standard_normal(16000 * 2) * 0.0224  # corpus median
        self.assertEqual(t.transcribe(clip), 'hello world')

    def test_an_unmeasurable_buffer_is_decoded_not_dropped(self):
        self.assertFalse(transcriber._is_silent(object()))


class CallFloorTests(unittest.TestCase):
    """Whisper pads every call to 30 s, so ~2.1-2.3 s is the cost of any call.
    Judged by RTF alone, every short clip looked like a throttled machine."""

    def test_a_short_clip_at_the_call_floor_is_not_slow(self):
        import transcriber

        with self.assertLogs('say.transcriber', level='INFO') as logs:
            transcriber.note_realtime(1.2, 2.1, 'whisper')
        self.assertFalse(any('WARNING' in line for line in logs.output))

    def test_a_short_clip_far_past_the_floor_still_warns(self):
        import transcriber

        with self.assertLogs('say.transcriber', level='WARNING') as logs:
            transcriber.note_realtime(2.0, 9.0, 'whisper')
        self.assertTrue(any('below real time' in line for line in logs.output))


class MemoryReleaseTests(unittest.TestCase):
    """Measured: the idle engine held 3.7 GB. The cache and the weights can both
    be given back without anyone waiting for it."""

    def _whisper(self):
        t = transcriber.Transcriber('mlx-community/whisper-small-mlx')
        t._loaded = True
        t._backend = 'whisper-mlx'
        return t

    def test_unload_drops_the_weights_and_keeps_the_transcriber_ready(self):
        import sys
        import types
        from unittest.mock import patch

        holder = types.SimpleNamespace(model=object(), model_path='m')
        cleared = []
        fake_tx = types.ModuleType('mlx_whisper.transcribe')
        fake_tx.ModelHolder = holder
        fake_core = types.ModuleType('mlx.core')
        fake_core.clear_cache = lambda: cleared.append(True)
        fake_mlx = types.ModuleType('mlx')
        fake_mlx.core = fake_core
        with patch.dict(sys.modules, {'mlx_whisper.transcribe': fake_tx, 'mlx': fake_mlx,
                                      'mlx.core': fake_core}):
            t = self._whisper()
            self.assertTrue(t.unload_idle())
            self.assertIsNone(holder.model)
            self.assertTrue(t._loaded)  # mlx-whisper reloads on its own; no warm-up again
            self.assertFalse(t.unload_idle())  # nothing left to drop
            t.release_cache()
        self.assertEqual(len(cleared), 2)

    def test_other_backends_are_left_alone(self):
        t = transcriber.Transcriber('moonshine/medium-streaming-en')
        t._backend = 'moonshine'
        self.assertFalse(t.unload_idle())
        t.release_cache()  # no-op, no raise

    def test_a_missing_runtime_never_raises(self):
        import sys
        from unittest.mock import patch

        with patch.dict(sys.modules, {'mlx_whisper.transcribe': None, 'mlx.core': None}):
            t = self._whisper()
            self.assertFalse(t.unload_idle())
            t.release_cache()
