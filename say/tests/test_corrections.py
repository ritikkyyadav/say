"""Tests for the correction-capture layer — corrections.py + readback.py.

These pin the two distinctions the metric depends on: continuation is not
correction, and a rewrite is not correction. Both were chosen because the
naive version of the metric lies in exactly those two ways, and a diff that
gets them wrong reports a working pipeline as broken (or the reverse).
"""
from __future__ import annotations

import os
import unittest
from tempfile import TemporaryDirectory

import config
import corrections
import readback
from server.store import TranscriptStore


class DiffTests(unittest.TestCase):
    def test_a_misheard_word_is_a_correction(self):
        d = corrections.diff('we need a rapper for this', 'we need a wrapper for this')
        self.assertTrue(corrections.looks_like_correction(d))
        self.assertEqual(d.distance, 1)
        self.assertEqual(d.words, 6)
        self.assertEqual(corrections.term_pairs(d), [('rapper', 'wrapper')])

    def test_typing_on_after_the_span_is_not_a_correction(self):
        """The common case: Say gets it right and the user keeps writing."""
        d = corrections.diff('The build passed.', 'The build passed. Then I ran the tests.')
        self.assertTrue(d.clean)
        self.assertEqual(d.rate, 0.0)
        self.assertFalse(corrections.looks_like_correction(d))

    def test_continuation_fused_into_the_final_word_is_still_peeled_off(self):
        """The cursor is left flush against our last token.

        The user's next keystrokes therefore land inside the same aligner
        block as our final word, and a naive diff scores one real fix plus
        seven words of new writing as a total rewrite of the span. Regression
        test: this scored rate=1.0 (and was discarded as a rewrite) before
        `_split_boundary_growth` existed.
        """
        d = corrections.diff(
            'I met Ritik at the cafe.',
            'I met Ritik at the café today and it was good.',
        )
        self.assertTrue(corrections.looks_like_correction(d))
        self.assertEqual(d.distance, 1)
        self.assertAlmostEqual(d.rate, 1 / 6, places=3)
        self.assertEqual(corrections.term_pairs(d), [('cafe.', 'café')])

    def test_expanding_the_first_word_is_not_mistaken_for_continuation(self):
        """Why the boundary split is trailing-only.

        Splitting the leading block too would turn "lets" -> "let us" into the
        nonsense pair ("lets", "us"), teaching the vocabulary layer a mapping
        the user never made.
        """
        d = corrections.diff('lets use cloud code', 'let us use Claude Code')
        pairs = dict(corrections.term_pairs(d))
        self.assertEqual(pairs.get('lets'), 'let us')
        self.assertEqual(pairs.get('cloud'), 'Claude')

    def test_a_rewrite_is_refused(self):
        d = corrections.diff('one two three four', 'completely different sentence entirely')
        self.assertGreater(d.rate, corrections.MAX_DRIFT)
        self.assertFalse(corrections.looks_like_correction(d))

    def test_case_and_punctuation_alone_are_the_formatters_business(self):
        for before, after in [
            ('Ship it on friday.', 'Ship it on Friday.'),
            ('ok then', 'ok then.'),
        ]:
            d = corrections.diff(before, after)
            self.assertTrue(d.clean, f'{before!r} -> {after!r}')
            self.assertFalse(corrections.looks_like_correction(d))

    def test_deleting_a_stray_word_counts(self):
        d = corrections.diff('send the the report now', 'send the report now')
        self.assertTrue(corrections.looks_like_correction(d))
        self.assertEqual(d.distance, 1)
        self.assertEqual(corrections.term_pairs(d), [])  # a deletion is not a term

    def test_empty_sides_are_never_corrections(self):
        self.assertFalse(corrections.looks_like_correction(corrections.diff('', 'hello')))
        self.assertFalse(corrections.looks_like_correction(corrections.diff('hello', '   ')))

    def test_long_substitutions_are_not_offered_as_vocabulary(self):
        d = corrections.diff(
            'the quick brown fox jumps over the lazy dog and keeps going',
            'the five six seven eight nine fox jumps over the lazy dog and keeps going',
        )
        for before, after in corrections.term_pairs(d):
            self.assertLessEqual(len(corrections.tokenize(before)), corrections.MAX_LEARNABLE_TOKENS)
            self.assertLessEqual(len(corrections.tokenize(after)), corrections.MAX_LEARNABLE_TOKENS)


