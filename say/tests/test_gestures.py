"""Tests for the hotkey → engine → glyph wiring (gestures.py).

The failure these pin was invisible to every other suite because it lived in
the *wiring*, not in any component: the overlay was correct, the controller
was correct, and the two closures in app.py that connected them showed the
glyph and opened the microphone on key-down without asking the controller
whether it could record. Measured on the shipped engine's own log, 58 Fn
presses were refused during PROCESSING — each one put a live, voice-reactive
mark on screen for a recording that was never happening, then tore it down
mid-sentence when the busy job finished, and left the mic stream open.
"""
from __future__ import annotations

import unittest

import config
from controller import DictationController, DictationEvent, DictationState
from gestures import GestureBridge
from injector import InjectionResult

BUSY = {DictationState.LOADING, DictationState.RECORDING, DictationState.PROCESSING}


class FakeController:
    def __init__(self, state=DictationState.READY, accept=True):
        self.state = state
        self.accept = accept
        self.calls: list[tuple] = []

    def can_start_recording(self) -> bool:
        return self.state not in BUSY

    def start_recording(self, mode='ptt') -> bool:
        self.calls.append(('start', mode))
        return self.accept and self.can_start_recording()

    def stop_recording(self) -> bool:
        self.calls.append(('stop',))
        return True

    def cancel_recording(self) -> bool:
        self.calls.append(('cancel',))
        return True


class FakeRecorder:
    def __init__(self):
        self.calls: list[str] = []

    def arm(self):
        self.calls.append('arm')

    def disarm(self):
        self.calls.append('disarm')


class FakeOverlay:
    def __init__(self, fail: set[str] | None = None):
        self.calls: list[tuple] = []
        self._fail = fail or set()

    def __getattr__(self, name):
        if name.startswith('_'):
            raise AttributeError(name)

        def method(*args):
            if name in self._fail:
                raise RuntimeError(f'{name} exploded')
            self.calls.append((name, *args))

        return method


class FakeListener:
    def __init__(self, mode='ptt'):
        self.mode = mode
        self.idled = 0

    def force_idle(self):
        self.idled += 1


def _bridge(controller, recorder=None, overlay=None, listener=None):
    recorder = recorder or FakeRecorder()
    return (
        GestureBridge(
            controller,
            recorder,
            overlay=(lambda: overlay),
            listener=(lambda: listener),
        ),
        recorder,
    )


class ArmingTests(unittest.TestCase):
    def test_key_down_while_ready_shows_the_glyph_before_opening_the_mic(self):
        """The order is the whole point: the mic can block for seconds."""
        order: list[str] = []
        overlay = FakeOverlay()
        recorder = FakeRecorder()
        overlay.calls = order
        recorder.calls = order
        bridge, _ = _bridge(FakeController(DictationState.READY), recorder, overlay)

        bridge.on_arm()

        self.assertEqual(order, [('arm',), 'arm'])

    def test_key_down_while_processing_touches_neither_glyph_nor_mic(self):
        """A recording that cannot start must not look or sound like one."""
        overlay = FakeOverlay()
        bridge, recorder = _bridge(FakeController(DictationState.PROCESSING), overlay=overlay)

        with self.assertLogs('say.gestures', level='INFO') as logs:
            bridge.on_arm()

        self.assertEqual(overlay.calls, [])
        self.assertEqual(recorder.calls, [])
        self.assertTrue(any('processing' in line for line in logs.output))

    def test_key_down_while_loading_or_recording_is_ignored_too(self):
        for state in (DictationState.LOADING, DictationState.RECORDING):
            overlay = FakeOverlay()
            bridge, recorder = _bridge(FakeController(state), overlay=overlay)
            bridge.on_arm()
            self.assertEqual(overlay.calls, [], state)
            self.assertEqual(recorder.calls, [], state)

    def test_key_down_after_an_error_still_arms(self):
        """ERROR is a startable state — the controller accepts a retry from it."""
        overlay = FakeOverlay()
        bridge, recorder = _bridge(FakeController(DictationState.ERROR), overlay=overlay)
        bridge.on_arm()
        self.assertEqual(overlay.calls, [('arm',)])
        self.assertEqual(recorder.calls, ['arm'])

    def test_a_tap_takes_back_the_glyph_and_the_mic(self):
        overlay = FakeOverlay()
        bridge, recorder = _bridge(FakeController(), overlay=overlay)
        bridge.on_arm()
        bridge.on_disarm()
        self.assertEqual(overlay.calls, [('arm',), ('release_arm',)])
        self.assertEqual(recorder.calls, ['arm', 'disarm'])

    def test_a_controller_without_the_predicate_is_treated_as_ready(self):
        class Bare:
            state = DictationState.READY

            def start_recording(self, mode='ptt'):
                return True

        overlay = FakeOverlay()
        bridge, recorder = _bridge(Bare(), overlay=overlay)
        bridge.on_arm()
        self.assertEqual(overlay.calls, [('arm',)])
        self.assertEqual(recorder.calls, ['arm'])


