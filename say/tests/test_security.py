"""Regressions for the findings of the pre-ship security and reliability audit.

Each test here corresponds to something that was actually wrong, or to a
guarantee the product makes out loud. They are deliberately blunt — several
assert on the *shape* of the code (is this endpoint a coroutine? does this
subprocess call interpolate?) rather than on behaviour, because the failure
modes are invisible at runtime: a blocked event loop looks like a slow app,
and a broken AppleScript literal looks like notifications being turned off.
"""

from __future__ import annotations

import asyncio
import inspect
import os
import subprocess
import sys
import unittest
from tempfile import TemporaryDirectory

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


class TokenComparison(unittest.TestCase):
    def test_auth_uses_a_constant_time_comparison(self):
        """A plain `!=` leaks the token prefix through timing."""
        import server.api as api

        src = inspect.getsource(api._check_auth)
        self.assertIn('compare_digest', src)
        self.assertNotIn('presented != token', src)

    def test_a_wrong_token_is_rejected(self):
        from fastapi.testclient import TestClient
        from server.api import create_app
        from server.store import TranscriptStore

        with TemporaryDirectory() as tmp:
            store = TranscriptStore(os.path.join(tmp, 't.db'))
            client = TestClient(create_app(store=store, auth_token='right'))
            self.assertEqual(client.get('/transcripts').status_code, 401)
            self.assertEqual(
                client.get('/transcripts', headers={'Authorization': 'Bearer wrong'}).status_code,
                401,
            )
            self.assertEqual(
                client.get('/transcripts', headers={'Authorization': 'Bearer right'}).status_code,
                200,
            )
            # SSE cannot set headers, so the query parameter is supported too.
            self.assertEqual(client.get('/transcripts?token=wrong').status_code, 401)
            store.close()

    def test_the_set_of_unauthenticated_routes_is_exactly_these_two(self):
        """Pinned, so a new open route has to be a decision rather than a default.

        `/health` has to be open — the shell probes it before it has read the
        token. `/listener` is open on purpose too: the UI polls it to decide
        whether to show the permissions banner, and it returns nothing but
        permission booleans. Everything else must be behind the token, and
        FastAPI's own /docs, /redoc and /openapi.json are switched off.
        """
        from fastapi.testclient import TestClient
        from server.api import create_app
        from server.store import TranscriptStore

        with TemporaryDirectory() as tmp:
            store = TranscriptStore(os.path.join(tmp, 't.db'))
            app = create_app(store=store, auth_token='secret')
            client = TestClient(app)
            open_routes = []
            for route in app.routes:
                path = getattr(route, 'path', '')
                if not path.startswith('/') or '{' in path or path == '/events':
                    continue
                if 'GET' not in getattr(route, 'methods', set()):
                    continue
                if client.get(path).status_code != 401:
                    open_routes.append(path)
            self.assertEqual(sorted(open_routes), ['/health', '/listener'])
            store.close()


