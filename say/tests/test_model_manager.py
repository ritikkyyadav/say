"""Tests for say/model_manager.py — swap, download, delete orchestration."""
from __future__ import annotations

import errno
import importlib
import os
import shutil
import tempfile
import time
import unittest

import config
import model_catalog as mc


def _build_cache_layout(cache_root: str, model_id: str, weight_bytes: int = 2_000_000) -> None:
    repo = os.path.join(cache_root, 'models--' + model_id.replace('/', '--'))
    blobs = os.path.join(repo, 'blobs')
    snap = os.path.join(repo, 'snapshots', 'rev1')
    os.makedirs(blobs, exist_ok=True)
    os.makedirs(snap, exist_ok=True)
    with open(os.path.join(blobs, 'deadbeef'), 'wb') as f:
        f.write(b'\0' * weight_bytes)
    link = os.path.join(snap, 'weights.safetensors')
    if not os.path.exists(link):
        os.symlink(os.path.join('..', '..', 'blobs', 'deadbeef'), link)


class _FakeTranscriber:
    def __init__(self):
        self.swapped_to: list[str] = []

    def swap_model(self, model_id: str) -> None:
        self.swapped_to.append(model_id)


class _FakeEngine:
    def __init__(self):
        self.transcriber = _FakeTranscriber()


