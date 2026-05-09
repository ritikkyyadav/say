"""Tests for say/server/store.py — SQLite transcript persistence."""
from __future__ import annotations

import os
import unittest
from datetime import UTC, date, datetime, timedelta
from tempfile import TemporaryDirectory

from server.store import TranscriptStore, _word_count


def _ts(d: date, hour: int = 12) -> str:
    return datetime(d.year, d.month, d.day, hour, tzinfo=UTC).isoformat(timespec='seconds').replace('+00:00', 'Z')


class StoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.path = os.path.join(self._tmp.name, 'transcripts.db')
        self.store = TranscriptStore(self.path)

    def tearDown(self):
        self.store.close()
        self._tmp.cleanup()

    def test_insert_and_list_returns_row(self):
        rid = self.store.insert(
            text='hello world',
            audio_seconds=1.5,
            inference_seconds=0.4,
            model='mlx-community/whisper-large-v3-turbo',
            inject_method='accessibility',
            target_app='com.apple.TextEdit',
            success=True,
        )
        self.assertGreater(rid, 0)

        rows = self.store.list(limit=10)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.text, 'hello world')
        self.assertEqual(row.word_count, 2)
        self.assertEqual(row.target_app, 'com.apple.TextEdit')
        self.assertEqual(row.inject_method, 'accessibility')
        self.assertTrue(row.success)
        self.assertEqual(self.store.count(), 1)

    def test_list_is_reverse_chronological_and_paginates(self):
        today = date(2026, 5, 8)
        for i in range(5):
            self.store.insert(
                text=f'transcript {i}',
                audio_seconds=1.0,
                inference_seconds=0.2,
                model='m',
                inject_method='accessibility',
                target_app=None,
                ts_utc=_ts(today, hour=10 + i),
            )
        rows = self.store.list(limit=3)
        self.assertEqual([r.text for r in rows], ['transcript 4', 'transcript 3', 'transcript 2'])

        # before-cursor pagination
        next_page = self.store.list(limit=3, before=rows[-1].ts_utc)
        self.assertEqual([r.text for r in next_page], ['transcript 1', 'transcript 0'])

    def test_summary_counts_and_wpm(self):
        today = date(2026, 5, 8)
        # 30-day window: 100 words across 60 seconds of audio → 100 wpm.
        self.store.insert(
            text=' '.join(['word'] * 60),
            audio_seconds=30.0,
            inference_seconds=1.0,
            model='m',
            inject_method='accessibility',
            target_app='com.apple.TextEdit',
            ts_utc=_ts(today - timedelta(days=2)),
        )
        self.store.insert(
            text=' '.join(['word'] * 40),
            audio_seconds=30.0,
            inference_seconds=1.0,
            model='m',
            inject_method='accessibility',
            target_app='com.apple.TextEdit',
            ts_utc=_ts(today - timedelta(days=10)),
        )
        # Outside the 30-day window — counts toward total_words but not wpm_30d.
        self.store.insert(
            text=' '.join(['word'] * 50),
            audio_seconds=30.0,
            inference_seconds=1.0,
            model='m',
            inject_method='accessibility',
            target_app=None,
            ts_utc=_ts(today - timedelta(days=45)),
        )

        s = self.store.summary(today=today)
        self.assertEqual(s['total_words'], 150)
        self.assertEqual(s['words_this_month'], 100)
        self.assertEqual(s['words_prev_month'], 50)
        self.assertAlmostEqual(s['wpm_30d'], 100.0)
        self.assertEqual(s['fixes_count'], 0)
        self.assertEqual(s['transcript_count'], 3)

    def test_streak_includes_today_and_breaks_on_gap(self):
        today = date(2026, 5, 8)
        # consecutive days: today, today-1, today-2, then a gap at today-3
        for offset in (0, 1, 2):
            self.store.insert(
                text='hi',
                audio_seconds=1.0,
                inference_seconds=0.1,
                model='m',
                inject_method='accessibility',
                target_app=None,
                ts_utc=_ts(today - timedelta(days=offset)),
            )
        # earlier disconnected entry
        self.store.insert(
            text='hi',
            audio_seconds=1.0,
            inference_seconds=0.1,
            model='m',
            inject_method='accessibility',
            target_app=None,
            ts_utc=_ts(today - timedelta(days=10)),
        )
        s = self.store.summary(today=today)
        self.assertEqual(s['streak_days'], 3)

    def test_heatmap_includes_zeros_and_real_counts(self):
        today = date(2026, 5, 8)
        self.store.insert(
            text='a b c',
            audio_seconds=1.0,
            inference_seconds=0.1,
            model='m',
            inject_method='accessibility',
            target_app=None,
            ts_utc=_ts(today - timedelta(days=2)),
        )
        self.store.insert(
            text='d e',
            audio_seconds=1.0,
            inference_seconds=0.1,
            model='m',
            inject_method='accessibility',
            target_app=None,
            ts_utc=_ts(today - timedelta(days=2), hour=15),
        )
        out = self.store.heatmap(days=7, today=today)
        self.assertEqual(len(out), 7)
        # First entry is start date (today - 6); last entry is today.
        self.assertEqual(out[0]['date'], (today - timedelta(days=6)).isoformat())
        self.assertEqual(out[-1]['date'], today.isoformat())

        by_date = {r['date']: r['count'] for r in out}
        self.assertEqual(by_date[(today - timedelta(days=2)).isoformat()], 2)
        self.assertEqual(by_date[today.isoformat()], 0)

    def test_desktop_usage_groups_by_target_app(self):
        today = date(2026, 5, 8)
        for app in ('com.apple.Safari', 'com.apple.Safari', 'com.apple.TextEdit', None):
            self.store.insert(
                text='hello',
                audio_seconds=1.0,
                inference_seconds=0.1,
                model='m',
                inject_method='accessibility',
                target_app=app,
                ts_utc=_ts(today - timedelta(days=1)),
            )
        usage = self.store.desktop_usage(days=30, today=today)
        by_app = {row['target_app']: row for row in usage}
        self.assertEqual(by_app['com.apple.Safari']['count'], 2)
        self.assertEqual(by_app['com.apple.TextEdit']['count'], 1)
        self.assertEqual(by_app['Unknown']['count'], 1)
        # Shares should sum to ~1.0
        self.assertAlmostEqual(sum(r['share'] for r in usage), 1.0, places=2)

    def test_word_count_handles_whitespace(self):
        self.assertEqual(_word_count(''), 0)
        self.assertEqual(_word_count('  '), 0)
        self.assertEqual(_word_count('hello   world\nyay'), 3)


if __name__ == '__main__':
    unittest.main()