class EventLoopBlocking(unittest.TestCase):
    def test_the_vocabulary_endpoint_is_not_a_coroutine(self):
        """The audit's reliability finding.

        `/stats/vocabulary` is the only CPU-bound route in the app: 220 ms over
        a real corpus, 12.7 s at the limit it accepts. Declared `async def` it
        runs *on the event loop*, so for that whole time nothing else is
        served — not `/health`, not the SSE stream the UI reads its recording
        state from. Declared `def`, Starlette hands it to a threadpool.
        """
        from server.api import create_app
        from server.store import TranscriptStore

        with TemporaryDirectory() as tmp:
            store = TranscriptStore(os.path.join(tmp, 't.db'))
            app = create_app(store=store, auth_token=None)
            route = next(r for r in app.routes
                         if getattr(r, 'path', '') == '/stats/vocabulary')
            self.assertFalse(
                asyncio.iscoroutinefunction(route.endpoint),
                'stats_vocabulary must stay sync so it is threadpooled',
            )
            store.close()

    def test_repeating_the_analysis_is_memoised(self):
        from fastapi.testclient import TestClient
        from server.api import create_app
        from server.store import TranscriptStore

        with TemporaryDirectory() as tmp:
            store = TranscriptStore(os.path.join(tmp, 't.db'))
            for _ in range(8):
                store.insert(text='so like I want you to just go ahead and rebuild it',
                             audio_seconds=4.0, inference_seconds=0.4, model='m',
                             inject_method='accessibility', target_app=None)
            client = TestClient(create_app(store=store, auth_token=None))
            first = client.get('/stats/vocabulary').json()
            second = client.get('/stats/vocabulary').json()
            self.assertEqual(first, second)
            self.assertIsNotNone(getattr(client.app.state, 'vocab_cache', None))
            store.close()

    def test_a_new_dictation_invalidates_the_cache(self):
        """Otherwise Home would show yesterday's numbers forever."""
        from fastapi.testclient import TestClient
        from server.api import create_app
        from server.store import TranscriptStore

        with TemporaryDirectory() as tmp:
            store = TranscriptStore(os.path.join(tmp, 't.db'))
            for _ in range(8):
                store.insert(text='one two three four five six seven eight',
                             audio_seconds=3.0, inference_seconds=0.3, model='m',
                             inject_method='accessibility', target_app=None)
            client = TestClient(create_app(store=store, auth_token=None))
            before = client.get('/stats/vocabulary').json()['total_words']
            store.insert(text='a completely different sentence appears here now',
                         audio_seconds=3.0, inference_seconds=0.3, model='m',
                         inject_method='accessibility', target_app=None)
            after = client.get('/stats/vocabulary').json()['total_words']
            self.assertGreater(after, before)
            store.close()


class NotificationSafety(unittest.TestCase):
    """`notify()` renders a transcript — the most externally-influenced string
    in the app if anyone ever dictates something they were sent."""

    def setUp(self):
        import config
        import notifications

        self.mod = notifications
        self.calls: list[list[str]] = []
        self._real_run = subprocess.run
        notifications.subprocess.run = lambda argv, **kw: self.calls.append(list(argv))
        # The switch lives on `config` and is read at call time (B11).
        self._show = config.SHOW_NOTIFICATIONS
        config.SHOW_NOTIFICATIONS = True

    def tearDown(self):
        import config

        self.mod.subprocess.run = self._real_run
        config.SHOW_NOTIFICATIONS = self._show

    def test_the_text_is_an_argument_never_part_of_the_script(self):
        self.mod.notify('Say', 'hello "there" and a \\ backslash')
        argv = self.calls[0]
        script = ' '.join(a for a, prev in zip(argv[1:], argv, strict=False) if prev == '-e')
        self.assertNotIn('hello', script, 'transcript text leaked into the script body')
        self.assertIn('hello "there" and a \\ backslash', argv)

    def test_a_multiline_transcript_still_notifies(self):
        """The bug this replaced.

        An AppleScript string literal cannot contain a raw newline, so the
        old string-building version produced a syntax error and showed
        nothing — silently, because osascript fails with a non-zero exit
        rather than an exception.
        """
        self.mod.notify('Say', 'first line\nsecond line\n\nthird')
        text = self.calls[0][-2]
        self.assertNotIn('\n', text)
        self.assertIn('first line second line third', text)

    def test_long_text_is_truncated(self):
        self.mod.notify('Say', 'x ' * 400)
        self.assertLessEqual(len(self.calls[0][-2]), 82)

    def test_notifications_can_be_switched_off(self):
        import config

        config.SHOW_NOTIFICATIONS = False
        self.mod.notify('Say', 'anything')
        self.assertEqual(self.calls, [])


class ModelPathGuard(unittest.TestCase):
    def test_a_sibling_sharing_a_prefix_is_not_inside(self):
        """`realpath(p).startswith(root)` also matches `root-something-else`."""
        from model_manager import _inside

        with TemporaryDirectory() as tmp:
            root = os.path.join(tmp, 'cache')
            os.makedirs(os.path.join(root, 'inner'))
            os.makedirs(os.path.join(tmp, 'cache-evil'))
            self.assertTrue(_inside(os.path.join(root, 'inner'), root))
            self.assertFalse(_inside(os.path.join(tmp, 'cache-evil'), root))
            self.assertFalse(_inside(tmp, root))

    def test_unknown_model_ids_never_reach_the_filesystem(self):
        """The control that actually holds today: an allowlist, checked first."""
        from model_manager import ModelManager

        manager = ModelManager()
        for bad in ('../../etc', 'nonsense/model', '/absolute/path', ''):
            with self.assertRaises(ValueError):
                manager.delete(bad)


