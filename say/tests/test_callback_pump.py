"""The hotkey callback pump: ordered, isolated, stoppable."""
from __future__ import annotations

import threading
import time
import unittest

from callback_pump import CallbackPump


def _wait(predicate, timeout=2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return False


class CallbackPumpTests(unittest.TestCase):
    def test_callbacks_run_in_the_order_they_were_fired(self):
        """The regression: a slow `start` followed by an instant `stop`.

        With one thread per callback, stop finished first and the recording
        it was meant to end had not begun yet.
        """
        done: list[str] = []
        pump = CallbackPump()

        def start():
            time.sleep(0.05)
            done.append('start')

        pump.fire(start)
        pump.fire(lambda: done.append('stop'))

        self.assertTrue(_wait(lambda: len(done) == 2))
        self.assertEqual(done, ['start', 'stop'])
        pump.stop()

    def test_a_raising_callback_does_not_stop_later_ones(self):
        done: list[str] = []
        pump = CallbackPump()

        def boom():
            raise RuntimeError('callback exploded')

        with self.assertLogs('say.callback_pump', level='ERROR'):
            pump.fire(boom)
            pump.fire(lambda: done.append('after'))
            self.assertTrue(_wait(lambda: done == ['after']))
        pump.stop()

    def test_callbacks_run_off_the_firing_thread(self):
        seen: list[threading.Thread] = []
        pump = CallbackPump()
        pump.fire(lambda: seen.append(threading.current_thread()))
        self.assertTrue(_wait(lambda: len(seen) == 1))
        self.assertIsNot(seen[0], threading.current_thread())
        pump.stop()

    def test_stop_ends_the_worker_and_is_idempotent(self):
        pump = CallbackPump()
        pump.fire(lambda: None)
        self.assertTrue(_wait(lambda: pump.running))
        pump.stop()
        self.assertFalse(pump.running)
        pump.stop()

    def test_the_worker_restarts_after_stop(self):
        done: list[str] = []
        pump = CallbackPump()
        pump.fire(lambda: done.append('a'))
        self.assertTrue(_wait(lambda: done == ['a']))
        pump.stop()
        pump.fire(lambda: done.append('b'))
        self.assertTrue(_wait(lambda: done == ['a', 'b']))
        pump.stop()

    def test_stop_from_inside_a_callback_does_not_deadlock(self):
        pump = CallbackPump()
        finished = threading.Event()

        def stop_myself():
            pump.stop(timeout=0.5)
            finished.set()

        pump.fire(stop_myself)
        self.assertTrue(finished.wait(2.0))


class ListenerOrderingTests(unittest.TestCase):
    """The real listeners route through the pump."""

    def test_fn_listener_keeps_start_before_stop(self):
        from hotkey_fn import FnHotkeyListener

        done: list[str] = []

        def start():
            time.sleep(0.05)
            done.append('start')

        listener = FnHotkeyListener(
            on_start_cb=start, on_stop_cb=lambda: done.append('stop')
        )
        listener._start_recording()
        listener._stop_recording()
        self.assertTrue(_wait(lambda: len(done) == 2))
        self.assertEqual(done, ['start', 'stop'])
        listener.stop()
        self.assertFalse(listener._pump.running)

    def test_legacy_listener_keeps_start_before_stop(self):
        try:
            from hotkey import HotkeyListener
        except Exception:  # pragma: no cover - pynput missing
            self.skipTest('pynput unavailable')

        done: list[str] = []

        def start():
            time.sleep(0.05)
            done.append('start')

        listener = HotkeyListener(
            on_press_cb=start, on_release_cb=lambda: done.append('stop'),
            hotkey='ctrl+space',
        )
        listener._fire(listener._on_press_cb)
        listener._fire(listener._on_release_cb)
        self.assertTrue(_wait(lambda: len(done) == 2))
        self.assertEqual(done, ['start', 'stop'])
        listener.stop()


if __name__ == '__main__':
    unittest.main()
