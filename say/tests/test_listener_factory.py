"""Tests for the listener auto-fallback factory and the live upgrade."""

import sys
import threading
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


class ListenerUpgraderTests(unittest.TestCase):
    """The fallback -> Fn swap that removes the "quit and reopen" step.

    A TCC grant applies to the already-running process; only the check made at
    startup is stale. Users grant Accessibility and then restart the *window*,
    which the engine outlives — so without this they sit on ctrl+space with no
    indication why.
    """

    def _info(self, label='Fn (hold) / Fn+Space (toggle)'):
        from listener_factory import ListenerInfo

        return ListenerInfo(
            listener=MagicMock(), kind='fn', label=label, fell_back=False
        )

    def test_upgrades_once_accessibility_is_granted(self):
        from listener_factory import ListenerUpgrader

        info = self._info()
        adopted = threading.Event()
        seen = []

        def on_upgraded(new):
            seen.append(new)
            adopted.set()

        upgrader = ListenerUpgrader(
            lambda: (info, None), on_upgraded, poll_seconds=0.01
        )
        with patch('permissions.accessibility_trusted', return_value=True):
            upgrader.start()
            self.assertTrue(adopted.wait(timeout=3), 'upgrade never fired')
        upgrader.stop()

        self.assertEqual(seen, [info])

    def test_does_not_upgrade_while_untrusted(self):
        from listener_factory import ListenerUpgrader

        attempted = []

        def build():
            attempted.append(1)
            return (self._info(), None)

        upgrader = ListenerUpgrader(
            build, lambda _info: None, poll_seconds=0.01
        )
        with patch('permissions.accessibility_trusted', return_value=False):
            upgrader.start()
            threading.Event().wait(0.15)
        upgrader.stop()

        self.assertEqual(attempted, [], 'built a tap without the grant')

    def test_keeps_watching_when_trusted_but_tap_refused(self):
        """Accessibility on, Input Monitoring still off: retry, do not give up."""
        from listener_factory import ListenerUpgrader

        calls = []

        def build():
            calls.append(1)
            if len(calls) < 3:
                return (None, 'tap refused')
            return (self._info(), None)

        adopted = threading.Event()
        upgrader = ListenerUpgrader(
            build,
            lambda _info: adopted.set(),
            poll_seconds=0.01,
            max_poll_seconds=0.02,
        )
        with patch('permissions.accessibility_trusted', return_value=True):
            upgrader.start()
            self.assertTrue(adopted.wait(timeout=3), 'gave up after a refusal')
        upgrader.stop()

        self.assertGreaterEqual(len(calls), 3)

    def test_a_failing_adopt_does_not_leak_the_new_listener(self):
        from listener_factory import ListenerUpgrader

        info = self._info()

        def on_upgraded(_new):
            raise RuntimeError('swap blew up')

        upgrader = ListenerUpgrader(
            lambda: (info, None), on_upgraded, poll_seconds=0.01
        )
        with patch('permissions.accessibility_trusted', return_value=True):
            upgrader.start()
            for _ in range(200):
                if info.listener.stop.called:
                    break
                threading.Event().wait(0.01)
        upgrader.stop()

        info.listener.stop.assert_called()

    def test_stop_is_safe_before_and_after_start(self):
        from listener_factory import ListenerUpgrader

        upgrader = ListenerUpgrader(
            lambda: (None, 'nope'), lambda _info: None, poll_seconds=0.01
        )
        upgrader.stop()
        with patch('permissions.accessibility_trusted', return_value=False):
            upgrader.start()
            self.assertTrue(upgrader.running)
        upgrader.stop()
        self.assertFalse(upgrader.running)


class TryStartFnListenerTests(unittest.TestCase):
    def test_returns_error_and_stops_the_tap_when_start_fails(self):
        from listener_factory import try_start_fn_listener

        failing = MagicMock()
        failing.start.return_value = False
        module = MagicMock()
        module.FnHotkeyListener.return_value = failing

        with patch.dict(sys.modules, {'hotkey_fn': module}):
            info, error = try_start_fn_listener(
                on_start=lambda: None, on_stop=lambda: None
            )

        self.assertIsNone(info)
        self.assertIn('CGEventTap', error)
        failing.stop.assert_called_once()


if __name__ == '__main__':
    unittest.main()
