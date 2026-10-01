"""The user's own version of a transcript, written from the app.

This is the label source that does not depend on the read-back. Measured on a
real history, the read-back compared 9 of 484 insertions and none of the 307
that ran to twenty words or more; a correction typed into the app works in
every app and at every length.
"""
from __future__ import annotations

import inspect
import os
import unittest
from tempfile import TemporaryDirectory

import corrections
from fastapi.testclient import TestClient
from server.api import create_app
from server.store import TranscriptStore


class _Base(unittest.TestCase):
    TEXT = 'open cloud code and look at the code base'

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.store = TranscriptStore(os.path.join(self._tmp.name, 't.db'))
        self.tid = self.store.insert(
            text=self.TEXT, audio_seconds=4.0, inference_seconds=0.4, model='m',
            inject_method='accessibility', target_app='dev.warp.Warp-Stable',
        )

    def tearDown(self):
        self.store.close()
        self._tmp.cleanup()


class WordErrorTests(unittest.TestCase):
    def test_identical_text_has_no_errors(self):
        self.assertEqual(corrections.word_errors('a b c', 'a b c'), (0, 3))

    def test_case_and_edge_punctuation_are_not_errors(self):
        self.assertEqual(corrections.word_errors('Hello, world.', 'hello world'), (0, 2))

    def test_a_substitution_is_one_error(self):
        self.assertEqual(corrections.word_errors('a wrapper here', 'a rapper here'), (1, 3))

    def test_words_missed_at_the_end_are_errors_not_continuation(self):
        """`diff()` drops a trailing addition as the user typing on. Against a
        deliberate reference the same words are ones we failed to hear."""
        errors, words = corrections.word_errors('ship it today please', 'ship it')
        self.assertEqual((errors, words), (2, 4))
        self.assertTrue(corrections.diff('ship it', 'ship it today please').clean)

    def test_an_empty_reference_does_not_divide_by_zero(self):
        self.assertEqual(corrections.word_errors('', 'something'), (1, 0))


class StoreTests(_Base):
    def test_saving_keeps_what_say_produced_and_stores_the_pair(self):
        row = self.store.set_manual_correction(
            self.tid, 'open Claude Code and look at the codebase'
        )
        self.assertEqual(row['source'], 'manual')
        self.assertEqual(row['inserted'], self.TEXT)
        self.assertEqual(row['corrected'], 'open Claude Code and look at the codebase')
        self.assertEqual(row['target_app'], 'dev.warp.Warp-Stable')
        self.assertEqual(self.store.list()[0].text, self.TEXT)

    def test_saving_again_replaces_the_earlier_version(self):
        self.store.set_manual_correction(self.tid, 'first try')
        self.store.set_manual_correction(self.tid, 'second try')
        self.assertEqual(self.store.manual_corrections(), {self.tid: 'second try'})
        self.assertEqual(len(self.store.corrections()), 1)

    def test_an_unknown_transcript_stores_nothing(self):
        self.assertIsNone(self.store.set_manual_correction(self.tid + 99, 'x'))
        self.assertEqual(self.store.corrections(), [])

    def test_clearing_reports_whether_there_was_one(self):
        self.store.set_manual_correction(self.tid, 'something else')
        self.assertTrue(self.store.clear_manual_correction(self.tid))
        self.assertFalse(self.store.clear_manual_correction(self.tid))
        self.assertEqual(self.store.manual_corrections(), {})

    def test_saving_the_text_unchanged_records_that_it_was_right(self):
        self.store.set_manual_correction(self.tid, self.TEXT)
        review = self.store.review_summary()
        self.assertEqual(review['reviewed'], 1)
        self.assertEqual(review['confirmed_right'], 1)
        self.assertEqual(review['word_rate'], 0.0)

    def test_the_review_rate_counts_changed_words(self):
        self.store.set_manual_correction(
            self.tid, 'open Claude Code and look at the codebase'
        )
        review = self.store.review_summary()
        # 'cloud code' -> 'Claude Code' is one aligned block of two words,
        # 'code base' -> 'codebase' another.
        self.assertEqual(review['edited_words'], 3)
        self.assertEqual(review['words'], 9)

    def test_a_manual_correction_does_not_move_the_read_back_rate(self):
        """Clips are picked for review because they look wrong. That is a
        selected sample, and it must not be reported as the insertion rate."""
        self.store.mark_observed(self.tid)
        self.store.set_manual_correction(self.tid, 'something entirely different')
        s = self.store.correction_summary()
        self.assertEqual(s['insertions'], 1)
        self.assertEqual(s['corrected'], 0)
        self.assertEqual(s['touch_rate'], 0.0)
        self.assertEqual(s['review']['reviewed'], 1)

    def test_a_manual_pair_still_yields_learnable_terms(self):
        row = self.store.set_manual_correction(
            self.tid, 'open Claude Code and look at the code base'
        )
        d = corrections.diff(row['inserted'], row['corrected'])
        # Alignment is case-insensitive, so 'code' == 'Code' and the pair that
        # comes out is the bare word. 'cloud' is a real word: anything that
        # turns pairs into replacement rules has to widen this with its
        # neighbour before offering it.
        self.assertEqual(corrections.term_pairs(d), [('cloud', 'Claude')])


