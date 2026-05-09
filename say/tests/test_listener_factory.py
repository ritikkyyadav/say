"""Tests for the listener auto-fallback factory."""

import sys
import unittest
from unittest.mock import MagicMock, patch


class ListenerFactoryTests(unittest.TestCase):
    def test_uses_fn_listener_when_available(self):
        from listener_factory import make_listener

        fake_fn = MagicMock()
        fake_fn.start.return_value = True
        fake_fn.hotkey_label = 'Fn (hold) / Fn+Space (toggle)'

        fake_module = MagicMock()
        fake_module.FnHotkeyListener.return_value = fake_fn

        with patch.dict(sys.modules, {'hotkey_fn': fake_module}):
            info = make_listener(
                on_start=lambda: None,
                on_stop=lambda: None,
            )

        self.assertEqual(info.kind, 'fn')
        self.assertFalse(info.fell_back)
        self.assertIs(info.listener, fake_fn)

    def test_falls_back_to_legacy_when_fn_fails(self):
        from listener_factory import make_listener

        failing_fn = MagicMock()
        failing_fn.start.return_value = False
        failing_fn.hotkey_label = 'Fn'

        fake_fn_module = MagicMock()
        fake_fn_module.FnHotkeyListener.return_value = failing_fn

        legacy = MagicMock()
        legacy.start.return_value = True
        legacy.hotkey_label = 'ctrl+space'

        fake_legacy_module = MagicMock()
        fake_legacy_module.HotkeyListener.return_value = legacy

        with patch.dict(
            sys.modules,
            {'hotkey_fn': fake_fn_module, 'hotkey': fake_legacy_module},
        ):
            info = make_listener(
                on_start=lambda: None,
                on_stop=lambda: None,
            )

        self.assertEqual(info.kind, 'legacy')
        self.assertTrue(info.fell_back)
        self.assertEqual(info.label, 'ctrl+space')

    def test_raises_when_both_fail(self):
        from listener_factory import make_listener

        failing_fn = MagicMock()
        failing_fn.start.return_value = False
        failing_fn.hotkey_label = 'Fn'
        fake_fn_module = MagicMock()
        fake_fn_module.FnHotkeyListener.return_value = failing_fn

        legacy = MagicMock()
        legacy.start.return_value = False
        legacy.hotkey_label = 'ctrl+space'
        fake_legacy_module = MagicMock()
        fake_legacy_module.HotkeyListener.return_value = legacy

        with patch.dict(
            sys.modules,
            {'hotkey_fn': fake_fn_module, 'hotkey': fake_legacy_module},
        ), self.assertRaises(RuntimeError):
            make_listener(
                on_start=lambda: None,
                on_stop=lambda: None,
            )


if __name__ == '__main__':
    unittest.main()
