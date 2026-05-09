"""Tests for the single-instance lock."""

import importlib
import os
import shutil
import tempfile
import unittest


class SingleInstanceTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='say-instance-')

        import settings as settings_mod
        importlib.reload(settings_mod)
        settings_mod.SETTINGS_DIR = self.tmpdir

        import single_instance as si
        self.si = importlib.reload(si)
        self.si.LOCK_PATH = os.path.join(self.tmpdir, 'say.lock')

    def tearDown(self):
        try:
            self.si.release()
        finally:
            shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_first_acquire_succeeds(self):
        self.assertTrue(self.si.acquire())
        self.assertTrue(os.path.exists(self.si.LOCK_PATH))

    def test_second_acquire_in_same_process_idempotent(self):
        # In the same process flock by default doesn't conflict with itself
        # but our acquire() opens a fresh FD each time. The first call holds
        # the lock; the second can either succeed (re-entrant) or fail.
        # We accept either as long as it doesn't raise.
        self.assertTrue(self.si.acquire())
        try:
            self.si.acquire()
        except Exception as exc:
            self.fail(f'acquire raised: {exc}')

    def test_release_clears_module_state(self):
        self.assertTrue(self.si.acquire())
        self.si.release()
        self.assertIsNone(self.si._Lock.fp)


if __name__ == '__main__':
    unittest.main()
