"""Tests for say/model_catalog.py — catalog data + cache-state helpers."""
from __future__ import annotations

import os
import tempfile
import unittest

import config
import model_catalog as mc


def _fake_cached_model(cache_root: str, model_id: str, weight_bytes: int = 2_000_000) -> None:
    """Build a minimal HF-cache layout so is_downloaded() sees `model_id`."""
    repo = os.path.join(cache_root, 'models--' + model_id.replace('/', '--'))
    blobs = os.path.join(repo, 'blobs')
    snap = os.path.join(repo, 'snapshots', 'rev1')
    os.makedirs(blobs, exist_ok=True)
    os.makedirs(snap, exist_ok=True)
    blob = os.path.join(blobs, 'deadbeef')
    with open(blob, 'wb') as f:
        f.write(b'\0' * weight_bytes)
    os.symlink(
        os.path.join('..', '..', 'blobs', 'deadbeef'),
        os.path.join(snap, 'weights.safetensors'),
    )


class ModelCatalogTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._prev_cache = os.environ.get('HF_HUB_CACHE')
        self._prev_bundle = os.environ.get('SAY_BUNDLED_MODELS_DIR')
        os.environ['HF_HUB_CACHE'] = self._tmp.name
        # Neutralize bundled-model discovery so cache tests are deterministic.
        self._bundle_dir = tempfile.mkdtemp(prefix='say-bundle-')
        os.environ['SAY_BUNDLED_MODELS_DIR'] = self._bundle_dir

    def tearDown(self):
        for key, prev in (
            ('HF_HUB_CACHE', self._prev_cache),
            ('SAY_BUNDLED_MODELS_DIR', self._prev_bundle),
        ):
            if prev is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = prev
        self._tmp.cleanup()
        import shutil

        shutil.rmtree(self._bundle_dir, ignore_errors=True)

    def test_catalog_is_ordered_and_unique(self):
        cat = mc.catalog()
        self.assertGreaterEqual(len(cat), 4)
        ids = [m.id for m in cat]
        self.assertEqual(len(ids), len(set(ids)), 'duplicate model ids')
        orders = [m.order for m in cat]
        self.assertEqual(orders, sorted(orders), 'catalog() must be order-sorted')

    def test_catalog_ids_match_config_valid_models(self):
        # Guard against drift: validation.py checks MODEL against
        # config.VALID_MODELS, so it must equal the catalog.
        self.assertEqual(set(config.VALID_MODELS), set(mc.CATALOG_IDS))

    def test_recommended_model_is_the_shipped_default(self):
        recommended = [m for m in mc.catalog() if m.recommended]
        self.assertEqual(len(recommended), 1)
        self.assertEqual(recommended[0].id, config.MODEL)

    def test_repo_dir_maps_slashes(self):
        d = mc.repo_dir('mlx-community/whisper-tiny-mlx')
        self.assertTrue(d.endswith('models--mlx-community--whisper-tiny-mlx'))
        self.assertTrue(d.startswith(self._tmp.name))

    def test_is_downloaded_false_then_true(self):
        mid = 'mlx-community/whisper-tiny-mlx'
        self.assertFalse(mc.is_downloaded(mid))
        self.assertEqual(mc.disk_usage_bytes(mid), 0)

        _fake_cached_model(self._tmp.name, mid, weight_bytes=2_000_000)
        self.assertTrue(mc.is_downloaded(mid))
        self.assertEqual(mc.disk_usage_bytes(mid), 2_000_000)

    def test_tiny_weights_below_threshold_not_downloaded(self):
        # A stray small file in the snapshot must not read as a real model.
        mid = 'mlx-community/whisper-base-mlx'
        _fake_cached_model(self._tmp.name, mid, weight_bytes=10_000)
        self.assertFalse(mc.is_downloaded(mid))

    def test_bundled_model_reads_as_downloaded(self):
        mid = 'mlx-community/whisper-small-mlx'
        leaf = mid.split('/')[-1]
        os.makedirs(os.path.join(self._bundle_dir, leaf), exist_ok=True)
        self.assertTrue(mc.is_downloaded(mid))
        self.assertTrue(mc.bundled_dir(mid) is not None)

    def test_to_dict_shape(self):
        info = mc.catalog()[0]
        d = mc.to_dict(info)
        for key in ('id', 'name', 'family', 'downloaded', 'bundled', 'size_bytes', 'speed'):
            self.assertIn(key, d)
        self.assertIsInstance(d['downloaded'], bool)


if __name__ == '__main__':
    unittest.main()