class StartTests(unittest.TestCase):
    def test_an_accepted_start_leaves_the_armed_glyph_up(self):
        overlay = FakeOverlay()
        listener = FakeListener(mode='hands_free')
        controller = FakeController()
        bridge, recorder = _bridge(controller, overlay=overlay, listener=listener)

        bridge.on_arm()
        bridge.on_start()

        self.assertEqual(controller.calls, [('start', 'hands_free')])
        self.assertEqual(overlay.calls, [('arm',)])
        self.assertEqual(recorder.calls, ['arm'])
        self.assertEqual(listener.idled, 0)

    def test_a_refused_start_retracts_the_glyph_closes_the_mic_and_resets_the_listener(self):
        """The regression. The state flipped to PROCESSING between key-down and
        the tap-vs-hold window resolving; the controller says no; everything
        the arm put up has to come back down."""
        overlay = FakeOverlay()
        listener = FakeListener()
        controller = FakeController(DictationState.READY)
        bridge, recorder = _bridge(controller, overlay=overlay, listener=listener)

        bridge.on_arm()
        controller.state = DictationState.PROCESSING   # the previous job is still busy
        bridge.on_start()

        self.assertEqual(overlay.calls, [('arm',), ('release_arm',)])
        self.assertEqual(recorder.calls, ['arm', 'disarm'])
        self.assertEqual(listener.idled, 1)

    def test_a_start_that_raises_is_treated_as_refused_and_never_escapes(self):
        class Boom(FakeController):
            def start_recording(self, mode='ptt'):
                raise RuntimeError('mic exploded')

        overlay = FakeOverlay()
        bridge, recorder = _bridge(Boom(), overlay=overlay)
        bridge.on_arm()
        bridge.on_start()          # must not raise into the hotkey thread
        self.assertEqual(overlay.calls, [('arm',), ('release_arm',)])
        self.assertEqual(recorder.calls, ['arm', 'disarm'])

    def test_stop_and_cancel_reach_the_controller(self):
        controller = FakeController()
        bridge, _ = _bridge(controller)
        bridge.on_stop()
        bridge.on_cancel()
        self.assertEqual(controller.calls, [('stop',), ('cancel',)])

    def test_the_mode_defaults_to_ptt_without_a_listener(self):
        controller = FakeController()
        bridge, _ = _bridge(controller)
        bridge.on_start()
        self.assertEqual(controller.calls, [('start', 'ptt')])


class StatusRoutingTests(unittest.TestCase):
    def _event(self, state, mode=None):
        return DictationEvent(state, 'msg', mode=mode)

    def test_state_events_drive_the_glyph(self):
        overlay = FakeOverlay()
        bridge, _ = _bridge(FakeController(), overlay=overlay)

        bridge.on_status(self._event(DictationState.RECORDING, mode='hands_free'))
        bridge.on_status(self._event(DictationState.PROCESSING))
        bridge.on_status(self._event(DictationState.READY))
        bridge.on_status(self._event(DictationState.ERROR))
        bridge.on_status(self._event(DictationState.LOADING))

        self.assertEqual(
            overlay.calls,
            [('listen', 'hands_free'), ('process',), ('dismiss',), ('dismiss',), ('dismiss',)],
        )

    def test_recording_without_a_mode_listens_as_push_to_talk(self):
        overlay = FakeOverlay()
        bridge, _ = _bridge(FakeController(), overlay=overlay)
        bridge.on_status(self._event(DictationState.RECORDING))
        self.assertEqual(overlay.calls, [('listen', 'ptt')])


class OverlayIsolationTests(unittest.TestCase):
    def test_no_overlay_is_not_an_error(self):
        bridge, recorder = _bridge(FakeController(), overlay=None)
        bridge.on_arm()
        bridge.on_status(DictationEvent(DictationState.READY, 'x'))
        self.assertEqual(recorder.calls, ['arm'])

    def test_a_broken_overlay_never_raises_and_warns_once_per_action(self):
        overlay = FakeOverlay(fail={'arm'})
        bridge, recorder = _bridge(FakeController(), overlay=overlay)

        with self.assertLogs('say.gestures', level='WARNING') as logs:
            bridge.on_arm()
            bridge.on_arm()

        warnings = [line for line in logs.output if 'Overlay arm failed' in line]
        self.assertEqual(len(warnings), 1)
        # The mic still opened both times: the glyph is feedback, not a gate.
        self.assertEqual(recorder.calls, ['arm', 'arm'])


class RealControllerTests(unittest.TestCase):
    """The bridge against the actual state machine, not a fake of it."""

    class Recorder(FakeRecorder):
        def start(self):
            self.calls.append('start')

        def stop(self):
            self.calls.append('stop')
            return [0] * config.SAMPLE_RATE

        @staticmethod
        def duration(audio):
            return len(audio) / config.SAMPLE_RATE

    def test_a_key_press_during_processing_is_a_no_op_end_to_end(self):
        recorder = self.Recorder()
        controller = DictationController(
            recorder=recorder,
            transcriber=None,
            injector=lambda _: InjectionResult(True, 'clipboard'),
            logger=None,
        )
        overlay = FakeOverlay()
        bridge = GestureBridge(controller, recorder, overlay=lambda: overlay)

        controller.state = DictationState.PROCESSING
        bridge.on_arm()
        bridge.on_start()

        self.assertEqual(overlay.calls, [('release_arm',)])   # nothing was armed; retract is harmless
        self.assertEqual(recorder.calls, ['disarm'])

    def test_a_key_press_while_ready_records_end_to_end(self):
        recorder = self.Recorder()
        controller = DictationController(
            recorder=recorder,
            transcriber=None,
            injector=lambda _: InjectionResult(True, 'clipboard'),
            logger=None,
        )
        overlay = FakeOverlay()
        bridge = GestureBridge(controller, recorder, overlay=lambda: overlay)
        controller.status_callback = bridge.on_status
        controller.state = DictationState.READY

        bridge.on_arm()
        bridge.on_start()

        self.assertEqual(controller.state, DictationState.RECORDING)
        self.assertEqual(overlay.calls, [('arm',), ('listen', 'ptt')])
        self.assertEqual(recorder.calls, ['arm', 'start'])


if __name__ == '__main__':
    unittest.main()
