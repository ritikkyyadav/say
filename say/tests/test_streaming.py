"""Tests for silence segmentation and background segment transcription."""

import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import streaming  # noqa: E402

SR = config.SAMPLE_RATE


def speech(seconds: float, level: float = 0.05) -> np.ndarray:
    rng = np.random.default_rng(0)
    return (rng.standard_normal(int(seconds * SR)) * level).astype('float32')


def silence(seconds: float) -> np.ndarray:
    return np.zeros(int(seconds * SR), dtype='float32')


class SplitOnSilenceTests(unittest.TestCase):
    def test_short_clip_is_one_segment(self):
        self.assertEqual(len(streaming.split_on_silence(speech(5))), 1)

    def test_splits_at_pauses(self):
        clip = np.concatenate([speech(12), silence(1.0), speech(12), silence(1.0), speech(10)])
        self.assertEqual(len(streaming.split_on_silence(clip)), 3)

    def test_segmentation_is_lossless(self):
        """Cuts land inside silence, so no audio may be dropped at the seams."""
        clip = np.concatenate([speech(12), silence(1.0), speech(9)])
        segs = streaming.split_on_silence(clip)
        self.assertTrue(np.array_equal(np.concatenate(segs), clip))

    def test_does_not_cut_on_a_short_gap(self):
        """A gap between words is not a pause — cutting there would split speech."""
        clip = np.concatenate([speech(12), silence(0.15), speech(6)])
        self.assertEqual(len(streaming.split_on_silence(clip)), 1)

    def test_does_not_cut_below_the_minimum_length(self):
        clip = np.concatenate([speech(2), silence(1.0), speech(2)])
        self.assertEqual(len(streaming.split_on_silence(clip)), 1)

    def test_forces_a_cut_at_the_maximum(self):
        """Continuous speech still gets segmented, or nothing would stream."""
        segs = streaming.split_on_silence(speech(70))
        self.assertGreaterEqual(len(segs), 2)
        for s in segs[:-1]:
            self.assertLessEqual(len(s) / SR, streaming.SEGMENT_MAX_SECONDS + 0.2)

    def test_empty_audio(self):
        self.assertEqual(streaming.split_on_silence(np.array([], dtype='float32')), [])


class CarryOverPromptTests(unittest.TestCase):
    def test_empty_previous_returns_base(self):
        self.assertEqual(streaming.carry_over_prompt('SEED', ''), 'SEED')

    def test_appends_previous_text(self):
        out = streaming.carry_over_prompt('SEED', 'Hello there.')
        self.assertEqual(out, 'SEED Hello there.')

    def test_truncates_to_the_tail(self):
        long = 'word ' * 200
        out = streaming.carry_over_prompt('SEED', long)
        self.assertLess(len(out), len(long))
        self.assertTrue(out.startswith('SEED'))

    def test_truncation_starts_on_a_word_boundary(self):
        out = streaming.carry_over_prompt('SEED', 'alpha ' * 100 + 'omega')
        self.assertNotIn('lpha', out.replace('alpha', ''))


class SilenceSegmenterTests(unittest.TestCase):
    def _feed(self, seg, audio, block=1024):
        cuts = 0
        for i in range(0, len(audio), block):
            if seg.feed(audio[i:i + block]):
                cuts += 1
        return cuts

    def test_matches_the_offline_splitter_on_a_pause(self):
        seg = streaming.SilenceSegmenter()
        clip = np.concatenate([speech(12), silence(1.0), speech(6)])
        self.assertEqual(self._feed(seg, clip), 1)

    def test_no_cut_on_short_audio(self):
        seg = streaming.SilenceSegmenter()
        self.assertEqual(self._feed(seg, speech(4)), 0)

    def test_reset_clears_progress(self):
        seg = streaming.SilenceSegmenter()
        self._feed(seg, speech(7))
        seg.reset()
        self.assertEqual(self._feed(seg, silence(1.0)), 0)