class DataAtRest(unittest.TestCase):
    def test_settings_are_written_owner_only(self):
        import settings

        saved = (settings.SETTINGS_DIR, settings.SETTINGS_FILE)
        try:
            with TemporaryDirectory() as tmp:
                settings.SETTINGS_DIR = tmp
                settings.SETTINGS_FILE = os.path.join(tmp, 's.json')
                settings.set_value('USER_NAME', 'Ritik')
                mode = os.stat(settings.SETTINGS_FILE).st_mode & 0o777
                self.assertEqual(mode, 0o600, f'settings.json is {oct(mode)}')
        finally:
            settings.SETTINGS_DIR, settings.SETTINGS_FILE = saved

    def test_the_runtime_handshake_is_owner_only(self):
        """It carries the API token in plaintext."""
        from server import runtime

        path = runtime.runtime_path()
        if not path.exists():
            self.skipTest('no engine has run on this machine')
        mode = os.stat(path).st_mode & 0o777
        self.assertEqual(mode, 0o600, f'runtime.json is {oct(mode)}')

    def test_the_docs_endpoints_are_disabled(self):
        """Swagger UI loads its JavaScript from a CDN."""
        from fastapi.testclient import TestClient
        from server.api import create_app
        from server.store import TranscriptStore

        with TemporaryDirectory() as tmp:
            store = TranscriptStore(os.path.join(tmp, 't.db'))
            client = TestClient(create_app(store=store, auth_token=None))
            for path in ('/docs', '/redoc', '/openapi.json'):
                self.assertEqual(client.get(path).status_code, 404, path)
            store.close()


class NoTelemetry(unittest.TestCase):
    """The product promise: the binary never phones home."""

    ENGINE_MODULES = (
        'controller', 'audio', 'transcriber', 'injector', 'postprocess',
        'corrections', 'readback', 'recordings', 'vocabulary', 'overlay_theme',
        'settings', 'config', 'notifications', 'metrics',
    )

    def test_no_engine_module_opens_an_outbound_connection(self):
        import importlib

        offenders = []
        for name in self.ENGINE_MODULES:
            mod = importlib.import_module(name)
            src = inspect.getsource(mod)
            for needle in ('requests.post', 'requests.get', 'urlopen',
                           'http.client', 'aiohttp', 'socket.create_connection'):
                if needle in src:
                    offenders.append(f'{name}: {needle}')
        self.assertEqual(offenders, [], f'outbound network in engine core: {offenders}')

    def test_the_profile_endpoint_stores_locally_and_caps_the_name(self):
        import settings
        from fastapi.testclient import TestClient
        from server.api import create_app
        from server.store import TranscriptStore

        saved = (settings.SETTINGS_DIR, settings.SETTINGS_FILE)
        try:
            with TemporaryDirectory() as tmp:
                settings.SETTINGS_DIR = tmp
                settings.SETTINGS_FILE = os.path.join(tmp, 's.json')
                store = TranscriptStore(os.path.join(tmp, 't.db'))
                client = TestClient(create_app(store=store, auth_token=None))

                body = client.post('/user/profile', json={'first_name': '  Ritik  '}).json()
                self.assertEqual(body['first_name'], 'Ritik')
                self.assertFalse(body['onboarded'])

                long_name = client.post('/user/profile', json={'first_name': 'z' * 500}).json()
                self.assertLessEqual(len(long_name['first_name']), 48)

                self.assertTrue(client.post('/user/profile', json={'onboarded': True})
                                .json()['onboarded'])
                store.close()
        finally:
            settings.SETTINGS_DIR, settings.SETTINGS_FILE = saved


if __name__ == '__main__':
    unittest.main()
