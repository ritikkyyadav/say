import importlib
import os
import shutil
import tempfile
import unittest


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='say-settings-')
        # Patch SETTINGS_DIR / SETTINGS_FILE / FIRST_RUN_MARKER on the
        # module so tests do not write to the user's real Library.
        import settings as settings_mod

        self.settings_mod = importlib.reload(settings_mod)
        self.settings_mod.SETTINGS_DIR = self.tmpdir
        self.settings_mod.SETTINGS_FILE = os.path.join(self.tmpdir, 'settings.json')
        self.settings_mod.FIRST_RUN_MARKER = os.path.join(
            self.tmpdir, 'first_run_complete'
        )

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_get_returns_config_default_when_unset(self):
        import config

        self.assertEqual(self.settings_mod.get('MODEL'), config.MODEL)

    def test_set_value_persists_and_mirrors(self):
        import config

        self.settings_mod.set_value('MODEL', 'mlx-community/whisper-small-mlx')
        self.assertEqual(config.MODEL, 'mlx-community/whisper-small-mlx')
        self.assertEqual(
            self.settings_mod.get('MODEL'),
            'mlx-community/whisper-small-mlx',
        )

        # Restore a clean module so the rest of the suite sees the
        # original config values.
        config.MODEL = 'mlx-community/whisper-large-v3-turbo'

    def test_set_value_rejects_unknown_keys(self):
        with self.assertRaises(KeyError):
            self.settings_mod.set_value('NOT_A_REAL_SETTING', 'x')

    def test_first_run_marker(self):
        self.assertTrue(self.settings_mod.is_first_run())
        self.settings_mod.mark_first_run_complete()
        self.assertFalse(self.settings_mod.is_first_run())

    def test_apply_to_config_overrides(self):
        import config
        original = config.LANGUAGE
        try:
            self.settings_mod.set_value('LANGUAGE', 'es')
            # Reset the in-memory attribute and re-apply
            config.LANGUAGE = original
            self.settings_mod.apply_to_config()
            self.assertEqual(config.LANGUAGE, 'es')
        finally:
            config.LANGUAGE = original


if __name__ == '__main__':
    unittest.main()