class RateTests(unittest.TestCase):
    def test_untouched_insertions_are_the_denominator(self):
        """The insertions nobody had to fix leave no row — and are the point."""
        rows = [{'words': 10, 'edit_distance': 2}]
        s = corrections.summarise(rows, insertions=8)
        self.assertEqual(s.insertions, 8)
        self.assertEqual(s.corrected, 1)
        self.assertAlmostEqual(s.touch_rate, 1 / 8)
        self.assertAlmostEqual(s.word_rate, 2 / 10)

    def test_a_denominator_below_the_row_count_cannot_produce_a_rate_above_one(self):
        s = corrections.summarise([{'words': 4, 'edit_distance': 1}] * 3, insertions=0)
        self.assertEqual(s.insertions, 3)
        self.assertLessEqual(s.touch_rate, 1.0)

    def test_zero_distance_rows_are_ignored(self):
        s = corrections.summarise([{'words': 5, 'edit_distance': 0}], insertions=4)
        self.assertEqual(s.corrected, 0)
        self.assertEqual(s.touch_rate, 0.0)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.store = TranscriptStore(os.path.join(self._tmp.name, 't.db'))

    def tearDown(self):
        self.store.close()
        self._tmp.cleanup()

    def _insert(self, n: int = 1) -> list[int]:
        return [
            self.store.insert(
                text='we need a rapper for this',
                audio_seconds=3.0,
                inference_seconds=0.4,
                model='m',
                inject_method='accessibility',
                target_app='com.apple.Terminal',
            )
            for _ in range(n)
        ]

    def test_roundtrip_and_rate(self):
        ids = self._insert(4)
        for i in ids:  # the read-back compared all four
            self.store.mark_observed(i)
        d = corrections.diff('we need a rapper for this', 'we need a wrapper for this')
        self.store.record_correction(
            transcript_id=ids[0], source='observed', inserted=d.inserted,
            corrected=d.corrected, edit_distance=d.distance, words=d.words,
            target_app='com.apple.Terminal',
        )
        rollup = self.store.correction_summary()
        self.assertEqual(rollup['insertions'], 4)
        self.assertEqual(rollup['corrected'], 1)
        self.assertAlmostEqual(rollup['touch_rate'], 0.25)
        self.assertEqual(len(self.store.corrections()), 1)

    def test_summary_reports_a_real_fixes_count(self):
        """Regression: this field was hardcoded to 0 with a TODO."""
        ids = self._insert(2)
        for i in ids:
            self.store.mark_observed(i)
        self.store.record_correction(
            transcript_id=ids[0], source='manual', inserted='a rapper',
            corrected='a wrapper', edit_distance=1, words=2,
        )
        s = self.store.summary()
        self.assertEqual(s['fixes_count'], 1)
        self.assertAlmostEqual(s['correction_rate'], 0.5)
        self.assertIn('correction_word_rate', s)

    def test_observed_but_unedited_insertions_are_the_clean_ones(self):
        for i in self._insert(3):
            self.store.mark_observed(i)
        s = self.store.summary()
        self.assertEqual(s['fixes_count'], 0)
        self.assertEqual(s['correction_rate'], 0.0)
        self.assertEqual(s['insertions_30d'], 3)

    def test_insertions_we_never_read_back_are_not_counted_as_clean(self):
        """The denominator is what we measured, not what we inserted.

        Every dictation predating this feature — and every one in an app that
        exposes no text to the Accessibility API — was never compared against
        anything. Folding them in would report a pipeline that had never been
        measured at all as flawless. Regression test: with the denominator set
        to "all successful insertions", a real database of 109 historical rows
        rendered as "100% left untouched".
        """
        self._insert(109)  # history, from before the read-back existed
        s = self.store.summary()
        self.assertEqual(s['insertions_30d'], 0)
        self.assertEqual(s['correction_rate'], 0.0)
        self.assertEqual(s['fixes_count'], 0)

    def test_a_mix_of_measured_and_unmeasured_only_scores_the_measured(self):
        ids = self._insert(10)
        for i in ids[:4]:
            self.store.mark_observed(i)
        d = corrections.diff('we need a rapper for this', 'we need a wrapper for this')
        self.store.record_correction(
            transcript_id=ids[0], source='observed', inserted=d.inserted,
            corrected=d.corrected, edit_distance=d.distance, words=d.words,
        )
        s = self.store.summary()
        self.assertEqual(s['insertions_30d'], 4)
        self.assertAlmostEqual(s['correction_rate'], 0.25)


