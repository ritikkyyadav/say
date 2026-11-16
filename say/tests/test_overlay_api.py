"""The overlay-appearance and vocabulary endpoints.

Both are new surfaces the desktop UI depends on completely: the Home screen is
built out of `/stats/vocabulary`, and the overlay customiser cannot save
anything without `/overlay`. The tests that matter here are the ones that stop
a bad request from persisting — a colour that is not a colour, or a theme id
that does not exist — because those settings are read at draw time by code
that has no way to complain.
"""

from __future__ import annotations

import os
import unittest
from datetime import UTC, datetime, timedelta
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient
from server.api import create_app
from server.store import TranscriptStore


def _ts(minutes_ago: int = 0) -> str:
    when = datetime.now(UTC) - timedelta(minutes=minutes_ago)
    return when.isoformat(timespec='seconds').replace('+00:00', 'Z')


class _Base(unittest.TestCase):
    def setUp(self):
        import config
        import settings

        self._tmp = TemporaryDirectory()
        self.store = TranscriptStore(os.path.join(self._tmp.name, 't.db'))

        # Never touch the real ~/Library settings.json.
        self._settings_file = settings.SETTINGS_FILE
        self._settings_dir = settings.SETTINGS_DIR
        settings.SETTINGS_DIR = self._tmp.name
        settings.SETTINGS_FILE = os.path.join(self._tmp.name, 'settings.json')
        self._cfg = {
            k: getattr(config, k)
            for k in ('OVERLAY_THEME', 'OVERLAY_CUSTOM_COLORS', 'SHOW_OVERLAY',
                      'OVERLAY_CONTRAST', 'SAVE_RECORDINGS')
        }

        self.app = create_app(store=self.store, auth_token=None)
        self.client = TestClient(self.app)

    def tearDown(self):
        import config
        import overlay_theme
        import settings

        settings.SETTINGS_FILE = self._settings_file
        settings.SETTINGS_DIR = self._settings_dir
        for k, v in self._cfg.items():
            setattr(config, k, v)
        overlay_theme.register_overlay(None)
        self.client.close()
        self.store.close()
        self._tmp.cleanup()

    def _seed(self, n: int = 60) -> None:
        for i in range(n):
            self.store.insert(
                text=('so like I want you to just go ahead and rebuild the whole '
                      'front end because like the transcripts follow the insights.'),
                audio_seconds=12.0,
                inference_seconds=1.0,
                model='m',
                inject_method='accessibility',
                target_app='dev.warp.Warp-Stable',
                ts_utc=_ts(i * 7),
            )