class ApiTests(_Base):
    def setUp(self):
        super().setUp()
        self.app = create_app(store=self.store, auth_token='secret')
        self.client = TestClient(self.app)
        self.auth = {'Authorization': 'Bearer secret'}

    def _post(self, tid, text, headers=None):
        return self.client.post(
            f'/transcripts/{tid}/correction', json={'text': text},
            headers=self.auth if headers is None else headers,
        )

    def test_saving_returns_the_pair_and_the_feed_carries_it(self):
        r = self._post(self.tid, '  open Claude Code  ')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['corrected'], 'open Claude Code')
        feed = self.client.get('/transcripts', headers=self.auth).json()
        self.assertEqual(feed[0]['text'], self.TEXT)
        self.assertEqual(feed[0]['corrected'], 'open Claude Code')

    def test_a_row_nobody_corrected_says_so(self):
        feed = self.client.get('/transcripts', headers=self.auth).json()
        self.assertIsNone(feed[0]['corrected'])

    def test_line_breaks_in_a_correction_survive(self):
        r = self._post(self.tid, 'first line\n\nsecond line')
        self.assertEqual(r.json()['corrected'], 'first line\n\nsecond line')

    def test_an_unknown_transcript_is_404(self):
        self.assertEqual(self._post(self.tid + 99, 'x').status_code, 404)

    def test_empty_text_is_refused_and_nothing_written(self):
        self.assertEqual(self._post(self.tid, '   ').status_code, 400)
        self.assertEqual(self.store.corrections(), [])

    def test_an_oversized_body_is_refused(self):
        self.assertEqual(self._post(self.tid, 'x' * 100_001).status_code, 400)

    def test_it_needs_the_token(self):
        self.assertEqual(self._post(self.tid, 'x', headers={}).status_code, 401)
        r = self.client.delete(f'/transcripts/{self.tid}/correction')
        self.assertEqual(r.status_code, 401)

    def test_deleting_undoes_it(self):
        self._post(self.tid, 'open Claude Code')
        r = self.client.delete(f'/transcripts/{self.tid}/correction', headers=self.auth)
        self.assertEqual(r.json(), {'deleted': True})
        feed = self.client.get('/transcripts', headers=self.auth).json()
        self.assertIsNone(feed[0]['corrected'])

    def test_the_summary_reports_reviews_apart_from_the_rate(self):
        self._post(self.tid, 'open Claude Code and look at the codebase')
        s = self.client.get('/corrections', headers=self.auth).json()['summary']
        self.assertEqual(s['review']['reviewed'], 1)
        self.assertEqual(s['corrected'], 0)

    def test_the_routes_stay_off_the_event_loop(self):
        """Aligning a long transcript is CPU-bound; as a coroutine it would
        stall /health and the event stream for as long as it ran."""
        for route in self.app.routes:
            if getattr(route, 'path', '') == '/transcripts/{transcript_id}/correction':
                self.assertFalse(inspect.iscoroutinefunction(route.endpoint))
