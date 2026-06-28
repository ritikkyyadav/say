"""Tests for say/recordings.py — on-disk audio receipts."""
from __future__ import annotations

import os
import unittest
import wave
import zipfile
from datetime import UTC, datetime, timedelta
from tempfile import TemporaryDirectory

import config
import numpy as np
import recordings
import settings
from server.store import TranscriptStore


def _sine(seconds: float, rate: int = 16000) -> np.ndarray:
    t = np.linspace(0, seconds, int(seconds * rate), endpoint=False, dtype=np.float32)
    return (0.2 * np.sin(2 * np.pi * 220.0 * t)).astype(np.float32)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec='seconds').replace('+00:00', 'Z')


class RecordingsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.store = TranscriptStore(os.path.join(self._tmp.name, 't.db'))
        # Isolate settings so apply_recordings_config never touches ~/Library.
        self._settings_file = settings.SETTINGS_FILE
        self._settings_dir = settings.SETTINGS_DIR
        settings.SETTINGS_DIR = self._tmp.name
        settings.SETTINGS_FILE = os.path.join(self._tmp.name, 'settings.json')
        self._cfg = {
            k: getattr(config, k)
            for k in (
                'SAVE_RECORDINGS', 'RECORDINGS_RETENTION_MODE',
                'RECORDINGS_RETENTION_DAYS', 'RECORDINGS_MAX_BYTES',
            )
        }

    def tearDown(self):
        settings.SETTINGS_FILE = self._settings_file
        settings.SETTINGS_DIR = self._settings_dir
        for k, v in self._cfg.items():
            setattr(config, k, v)
        self.store.close()
        self._tmp.cleanup()

    def _add(self, text='hello', seconds=1.0, ts=None):
        tid = self.store.insert(
            text=text, audio_seconds=seconds, inference_seconds=0.1,
            model='m', inject_method='accessibility',
            target_app='com.apple.TextEdit', ts_utc=ts,
        )
        path = recordings.save_for_transcript(self.store, tid, _sine(seconds))
        return tid, path

    def test_save_writes_valid_wav_and_links_row(self):
        tid, path = self._add(seconds=1.0)
        self.assertIsNotNone(path)
        self.assertTrue(os.path.exists(path))
        with wave.open(path, 'rb') as w:
            self.assertEqual(w.getnchannels(), 1)
            self.assertEqual(w.getsampwidth(), 2)
            self.assertEqual(w.getframerate(), 16000)
            self.assertGreater(w.getnframes(), 0)
        self.assertEqual(self.store.audio_path_for(tid), path)

    def test_list_and_totals(self):
        self._add()
        self._add()
        rows = recordings.list_recordings(self.store)
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r['has_audio'] for r in rows))
        self.assertEqual(recordings.count(self.store), 2)
        self.assertGreater(recordings.total_bytes(self.store), 0)

    def test_read_bytes_and_missing(self):
        tid, _ = self._add()
        data = recordings.read_bytes(self.store, tid)
        self.assertIsInstance(data, bytes)
        self.assertTrue(data.startswith(b'RIFF'))
        self.assertIsNone(recordings.read_bytes(self.store, 99999))

    def test_delete_one(self):
        tid, path = self._add()
        self.assertTrue(recordings.delete_recording(self.store, tid))
        self.assertFalse(os.path.exists(path))
        self.assertIsNone(self.store.audio_path_for(tid))
        self.assertEqual(recordings.count(self.store), 0)

    def test_delete_all(self):
        self._add()
        self._add()
        removed = recordings.delete_all(self.store)
        self.assertEqual(removed, 2)
        self.assertEqual(recordings.count(self.store), 0)
        self.assertEqual(recordings.list_recordings(self.store), [])

    def test_retention_days_prunes_old(self):
        old_id, _ = self._add(ts=_iso(datetime.now(UTC) - timedelta(days=40)))
        new_id, _ = self._add(ts=_iso(datetime.now(UTC)))
        config.RECORDINGS_RETENTION_MODE = 'days'
        config.RECORDINGS_RETENTION_DAYS = 30
        pruned = recordings.enforce_retention(self.store)
        self.assertEqual(pruned, 1)
        ids = {r['id'] for r in recordings.list_recordings(self.store)}
        self.assertEqual(ids, {new_id})

    def test_retention_size_keeps_within_budget(self):
        ids = []
        base = datetime.now(UTC)
        for i in range(3):
            tid, _ = self._add(seconds=1.0, ts=_iso(base + timedelta(seconds=i)))
            ids.append(tid)
        one = recordings.file_size(self.store, ids[-1])
        config.RECORDINGS_RETENTION_MODE = 'size'
        config.RECORDINGS_MAX_BYTES = int(one * 1.5)  # room for ~1 clip
        pruned = recordings.enforce_retention(self.store)
        self.assertGreaterEqual(pruned, 1)
        self.assertLessEqual(recordings.total_bytes(self.store), config.RECORDINGS_MAX_BYTES)

    def test_export_zip_contains_manifest_and_clips(self):
        self._add(text='one')
        self._add(text='two')
        result = recordings.export_zip(self.store)
        self.assertEqual(result['count'], 2)
        self.assertTrue(os.path.exists(result['path']))
        with zipfile.ZipFile(result['path']) as zf:
            names = zf.namelist()
            self.assertIn('manifest.csv', names)
            self.assertIn('manifest.json', names)
            clips = [n for n in names if n.startswith('clips/') and n.endswith('.wav')]
            self.assertEqual(len(clips), 2)

    def test_export_empty_returns_none(self):
        result = recordings.export_zip(self.store)
        self.assertEqual(result['count'], 0)
        self.assertIsNone(result['path'])

    def test_apply_config_persists_and_prunes(self):
        self._add(ts=_iso(datetime.now(UTC) - timedelta(days=40)))
        self._add(ts=_iso(datetime.now(UTC)))
        cfg = recordings.apply_recordings_config(
            self.store, save_recordings=True, retention_mode='days', retention_days=30,
        )
        self.assertTrue(cfg['save_recordings'])
        self.assertEqual(cfg['retention_mode'], 'days')
        self.assertEqual(cfg['count'], 1)  # the 40-day-old clip was pruned
        self.assertTrue(config.SAVE_RECORDINGS)

    def test_apply_config_rejects_bad_mode(self):
        with self.assertRaises(ValueError):
            recordings.apply_recordings_config(self.store, retention_mode='bogus')


if __name__ == '__main__':
    unittest.main()
