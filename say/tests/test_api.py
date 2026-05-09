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
        self.app = create_app(store=self.store, auth_token='secret')
        self.client = TestClient(self.app)

    def tearDown(self):
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


if __name__ == '__main__':
    unittest.main()