class RelocateTests(unittest.TestCase):
    def test_span_is_found_between_its_anchors(self):
        value = 'Dear team, we need a wrapper for this. Thanks.'
        self.assertEqual(
            readback._relocate(value, 'Dear team, ', '. Thanks.'),
            'we need a wrapper for this',
        )

    def test_missing_anchor_gives_up_rather_than_guessing(self):
        """A wrong region fabricates a correction, which is worse than no data."""
        self.assertIsNone(readback._relocate('totally different text', 'Dear team, ', '.'))
        self.assertIsNone(readback._relocate('Dear team, hello', 'Dear team, ', '. Thanks.'))

    def test_span_at_either_edge_of_the_field(self):
        self.assertEqual(readback._relocate('hello world', '', ' world'), 'hello')
        self.assertEqual(readback._relocate('hello world', 'hello ', ''), 'world')


class WatcherTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.store = TranscriptStore(os.path.join(self._tmp.name, 't.db'))
        self.tid = self.store.insert(
            text='we need a rapper for this', audio_seconds=3.0, inference_seconds=0.4,
            model='m', inject_method='accessibility', target_app='com.apple.Terminal',
        )
        self._orig_focus = readback._focused_element
        self._orig_read = readback._read_value
        self._orig_flag = config.LEARN_FROM_CORRECTIONS

    def tearDown(self):
        readback._focused_element = self._orig_focus
        readback._read_value = self._orig_read
        config.LEARN_FROM_CORRECTIONS = self._orig_flag
        self.store.close()
        self._tmp.cleanup()

    def _fake_ax(self, before: str, after: str):
        """Stand in for the focused text field: one value now, another later."""
        seq = [before, after]
        readback._focused_element = lambda: object()
        readback._read_value = lambda el: seq.pop(0) if seq else after  # type: ignore[assignment]

    def test_an_edit_inside_our_span_is_recorded(self):
        self._fake_ax(
            'Note: we need a rapper for this. End.',
            'Note: we need a wrapper for this. End.',
        )
        w = readback.CorrectionWatcher(self.store, delay_s=0)
        self.assertTrue(w.observe(self.tid, 'we need a rapper for this', 'com.apple.Terminal'))
        w.shutdown()
        rows = self.store.corrections()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['source'], 'observed')
        self.assertEqual(rows[0]['corrected'], 'we need a wrapper for this')
        self.assertEqual(rows[0]['edit_distance'], 1)

    def test_typing_on_afterwards_records_nothing(self):
        self._fake_ax(
            'we need a wrapper for this',
            'we need a wrapper for this and it should be typed today',
        )
        w = readback.CorrectionWatcher(self.store, delay_s=0)
        w.observe(self.tid, 'we need a wrapper for this')
        w.shutdown()
        self.assertEqual(self.store.corrections(), [])

    def test_a_clean_insertion_is_still_marked_as_measured(self):
        """Clean is a result. It only counts because we actually looked."""
        self._fake_ax('Note: we need a wrapper. End.', 'Note: we need a wrapper. End.')
        w = readback.CorrectionWatcher(self.store, delay_s=0)
        w.observe(self.tid, 'we need a wrapper')
        self.assertEqual(self.store.corrections(), [])
        self.assertEqual(self.store.correction_summary()['insertions'], 1)

    def test_a_field_that_vanished_leaves_the_insertion_unmeasured(self):
        readback._focused_element = lambda: object()
        seq = ['Note: we need a wrapper. End.']
        readback._read_value = lambda el: seq.pop(0) if seq else None
        w = readback.CorrectionWatcher(self.store, delay_s=0)
        w.observe(self.tid, 'we need a wrapper')
        self.assertEqual(self.store.correction_summary()['insertions'], 0)

    def test_a_span_we_cannot_find_is_never_observed(self):
        """If the app reformatted our text we cannot attribute later edits."""
        self._fake_ax('something else entirely', 'something else entirely')
        w = readback.CorrectionWatcher(self.store, delay_s=0)
        self.assertFalse(w.observe(self.tid, 'we need a rapper for this'))
        self.assertEqual(self.store.corrections(), [])

    def test_without_the_accessibility_grant_it_fails_closed(self):
        readback._focused_element = lambda: None
        w = readback.CorrectionWatcher(self.store, delay_s=0)
        self.assertFalse(w.observe(self.tid, 'anything at all'))
        self.assertEqual(self.store.corrections(), [])

    def test_the_switch_turns_it_off_entirely(self):
        self._fake_ax('a rapper here', 'a wrapper here')
        config.LEARN_FROM_CORRECTIONS = False
        w = readback.CorrectionWatcher(self.store, delay_s=0)
        self.assertFalse(w.observe(self.tid, 'a rapper here'))
        self.assertEqual(self.store.corrections(), [])

    def test_document_context_is_dropped_once_it_has_been_used(self):
        """The anchors are the only document text held, and only briefly."""
        self._fake_ax('Secret memo: a rapper here. More secrets.', 'Secret memo: a wrapper here. More secrets.')
        w = readback.CorrectionWatcher(self.store, delay_s=0)
        w.observe(self.tid, 'a rapper here')
        w.shutdown()
        self.assertEqual(w._pending, [])
        rows = self.store.corrections()
        self.assertEqual(len(rows), 1)
        # Nothing but our own span and its replacement reached the database.
        for value in rows[0].values():
            self.assertNotIn('Secret memo', str(value))
            self.assertNotIn('More secrets', str(value))

    def test_a_real_delay_arms_a_timer_that_shutdown_cancels(self):
        """The production path: nothing is read until the window elapses."""
        self._fake_ax('a rapper here', 'a wrapper here')
        w = readback.CorrectionWatcher(self.store, delay_s=30)
        self.assertTrue(w.observe(self.tid, 'a rapper here'))
        self.assertEqual(len(w._pending), 1)
        w.shutdown()
        self.assertEqual(w._pending, [])
        self.assertEqual(self.store.corrections(), [])

    def test_rapid_dictation_cannot_pile_up_timers(self):
        readback._focused_element = lambda: object()
        readback._read_value = lambda el: 'a rapper here'
        w = readback.CorrectionWatcher(self.store, delay_s=30)
        for _ in range(readback._MAX_PENDING * 2):
            w.observe(self.tid, 'a rapper here')
        self.assertLessEqual(len(w._pending), readback._MAX_PENDING)
        w.shutdown()

    def test_observation_never_raises_into_the_caller(self):
        def boom():
            raise RuntimeError('AX exploded')

        readback._focused_element = boom
        w = readback.CorrectionWatcher(self.store, delay_s=0)
        self.assertFalse(w.observe(self.tid, 'anything'))


if __name__ == '__main__':
    unittest.main()
