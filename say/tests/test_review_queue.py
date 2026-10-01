"""The review queue: which dictations are worth checking by ear."""
from __future__ import annotations

import os
import stat
import unittest
from importlib import import_module
from tempfile import TemporaryDirectory

import review_queue
from fastapi.testclient import TestClient
from server.api import create_app
from server.store import TranscriptStore

LONG = 'please open the settings page and change the model to the larger one'


class RankTests(unittest.TestCase):
    def test_clips_two_models_agree_on_are_not_queued(self):
        self.assertEqual(review_queue.rank([{'id': 1, 'stored': LONG, 'other': LONG}]), [])

    def test_case_and_punctuation_are_not_disagreement(self):
        clips = [{'id': 1, 'stored': LONG, 'other': LONG.capitalize() + '.'}]
        self.assertEqual(review_queue.rank(clips), [])

    def test_more_differing_words_ranks_first(self):
        clips = [
            {'id': 1, 'stored': LONG, 'other': LONG.replace('settings', 'sittings')},
            {'id': 2, 'stored': LONG, 'other': 'something else entirely was said in this clip here now ok'},
        ]
        ranked = review_queue.rank(clips)
        self.assertEqual([e['id'] for e in ranked], [2, 1])
        self.assertEqual([e['rank'] for e in ranked], [1, 2])
        self.assertEqual(ranked[1]['differing_words'], 1)

    def test_a_short_clip_is_not_worth_a_place(self):
        """One differing word of three is a 33% disagreement and tells nothing."""
        self.assertEqual(review_queue.rank([{'id': 1, 'stored': 'yes do that', 'other': 'yes do this'}]), [])

    def test_the_queue_is_bounded(self):
        clips = [
            {'id': i, 'stored': LONG, 'other': LONG.replace('model', f'muddle{i}')}
            for i in range(1, 12)
        ]
        self.assertEqual(len(review_queue.rank(clips, limit=5)), 5)


class FileTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.store = TranscriptStore(os.path.join(self._tmp.name, 't.db'))

    def tearDown(self):
        self.store.close()
        self._tmp.cleanup()

    def test_roundtrip_lives_beside_the_database_and_is_private(self):
        entries = review_queue.rank(
            [{'id': 7, 'stored': LONG, 'other': LONG.replace('settings', 'sittings')}]
        )
        path = review_queue.save(self.store, entries, model='m')
        self.assertEqual(str(path.parent), self._tmp.name)
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertEqual(review_queue.load(self.store)[7]['rank'], 1)

    def test_the_file_holds_no_transcript_text(self):
        entries = review_queue.rank(
            [{'id': 7, 'stored': LONG, 'other': LONG.replace('settings', 'sittings')}]
        )
        path = review_queue.save(self.store, entries)
        raw = path.read_text()
        self.assertNotIn('settings', raw)
        self.assertNotIn('sittings', raw)

    def test_a_missing_or_corrupt_queue_is_just_empty(self):
        self.assertEqual(review_queue.load(self.store), {})
        review_queue.queue_path(self.store).write_text('{not json')
        self.assertEqual(review_queue.load(self.store), {})


class FeedTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.store = TranscriptStore(os.path.join(self._tmp.name, 't.db'))
        self.tid = self.store.insert(
            text=LONG, audio_seconds=5.0, inference_seconds=0.4, model='m',
            inject_method='accessibility', target_app='x',
        )
        self.client = TestClient(create_app(store=self.store, auth_token='secret'))
        self.auth = {'Authorization': 'Bearer secret'}

    def tearDown(self):
        self.store.close()
        self._tmp.cleanup()

    def _feed(self):
        return self.client.get('/transcripts', headers=self.auth).json()[0]

    def test_no_queue_means_nothing_to_review(self):
        self.assertIsNone(self._feed()['review'])

    def test_a_queued_row_says_how_much_is_in_doubt(self):
        review_queue.save(self.store, [
            {'id': self.tid, 'rank': 1, 'differing_words': 3, 'words': 13, 'share': 0.23},
        ])
        self.assertEqual(self._feed()['review'], {'rank': 1, 'differing_words': 3})

    def test_a_row_the_user_has_checked_leaves_the_queue(self):
        review_queue.save(self.store, [
            {'id': self.tid, 'rank': 1, 'differing_words': 3, 'words': 13, 'share': 0.23},
        ])
        self.store.set_manual_correction(self.tid, LONG)
        self.assertIsNone(self._feed()['review'])

    def test_a_corrupt_queue_cannot_break_the_feed(self):
        review_queue.queue_path(self.store).write_text('[1, 2')
        self.assertEqual(self._feed()['text'], LONG)


class ToolPureTests(unittest.TestCase):
    def test_readback_probe_names_the_cause(self):
        probe = import_module('tools.readback_probe')
        text = probe.probe_text(5, 1)
        self.assertEqual(len(text.split()), 5)
        self.assertEqual(probe.classify([(0.0, None), (0.4, None)], text).kind, 'unreadable')
        self.assertEqual(probe.classify([(0.0, f'x {text}')], text).kind, 'immediate')
        late = probe.classify([(0.0, 'x'), (0.4, 'x'), (1.0, f'x {text}')], text)
        self.assertEqual((late.kind, late.seen_at), ('late', 1.0))
        wrapped = text.replace(' ', '\n', 1)
        self.assertEqual(probe.classify([(0.0, wrapped)], text).kind, 'reformatted')
        self.assertEqual(probe.classify([(0.0, 'other'), (2.0, 'other')], text).kind, 'absent')

    def test_a_field_that_becomes_readable_late_is_still_timing(self):
        probe = import_module('tools.readback_probe')
        text = probe.probe_text(40, 2)
        self.assertEqual(probe.classify([(0.0, None), (0.15, text)], text).kind, 'late')

    def test_health_report_counts_by_week(self):
        from datetime import date

        health = import_module('tools.health_report')
        lines = [
            '2026-09-07 10:00:00,000 [ERROR] say.audio: Microphone did not open within 8s — audio device wedged',
            '2026-09-09 10:00:00,000 [ERROR] say.audio: Microphone did not open within 8s — audio device wedged',
            '2026-09-14 10:00:00,000 [WARNING] say.transcriber: Decoding fell below real time: 8s',
            'a continuation line with no date',
        ]
        counts = health.tally(lines)
        self.assertEqual(counts['mic wedged'][date(2026, 9, 7)], 2)
        self.assertEqual(counts['decode slower than speech'][date(2026, 9, 14)], 1)
        table = health.render(counts)
        self.assertIn('09/07', table)
        self.assertIn('mic wedged', table)

    def test_health_report_on_an_empty_log_says_so(self):
        health = import_module('tools.health_report')
        self.assertIn('No dated lines', health.render(health.tally([])))