class OverlayEndpoint(_Base):
    def test_get_returns_the_catalogue_and_the_current_theme(self):
        body = self.client.get('/overlay').json()
        self.assertEqual(body['theme'], 'ink')
        self.assertGreater(len(body['presets']), 3)
        self.assertEqual(body['presets'][0]['id'], 'ink')
        self.assertTrue(body['presets'][0]['adaptive'])
        self.assertGreaterEqual(body['max_colors'], 2)

    def test_setting_a_preset_persists_it(self):
        import config

        r = self.client.post('/overlay', json={'theme': 'signal'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['theme'], 'signal')
        self.assertEqual(config.OVERLAY_THEME, 'signal')

    def test_an_unknown_theme_is_refused_and_nothing_is_written(self):
        import config

        before = config.OVERLAY_THEME
        r = self.client.post('/overlay', json={'theme': 'chartreuse'})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(config.OVERLAY_THEME, before)

    def test_a_bad_colour_is_refused_and_nothing_is_written(self):
        import config

        before = list(getattr(config, 'OVERLAY_CUSTOM_COLORS', []))
        r = self.client.post('/overlay', json={'colors': ['#FF0000', 'banana']})
        self.assertEqual(r.status_code, 400)
        self.assertIn('hex', r.json()['detail'])
        self.assertEqual(list(config.OVERLAY_CUSTOM_COLORS), before)

    def test_colours_are_stored_canonically(self):
        r = self.client.post('/overlay', json={'theme': 'custom', 'colors': ['#0f0', 'ff0000']})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['colors'], ['#00FF00', '#FF0000'])

    def test_custom_without_colours_is_refused(self):
        """Otherwise the glyph silently falls back and the choice looks ignored."""
        r = self.client.post('/overlay', json={'theme': 'custom'})
        self.assertEqual(r.status_code, 400)

    def test_the_show_switch_round_trips(self):
        self.assertFalse(self.client.post('/overlay', json={'show_overlay': False}).json()['show_overlay'])
        self.assertTrue(self.client.post('/overlay', json={'show_overlay': True}).json()['show_overlay'])

    def test_saving_succeeds_even_though_no_glyph_is_running(self):
        """The settings are already on disk by the time the overlay is told.

        A headless process (this test, a CI run, the menu-bar app before its
        run loop exists) has no live overlay, and that must not turn a
        successful save into a 500.
        """
        r = self.client.post('/overlay', json={'theme': 'ember', 'preview': True})
        self.assertEqual(r.status_code, 200)

    def test_a_live_overlay_is_repainted_and_previewed(self):
        import overlay_theme

        calls: list[str] = []

        class FakeOverlay:
            def set_theme(self):
                calls.append('set_theme')

            def preview(self):
                calls.append('preview')

        overlay_theme.register_overlay(FakeOverlay())
        self.client.post('/overlay', json={'theme': 'mono', 'preview': True})
        self.assertEqual(calls, ['set_theme', 'preview'])

    def test_a_failing_overlay_cannot_fail_the_request(self):
        import overlay_theme

        class Exploding:
            def set_theme(self):
                raise RuntimeError('window server is gone')

        overlay_theme.register_overlay(Exploding())
        r = self.client.post('/overlay', json={'theme': 'tide'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['theme'], 'tide')

    def test_preview_reports_when_there_is_nothing_to_show(self):
        body = self.client.post('/overlay/preview').json()
        self.assertFalse(body['shown'])


class VocabularyEndpoint(_Base):
    def test_a_thin_corpus_reports_not_enough_data(self):
        self.store.insert(
            text='hello there', audio_seconds=2.0, inference_seconds=0.3,
            model='m', inject_method='accessibility', target_app=None, ts_utc=_ts(),
        )
        body = self.client.get('/stats/vocabulary').json()
        self.assertFalse(body['enough_data'])
        self.assertEqual(body['insights'], [])

    def test_a_real_corpus_produces_insights(self):
        self._seed()
        body = self.client.get('/stats/vocabulary').json()
        self.assertTrue(body['enough_data'])
        self.assertTrue(body['insights'])
        self.assertEqual(body['insights'][0]['id'], 'crutch')
        self.assertIn('like', [w['word'] for w in body['top_words']])
        self.assertEqual(len(body['hours']), 24)
        self.assertEqual(len(body['weekdays']), 7)

    def test_every_insight_is_renderable(self):
        self._seed()
        for insight in self.client.get('/stats/vocabulary').json()['insights']:
            self.assertIn(insight['viz']['kind'], {'dots', 'bars'})
            self.assertTrue(insight['headline'])
            self.assertTrue(insight['body'])


class TranscriptAudioFlag(_Base):
    def test_rows_without_audio_report_false(self):
        self._seed(6)
        rows = self.client.get('/transcripts?limit=10').json()
        self.assertTrue(rows)
        self.assertTrue(all(r['has_audio'] is False for r in rows))

    def test_a_recorded_path_whose_file_is_gone_reports_false(self):
        """A play button that 404s is worse than no play button.

        Retention sweeps and manual deletes remove the WAV and leave the
        column set, so the column alone cannot be trusted.
        """
        self._seed(6)
        row_id = self.client.get('/transcripts?limit=1').json()[0]['id']
        self.store.set_audio_path(row_id, os.path.join(self._tmp.name, 'gone.wav'))
        rows = self.client.get('/transcripts?limit=1').json()
        self.assertFalse(rows[0]['has_audio'])

    def test_a_real_file_reports_true(self):
        self._seed(6)
        row_id = self.client.get('/transcripts?limit=1').json()[0]['id']
        path = os.path.join(self._tmp.name, 'clip.wav')
        with open(path, 'wb') as fh:
            fh.write(b'RIFF')
        self.store.set_audio_path(row_id, path)
        rows = self.client.get('/transcripts?limit=1').json()
        self.assertTrue(rows[0]['has_audio'])


if __name__ == '__main__':
    unittest.main()
