"""Tests for say/server/api.py — FastAPI surface."""
from __future__ import annotations

import os
import unittest
from datetime import UTC, datetime
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient
from server.api import create_app
from server.store import TranscriptStore


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec='seconds').replace('+00:00', 'Z')


class ApiTests(unittest.TestCase):
    def setUp(self):
        import config
        import settings

        self._tmp = TemporaryDirectory()
        self.store = TranscriptStore(os.path.join(self._tmp.name, 't.db'))
        self.store.insert(
            text='hello there',
            audio_seconds=2.0,
            inference_seconds=0.3,
            model='m',
            inject_method='accessibility',
            target_app='com.apple.TextEdit',
            ts_utc=_now(),
        )
        # Isolate settings + config knobs so recording-config tests never
        # touch the user's real ~/Library settings.json.
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
        # Hermetic Moonshine cache so model state never reads the dev machine.
        self._prev_moon = os.environ.get('SAY_MOONSHINE_CACHE')
        os.environ['SAY_MOONSHINE_CACHE'] = os.path.join(self._tmp.name, 'moon')
        self.app = create_app(store=self.store, auth_token='secret')
        self.client = TestClient(self.app)

    def tearDown(self):
        import config
        import settings

        settings.SETTINGS_FILE = self._settings_file
        settings.SETTINGS_DIR = self._settings_dir
        for k, v in self._cfg.items():
            setattr(config, k, v)
        if self._prev_moon is None:
            os.environ.pop('SAY_MOONSHINE_CACHE', None)
        else:
            os.environ['SAY_MOONSHINE_CACHE'] = self._prev_moon
        self.client.close()
        self.store.close()
        self._tmp.cleanup()

    def test_health_does_not_require_auth(self):
        r = self.client.get('/health')
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body['ok'])
        self.assertIn('state', body)

    def test_transcripts_requires_auth(self):
        r = self.client.get('/transcripts')
        self.assertEqual(r.status_code, 401)

    def test_transcripts_returns_inserted_row(self):
        r = self.client.get('/transcripts', headers={'Authorization': 'Bearer secret'})
        self.assertEqual(r.status_code, 200)
        rows = r.json()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['text'], 'hello there')
        self.assertEqual(rows[0]['target_app'], 'com.apple.TextEdit')

    def test_summary_endpoint(self):
        r = self.client.get('/stats/summary?token=secret')
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIn('total_words', body)
        self.assertGreaterEqual(body['total_words'], 2)
        self.assertEqual(body['fixes_count'], 0)

    def test_user_profile_returns_first_name(self):
        r = self.client.get('/user/profile?token=secret')
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIn('first_name', body)
        self.assertTrue(isinstance(body['first_name'], str))
        self.assertGreater(len(body['first_name']), 0)

    def test_copy_endpoint_returns_204(self):
        r = self.client.post('/transcripts/1/copy?token=secret')
        self.assertEqual(r.status_code, 204)

    def test_desktop_usage_and_heatmap(self):
        r1 = self.client.get('/stats/desktop_usage?days=30&token=secret')
        self.assertEqual(r1.status_code, 200)
        usage = r1.json()
        self.assertEqual(usage[0]['target_app'], 'com.apple.TextEdit')

        r2 = self.client.get('/stats/heatmap?days=14&token=secret')
        self.assertEqual(r2.status_code, 200)
        grid = r2.json()
        self.assertEqual(len(grid), 14)

    # ── Model management endpoints ───────────────────────────────────────

    def test_models_requires_auth(self):
        r = self.client.get('/models')
        self.assertEqual(r.status_code, 401)

    def test_models_lists_catalog_with_active(self):
        r = self.client.get('/models?token=secret')
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIn('active', body)
        self.assertIn('cache_bytes', body)
        self.assertGreaterEqual(len(body['models']), 4)
        actives = [m for m in body['models'] if m['active']]
        self.assertEqual(len(actives), 1)
        self.assertEqual(actives[0]['id'], body['active'])

    def test_set_active_rejects_unknown_model(self):
        r = self.client.post(
            '/models/active',
            json={'id': 'no-such/model'},
            headers={'Authorization': 'Bearer secret'},
        )
        self.assertEqual(r.status_code, 400)

    def test_download_rejects_unknown_model(self):
        r = self.client.post('/models/no-such/model/download?token=secret')
        self.assertEqual(r.status_code, 404)

    def test_delete_active_model_is_refused(self):
        active = self.client.get('/models?token=secret').json()['active']
        r = self.client.delete(
            f'/models/{active}',
            headers={'Authorization': 'Bearer secret'},
        )
        self.assertEqual(r.status_code, 409)

    def test_cancel_download_is_noop_when_idle(self):
        r = self.client.post('/models/mlx-community/whisper-tiny-mlx/cancel?token=secret')
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()['cancelled'])

    def test_download_insufficient_storage_returns_507(self):
        import model_manager

        # Empty temp cache so the target model reads as not-downloaded, and a
        # stubbed free-space probe so the disk pre-check trips.
        with TemporaryDirectory() as cache:
            prev = os.environ.get('HF_HUB_CACHE')
            orig_free = model_manager._free_bytes_for_cache
            os.environ['HF_HUB_CACHE'] = cache
            model_manager._free_bytes_for_cache = lambda: 1000
            try:
                r = self.client.post(
                    '/models/mlx-community/whisper-tiny-mlx/download?token=secret'
                )
            finally:
                model_manager._free_bytes_for_cache = orig_free
                if prev is None:
                    os.environ.pop('HF_HUB_CACHE', None)
                else:
                    os.environ['HF_HUB_CACHE'] = prev
        self.assertEqual(r.status_code, 507)

    # ── Recordings endpoints ─────────────────────────────────────────────

    def test_recordings_requires_auth(self):
        self.assertEqual(self.client.get('/recordings').status_code, 401)

    def test_recordings_config_defaults_and_update(self):
        cfg = self.client.get('/recordings/config?token=secret').json()
        self.assertIn('save_recordings', cfg)
        self.assertIn('retention_mode', cfg)
        self.assertIn('total_bytes', cfg)
        r = self.client.post(
            '/recordings/config',
            json={'save_recordings': True, 'retention_mode': 'keep_all'},
            headers={'Authorization': 'Bearer secret'},
        )
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()['save_recordings'])

    def test_recordings_config_rejects_bad_mode(self):
        r = self.client.post(
            '/recordings/config',
            json={'retention_mode': 'nope'},
            headers={'Authorization': 'Bearer secret'},
        )
        self.assertEqual(r.status_code, 400)

    def test_recording_audio_roundtrip_and_delete(self):
        import numpy as np
        import recordings

        tid = self.store.insert(
            text='clip', audio_seconds=1.0, inference_seconds=0.1, model='m',
            inject_method='accessibility', target_app=None, ts_utc=_now(),
        )
        recordings.save_for_transcript(self.store, tid, np.zeros(16000, dtype=np.float32))

        listed = self.client.get('/recordings?token=secret').json()
        self.assertTrue(any(x['id'] == tid for x in listed))

        ra = self.client.get(f'/recordings/{tid}/audio?token=secret')
        self.assertEqual(ra.status_code, 200)
        self.assertTrue(ra.headers['content-type'].startswith('audio/wav'))
        self.assertTrue(ra.content.startswith(b'RIFF'))

        rd = self.client.delete(
            f'/recordings/{tid}', headers={'Authorization': 'Bearer secret'}
        )
        self.assertEqual(rd.status_code, 200)
        self.assertTrue(rd.json()['deleted'])
        self.assertEqual(
            self.client.get(f'/recordings/{tid}/audio?token=secret').status_code, 404
        )

    def test_recordings_export_empty(self):
        r = self.client.post('/recordings/export?token=secret')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['count'], 0)


if __name__ == '__main__':
    unittest.main()
