"""State-machine tests for FnHotkeyListener.

These exercise the pure Python state transitions without spinning up
the Quartz CGEventTap. We invoke the private `_handle_*` methods
directly — same path the real event tap takes.
"""

import time
import unittest


class FnHotkeyStateMachineTests(unittest.TestCase):
    def _make(self):
        from hotkey_fn import FnHotkeyListener

        events = []

        def cb(name):
            def fn():
                events.append(name)
            return fn

        listener = FnHotkeyListener(
            on_start_cb=cb('start'),
            on_stop_cb=cb('stop'),
            on_cancel_cb=cb('cancel'),
        )
        return listener, events

    def _wait_for(self, events, name, timeout=1.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if name in events:
                return True
            time.sleep(0.01)
        return False

    def test_short_fn_tap_does_not_fire(self):
        listener, events = self._make()
        listener._handle_fn_down()
        listener._handle_fn_up()
        # Quick tap with no Space → ignored, no callbacks
        self.assertNotIn('start', events)
        self.assertNotIn('stop', events)

    def test_fn_held_triggers_push_to_talk(self):
        listener, events = self._make()
        # Lower the pending timeout so the test runs fast.
        import hotkey_fn

        original = hotkey_fn._PENDING_TIMEOUT
        hotkey_fn._PENDING_TIMEOUT = 0.05
        try:
            listener._handle_fn_down()
            self.assertTrue(self._wait_for(events, 'start', timeout=1.0))
            listener._handle_fn_up()
            self.assertTrue(self._wait_for(events, 'stop', timeout=1.0))
        finally:
            hotkey_fn._PENDING_TIMEOUT = original

    def test_fn_plus_space_starts_hands_free(self):
        listener, events = self._make()
        listener._handle_fn_down()
        listener._handle_space_down()
        self.assertTrue(self._wait_for(events, 'start', timeout=1.0))
        self.assertEqual(listener.mode, 'hands_free')

        # Releasing Fn while in hands-free is a no-op.
        listener._handle_fn_up()
        time.sleep(0.05)
        self.assertNotIn('stop', events)

        # Pressing Fn again stops.
        listener._handle_fn_down()
        self.assertTrue(self._wait_for(events, 'stop', timeout=1.0))

    def test_escape_cancels(self):
        listener, events = self._make()
        import hotkey_fn

        original = hotkey_fn._PENDING_TIMEOUT
        hotkey_fn._PENDING_TIMEOUT = 0.05
        try:
            listener._handle_fn_down()
            self.assertTrue(self._wait_for(events, 'start', timeout=1.0))
            listener._handle_escape()
            self.assertTrue(self._wait_for(events, 'cancel', timeout=1.0))
        finally:
            hotkey_fn._PENDING_TIMEOUT = original

    def test_force_idle_resets_state(self):
        listener, _ = self._make()
        import hotkey_fn

        original = hotkey_fn._PENDING_TIMEOUT
        hotkey_fn._PENDING_TIMEOUT = 0.05
        try:
            listener._handle_fn_down()
            time.sleep(0.1)
            self.assertTrue(listener.is_recording)
            listener.force_idle()
            self.assertFalse(listener.is_recording)
            self.assertEqual(listener.current_state(), 'idle')
        finally:
            hotkey_fn._PENDING_TIMEOUT = original


if __name__ == '__main__':
    unittest.main()