class _FakeTranscriber:
    """Records the prompt each segment was given."""

    def __init__(self, texts=None):
        self.prompts = []
        self.texts = list(texts or [])
        self.calls = 0

    def transcribe(self, audio, prompt_override=None):
        self.prompts.append(prompt_override)
        self.calls += 1
        return self.texts.pop(0) if self.texts else f'seg{self.calls}'


class SegmentedSessionTests(unittest.TestCase):
    def test_joins_background_segments_with_the_tail(self):
        t = _FakeTranscriber(['one', 'two', 'three'])
        s = streaming.SegmentedSession(t, base_prompt='SEED')
        s.push(speech(1))
        s.push(speech(1))
        self.assertEqual(s.finish(speech(1)), 'one two three')

    def test_tail_only_when_no_segments_closed(self):
        t = _FakeTranscriber(['only'])
        s = streaming.SegmentedSession(t, base_prompt='SEED')
        self.assertEqual(s.finish(speech(1)), 'only')

    def test_carry_over_reaches_later_segments(self):
        """The whole point: segment N must see segment N-1's text.

        Without this, splitting the audio loses the cross-window context that
        produces sentence structure — measurably worse punctuation.
        """
        t = _FakeTranscriber(['First part.', 'Second part.'])
        s = streaming.SegmentedSession(t, base_prompt='SEED')
        s.push(speech(1))
        # A tail long enough to carry context; anything under
        # SHORT_SEGMENT_SECONDS is deliberately decoded without it (B3).
        s.finish(speech(3))
        self.assertEqual(t.prompts[0], 'SEED')
        self.assertIn('First part.', t.prompts[1])

    def test_empty_segments_are_skipped(self):
        t = _FakeTranscriber(['', 'kept'])
        s = streaming.SegmentedSession(t, base_prompt='SEED')
        s.push(speech(1))
        self.assertEqual(s.finish(speech(1)), 'kept')

    def test_a_failing_segment_does_not_lose_the_rest(self):
        class Boom(_FakeTranscriber):
            def transcribe(self, audio, prompt_override=None):
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError('segment exploded')
                return 'survived'

        s = streaming.SegmentedSession(Boom(), base_prompt='SEED')
        s.push(speech(1))
        self.assertEqual(s.finish(speech(1)), 'survived')

    def test_cancel_discards_everything(self):
        t = _FakeTranscriber()
        s = streaming.SegmentedSession(t, base_prompt='SEED')
        s.push(speech(1))
        s.cancel()
        self.assertEqual(s.segment_count, 0)


if __name__ == '__main__':
    unittest.main()


class CompletenessTests(unittest.TestCase):
    """A partial transcript must be detectable — silently dropping words is
    worse than being slow, so `complete` is what lets the caller re-decode."""

    def test_complete_on_the_happy_path(self):
        s = streaming.SegmentedSession(_FakeTranscriber(['a', 'b']), base_prompt='S')
        s.push(speech(1))
        s.finish(speech(1))
        self.assertTrue(s.complete)

    def test_a_failed_segment_marks_the_result_incomplete(self):
        class Boom(_FakeTranscriber):
            def transcribe(self, audio, prompt_override=None):
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError('boom')
                return 'tail'

        s = streaming.SegmentedSession(Boom(), base_prompt='S')
        s.push(speech(1))
        s.finish(speech(1))
        self.assertFalse(s.complete)

    def test_timeout_marks_the_result_incomplete(self):
        import time as _t

        class Slow(_FakeTranscriber):
            def transcribe(self, audio, prompt_override=None):
                _t.sleep(0.4)
                return 'slow'

        s = streaming.SegmentedSession(Slow(), base_prompt='S')
        s.push(speech(1))
        s.finish(speech(1), timeout=0.01)
        self.assertFalse(s.complete)


