"""The notification switch has to work without a restart."""
from __future__ import annotations

import unittest
from unittest.mock import patch

import config
import notifications


class NotificationToggleTests(unittest.TestCase):
    def setUp(self):
        self._saved = config.SHOW_NOTIFICATIONS

    def tearDown(self):
        config.SHOW_NOTIFICATIONS = self._saved

    def test_the_flag_is_read_at_call_time(self):
        """Regression: the module used to copy the value at import, so the
        Preferences switch only took effect after a relaunch."""
        config.SHOW_NOTIFICATIONS = False
        with patch('notifications.subprocess.run') as run:
            notifications.notify('Say', 'hidden')
        run.assert_not_called()

        config.SHOW_NOTIFICATIONS = True
        with patch('notifications.subprocess.run') as run:
            notifications.notify('Say', 'shown')
        run.assert_called_once()

    def test_the_text_still_travels_as_an_argument(self):
        config.SHOW_NOTIFICATIONS = True
        with patch('notifications.subprocess.run') as run:
            notifications.notify('Say', 'line one\nline two')
        argv = run.call_args.args[0]
        self.assertEqual(argv[-2:], ['line one line two', 'Say'])


if __name__ == '__main__':
    unittest.main()