class ModelManagerTests(unittest.TestCase):
    def setUp(self):
        # Hermetic settings: redirect the persisted file to a temp dir.
        self._tmp = tempfile.mkdtemp(prefix='say-mm-')
        import settings as settings_mod

        self.settings_mod = importlib.reload(settings_mod)
        self.settings_mod.SETTINGS_DIR = self._tmp
        self.settings_mod.SETTINGS_FILE = os.path.join(self._tmp, 'settings.json')
        self.settings_mod.FIRST_RUN_MARKER = os.path.join(self._tmp, 'first_run_complete')

        # Hermetic HF cache.
        self._cache = tempfile.mkdtemp(prefix='say-mm-cache-')
        self._prev_cache = os.environ.get('HF_HUB_CACHE')
        self._prev_bundle = os.environ.get('SAY_BUNDLED_MODELS_DIR')
        os.environ['HF_HUB_CACHE'] = self._cache
        self._bundle = tempfile.mkdtemp(prefix='say-mm-bundle-')
        os.environ['SAY_BUNDLED_MODELS_DIR'] = self._bundle
        # Hermetic Moonshine cache.
        self._moon = tempfile.mkdtemp(prefix='say-mm-moon-')
        self._prev_moon = os.environ.get('SAY_MOONSHINE_CACHE')
        os.environ['SAY_MOONSHINE_CACHE'] = self._moon

        self._orig_model = config.MODEL

        # Fresh manager + module bound to the reloaded settings.
        import model_manager

        self.mm = importlib.reload(model_manager)
        # model_manager imported `settings as settings_mod` — point it at ours.
        self.mm.settings_mod = self.settings_mod
        self.mgr = self.mm.ModelManager()

    def tearDown(self):
        config.MODEL = self._orig_model
        for key, prev in (
            ('HF_HUB_CACHE', self._prev_cache),
            ('SAY_BUNDLED_MODELS_DIR', self._prev_bundle),
            ('SAY_MOONSHINE_CACHE', self._prev_moon),
        ):
            if prev is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = prev
        for d in (self._tmp, self._cache, self._bundle, self._moon):
            shutil.rmtree(d, ignore_errors=True)

    def test_list_models_marks_active(self):
        rows = self.mgr.list_models()
        self.assertEqual(len(rows), len(mc.catalog()))
        active = [r for r in rows if r['active']]
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]['id'], config.MODEL)

    def test_set_active_persists_and_swaps_live(self):
        engine = _FakeEngine()
        self.mgr.register_engine(engine)

        target = 'mlx-community/whisper-small-mlx'
        result = self.mgr.set_active(target)

        self.assertEqual(result, target)
        self.assertEqual(self.mgr.active_model(), target)
        self.assertEqual(self.settings_mod.get('MODEL'), target)
        self.assertEqual(engine.transcriber.swapped_to, [target])

    def test_set_active_unknown_raises(self):
        with self.assertRaises(ValueError):
            self.mgr.set_active('not-a-real/model')

    def test_start_download_reports_done(self):
        mid = 'mlx-community/whisper-tiny-mlx'

        def fake_snapshot_download(repo_id, **_kw):
            _build_cache_layout(self._cache, repo_id)
            return os.path.join(self._cache, repo_id)

        import huggingface_hub

        orig = huggingface_hub.snapshot_download
        huggingface_hub.snapshot_download = fake_snapshot_download
        try:
            st = self.mgr.start_download(mid)
            self.assertEqual(st['state'], 'downloading')

            deadline = time.time() + 5
            while time.time() < deadline:
                if self.mgr.download_status(mid)['state'] in ('done', 'error'):
                    break
                time.sleep(0.05)
        finally:
            huggingface_hub.snapshot_download = orig

        final = self.mgr.download_status(mid)
        self.assertEqual(final['state'], 'done')
        self.assertEqual(final['percent'], 100)
        self.assertTrue(mc.is_downloaded(mid))

    def test_start_download_already_cached_is_done(self):
        mid = 'mlx-community/whisper-base-mlx'
        _build_cache_layout(self._cache, mid)
        st = self.mgr.start_download(mid)
        self.assertEqual(st['state'], 'done')
        self.assertEqual(st['percent'], 100)

    def test_delete_refuses_active(self):
        with self.assertRaises(ValueError):
            self.mgr.delete(self.mgr.active_model())

    def test_delete_removes_cached_model(self):
        mid = 'mlx-community/whisper-tiny-mlx'  # not the active (moonshine) model
        _build_cache_layout(self._cache, mid)
        self.assertTrue(mc.is_downloaded(mid))
        self.mgr.delete(mid)
        self.assertFalse(mc.is_downloaded(mid))

    def test_delete_removes_moonshine_model(self):
        mid = 'moonshine/medium-streaming-en'
        # Switch active away (Moonshine is the default) so deletion is allowed.
        self.mgr.set_active('mlx-community/whisper-small-mlx')
        d = mc.moonshine_model_dir(mid)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, 'encoder.ort'), 'wb') as f:
            f.write(b'\0' * 200_000)
        self.assertTrue(mc.is_downloaded(mid))
        self.mgr.delete(mid)
        self.assertFalse(mc.is_downloaded(mid))

    def test_total_disk_bytes_sums_downloaded(self):
        a = 'mlx-community/whisper-tiny-mlx'
        b = 'mlx-community/whisper-base-mlx'
        _build_cache_layout(self._cache, a, weight_bytes=1_500_000)
        _build_cache_layout(self._cache, b, weight_bytes=2_500_000)
        self.assertEqual(self.mgr.total_disk_bytes(), 4_000_000)

    def test_start_download_rejects_when_disk_full(self):
        orig = self.mm._free_bytes_for_cache
        self.mm._free_bytes_for_cache = lambda: 1000  # almost no space
        try:
            with self.assertRaises(self.mm.InsufficientStorage):
                self.mgr.start_download('mlx-community/whisper-tiny-mlx')
        finally:
            self.mm._free_bytes_for_cache = orig

    def test_friendly_error_categorizes(self):
        disk = self.mm._friendly_error(OSError(errno.ENOSPC, 'No space left'))
        self.assertIn('disk space', disk.lower())
        net = self.mm._friendly_error(Exception('Connection timed out'))
        self.assertIn('network', net.lower())
        other = self.mm._friendly_error(Exception('weird repo error'))
        self.assertIn('failed', other.lower())

    def test_cancel_noop_when_not_downloading(self):
        self.assertFalse(self.mgr.cancel_download('mlx-community/whisper-tiny-mlx'))

    def test_cancel_aborts_and_cleans_partial(self):
        mid = 'mlx-community/whisper-tiny-mlx'

        # Fake download that "streams" via the cancellable tqdm so a cancel
        # request raises DownloadCancelled mid-stream, exactly like the real
        # snapshot_download path.
        def fake_snapshot_download(repo_id, tqdm_class=None, **_kw):
            # Lay down a partial (sub-threshold) cache so cleanup has work to do.
            _build_cache_layout(self._cache, repo_id, weight_bytes=100_000)
            bar = tqdm_class(total=10_000, disable=True) if tqdm_class else None
            for _ in range(10_000):
                if bar is not None:
                    bar.update(1)  # raises DownloadCancelled once the event is set
                time.sleep(0.002)
            return os.path.join(self._cache, repo_id)

        import huggingface_hub

        orig = huggingface_hub.snapshot_download
        huggingface_hub.snapshot_download = fake_snapshot_download
        try:
            self.mgr.start_download(mid)
            # Wait until the worker is actually streaming.
            deadline = time.time() + 5
            while time.time() < deadline and self.mgr.download_status(mid)['state'] != 'downloading':
                time.sleep(0.02)
            self.assertTrue(self.mgr.cancel_download(mid))

            # Worker should observe the cancel, clean the partial, and clear state.
            deadline = time.time() + 5
            while time.time() < deadline and self.mgr.download_status(mid)['state'] == 'downloading':
                time.sleep(0.02)
        finally:
            huggingface_hub.snapshot_download = orig

        self.assertEqual(self.mgr.download_status(mid)['state'], 'idle')
        self.assertFalse(mc.is_downloaded(mid))
        # Partial cache dir removed.
        self.assertFalse(os.path.isdir(mc.repo_dir(mid)))


if __name__ == '__main__':
    unittest.main()