class AttemptCountTests(unittest.TestCase):
    """`attempted` is what lets the controller tell silence from failure."""

    class _Quiet:
        def transcribe(self, audio, prompt_override=None):
            return ''

    def test_segments_and_tail_are_counted(self):
        session = streaming.SegmentedSession(self._Quiet(), base_prompt='')
        session.push(speech(9))
        session.push(speech(9))
        self.assertEqual(session.finish(speech(1)), '')
        self.assertEqual(session.attempted, 3)
        self.assertTrue(session.complete)

    def test_no_tail_counts_only_the_segments(self):
        session = streaming.SegmentedSession(self._Quiet(), base_prompt='')
        session.push(speech(9))
        session.finish(None)
        self.assertEqual(session.attempted, 1)

    def test_a_failing_decode_is_still_an_attempt(self):
        class Boom:
            def transcribe(self, audio, prompt_override=None):
                raise RuntimeError('decoder fell over')

        session = streaming.SegmentedSession(Boom(), base_prompt='')
        session.push(speech(9))
        session.finish(None)
        self.assertEqual(session.attempted, 1)
        self.assertFalse(session.complete)


class ShortPieceTests(unittest.TestCase):
    """B3: a piece shorter than SHORT_SEGMENT_SECONDS gets the short prompt
    (terms only) and no carry-over — the guard whole-clip mode already had."""

    class _Recorder:
        def __init__(self):
            self.prompts: list[str] = []

        def transcribe(self, audio, prompt_override=None):
            self.prompts.append(prompt_override)
            return 'words'

    def test_a_decodable_short_tail_gets_the_short_prompt_and_no_carry_over(self):
        rec = self._Recorder()
        session = streaming.SegmentedSession(rec, base_prompt='SEED', short_prompt='TERMS')
        session.push(speech(9))
        session.finish(speech(1.5))
        self.assertEqual(rec.prompts[0], 'SEED')
        self.assertEqual(rec.prompts[1], 'TERMS')

    def test_a_tail_below_the_decoder_minimum_marks_the_session_incomplete(self):
        rec = self._Recorder()
        session = streaming.SegmentedSession(rec, base_prompt='SEED', short_prompt='TERMS')
        session.push(speech(9))
        result = session.finish(speech(0.5))
        self.assertEqual(result, 'words')
        self.assertFalse(session.complete)
        self.assertEqual(session.attempted, 1)
        self.assertEqual(len(rec.prompts), 1)

    def test_a_long_tail_still_carries_the_previous_text(self):
        rec = self._Recorder()
        session = streaming.SegmentedSession(rec, base_prompt='SEED', short_prompt='TERMS')
        session.push(speech(9))
        session.finish(speech(3))
        self.assertEqual(rec.prompts[1], 'SEED words')

    def test_the_short_prompt_defaults_to_the_base_prompt(self):
        rec = self._Recorder()
        session = streaming.SegmentedSession(rec, base_prompt='SEED')
        session.finish(speech(1.5))
        self.assertEqual(rec.prompts, ['SEED'])

    def test_the_threshold_matches_the_whole_clip_guard(self):
        import transcriber

        self.assertEqual(streaming.SHORT_SEGMENT_SECONDS, transcriber._STYLE_SEED_MIN_SECONDS)


class ProgressTests(unittest.TestCase):
    """B8: the session reports (done, total) so the wait can be shown."""

    class _Slowish:
        def transcribe(self, audio, prompt_override=None):
            return 'x'

    def test_progress_is_reported_per_decode_and_ends_complete(self):
        seen = []
        session = streaming.SegmentedSession(self._Slowish(), on_progress=lambda d, t: seen.append((d, t)))
        session.push(speech(9))
        session.push(speech(9))
        session.finish(speech(3))
        self.assertEqual(seen[-1], (3, 3))
        self.assertEqual(session.progress, (3, 3))
        # done never exceeds total, and both only ever grow.
        for (d, t), (d2, t2) in zip(seen, seen[1:], strict=False):
            self.assertLessEqual(d, t)
            self.assertLessEqual(d, d2)
            self.assertLessEqual(t, t2)

    def test_a_raising_callback_never_breaks_the_session(self):
        def boom(_d, _t):
            raise RuntimeError('ui went away')

        session = streaming.SegmentedSession(self._Slowish(), on_progress=boom)
        session.push(speech(9))
        self.assertEqual(session.finish(speech(3)), 'x x')
        self.assertTrue(session.complete)
