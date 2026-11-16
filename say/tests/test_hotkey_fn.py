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

    def test_space_after_ptt_converts_to_hands_free(self):
        """The deliberate 'hold Fn, then tap Space' gesture must work even
        after the PENDING window has lapsed and push-to-talk has begun —
        this is the real-world path (a human can't reliably hit a <250ms
        chord), and the one that was silently falling back to push-to-talk.
        """
        listener, events = self._make()
        import hotkey_fn

        original = hotkey_fn._PENDING_TIMEOUT
        hotkey_fn._PENDING_TIMEOUT = 0.05
        try:
            listener._handle_fn_down()
            # Let the pending window lapse → push-to-talk begins.
            self.assertTrue(self._wait_for(events, 'start', timeout=1.0))
            self.assertEqual(listener.mode, 'ptt')

            # Now tap Space with Fn still held → convert to hands-free,
            # WITHOUT starting a second recording.
            listener._handle_space_down()
            self.assertEqual(listener.mode, 'hands_free')
            self.assertEqual(events.count('start'), 1)

            # Releasing Fn no longer stops (hands-free semantics).
            listener._handle_fn_up()
            time.sleep(0.05)
            self.assertNotIn('stop', events)

            # Pressing Fn again stops & transcribes.
            listener._handle_fn_down()
            self.assertTrue(self._wait_for(events, 'stop', timeout=1.0))
        finally:
            hotkey_fn._PENDING_TIMEOUT = original

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

    def test_fn_arrow_chord_does_not_start_a_dictation(self):
        """Regression: Fn+arrow (Home/End/PageUp/Down) fired a real recording.

        Holding Fn past the pending window used to fall through to push-to-talk
        regardless of what else was pressed, so navigating a document with
        Fn+arrows opened the mic and transcribed the room — text that could then
        be injected into the focused app.
        """
        listener, events = self._make()
        import hotkey_fn

        original = hotkey_fn._PENDING_TIMEOUT
        hotkey_fn._PENDING_TIMEOUT = 0.05
        try:
            listener._handle_fn_down()
            # Down-arrow while Fn is held — an ordinary Page Down.
            listener._handle_other_key()
            self.assertEqual(listener.mode, 'idle')
            # Let the old pending window elapse; nothing may start.
            time.sleep(0.12)
            listener._handle_fn_up()
            time.sleep(0.05)
            self.assertNotIn('start', events)
            self.assertNotIn('stop', events)
        finally:
            hotkey_fn._PENDING_TIMEOUT = original

    def test_key_during_push_to_talk_cancels_and_discards(self):
        """If PTT already began, an ordinary chord must cancel, not transcribe."""
        listener, events = self._make()
        import hotkey_fn

        original = hotkey_fn._PENDING_TIMEOUT
        hotkey_fn._PENDING_TIMEOUT = 0.05
        try:
            listener._handle_fn_down()
            self.assertTrue(self._wait_for(events, 'start', timeout=1.0))
            listener._handle_other_key()
            self.assertTrue(self._wait_for(events, 'cancel', timeout=1.0))
            self.assertEqual(listener.mode, 'idle')
            # Releasing Fn afterwards must not transcribe.
            listener._handle_fn_up()
            time.sleep(0.05)
            self.assertNotIn('stop', events)
        finally:
            hotkey_fn._PENDING_TIMEOUT = original

    def test_hands_free_survives_typing(self):
        """Hands-free was latched deliberately — typing must not cancel it."""
        listener, events = self._make()
        listener._handle_fn_down()
        listener._handle_space_down()
        self.assertEqual(listener.mode, 'hands_free')
        listener._handle_other_key()
        self.assertEqual(listener.mode, 'hands_free')
        self.assertNotIn('cancel', events)


if __name__ == '__main__':
    unittest.main()


class TapOutageRecoveryTests(unittest.TestCase):
    """A disabled-and-re-enabled event tap must not strand a live gesture.

    macOS disables the tap on callback timeout / certain user input and
    delivers nothing until it is re-enabled. A release inside that window is
    lost, so without an explicit hardware resync the listener stays stuck in
    PUSH_TO_TALK: the clip never stops and every later Fn press is refused as
    "already recording" — dictation dead until relaunch.
    """

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
            on_arm_cb=cb('arm'),
            on_disarm_cb=cb('disarm'),
        )
        return listener, events

    def _wait_for(self, events, name, timeout=1.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if name in events:
                return True
            time.sleep(0.01)
        return False

    def _enter_ptt(self, listener):
        import hotkey_fn

        original = hotkey_fn._PENDING_TIMEOUT
        hotkey_fn._PENDING_TIMEOUT = 0.02
        try:
            listener._handle_fn_down()
            deadline = time.time() + 1.0
            while time.time() < deadline and listener.mode != 'ptt':
                time.sleep(0.01)
        finally:
            hotkey_fn._PENDING_TIMEOUT = original
        self.assertEqual(listener.mode, 'ptt')

    def test_release_missed_during_outage_stops_the_recording(self):
        listener, events = self._make()
        self._enter_ptt(listener)
        self.assertTrue(self._wait_for(events, 'start'))

        # The user let go of Fn while the tap was dead: hardware says "up",
        # but no flagsChanged was ever delivered.
        listener._fn_physically_down = lambda: False
        listener._resync_after_tap_outage()

        self.assertTrue(self._wait_for(events, 'stop'), 'recording was never stopped')
        self.assertEqual(listener.mode, 'idle')
        self.assertFalse(listener.is_recording)

    def test_a_still_held_key_is_left_recording(self):
        listener, events = self._make()
        self._enter_ptt(listener)
        self.assertTrue(self._wait_for(events, 'start'))

        # Fn genuinely still held — the outage was incidental. Stopping here
        # would truncate the user mid-sentence.
        listener._fn_physically_down = lambda: True
        listener._resync_after_tap_outage()

        time.sleep(0.05)
        self.assertNotIn('stop', events)
        self.assertEqual(listener.mode, 'ptt')
        self.assertTrue(listener.is_recording)

    def test_unreadable_hardware_state_changes_nothing(self):
        listener, events = self._make()
        self._enter_ptt(listener)
        self.assertTrue(self._wait_for(events, 'start'))

        # Cannot read the flags → do not guess; a spurious read must never
        # cancel a live recording.
        listener._fn_physically_down = lambda: None
        listener._resync_after_tap_outage()

        time.sleep(0.05)
        self.assertNotIn('stop', events)
        self.assertEqual(listener.mode, 'ptt')

    def test_pending_gesture_is_disarmed_not_recorded(self):
        listener, events = self._make()
        listener._handle_fn_down()          # PENDING — mic armed, not recording
        self.assertEqual(listener.mode, 'pending')

        listener._fn_physically_down = lambda: False
        listener._resync_after_tap_outage()

        self.assertTrue(self._wait_for(events, 'disarm'), 'armed mic was never released')
        self.assertNotIn('start', events)
        self.assertEqual(listener.mode, 'idle')

    def test_idle_listener_is_untouched(self):
        listener, events = self._make()
        listener._fn_physically_down = lambda: False
        listener._resync_after_tap_outage()

        time.sleep(0.05)
        self.assertEqual(events, [])
        self.assertEqual(listener.mode, 'idle')


class TapDisabledEventWiringTests(unittest.TestCase):
    """The recovery must hang off the real tap-disabled event, not just exist.

    `_event_callback` imports Quartz lazily, so a stub in sys.modules is
    enough to drive the disabled branch without a real CGEventTap.
    """

    def _fake_quartz(self):
        import types

        q = types.ModuleType('Quartz')
        q.kCGEventTapDisabledByTimeout = 0xFFFFFFFE
        q.kCGEventTapDisabledByUserInput = 0xFFFFFFFF
        q.kCGEventFlagsChanged = 12
        q.kCGEventKeyDown = 10
        q.enabled = []
        q.CGEventTapEnable = lambda tap, on: q.enabled.append((tap, on))
        return q

    def test_disabled_event_reenables_the_tap_and_resyncs(self):
        import sys

        from hotkey_fn import FnHotkeyListener

        listener = FnHotkeyListener(
            on_start_cb=lambda: None,
            on_stop_cb=lambda: None,
        )
        listener._tap = object()
        calls = []
        listener._resync_after_tap_outage = lambda: calls.append('resync')

        q = self._fake_quartz()
        saved = sys.modules.get('Quartz')
        sys.modules['Quartz'] = q
        try:
            for kind in (q.kCGEventTapDisabledByTimeout, q.kCGEventTapDisabledByUserInput):
                del calls[:]
                del q.enabled[:]
                out = listener._event_callback(None, kind, 'evt', None)
                self.assertEqual(out, 'evt', 'the event must still propagate')
                self.assertEqual(
                    [on for _, on in q.enabled], [True], 'tap was not re-enabled'
                )
                self.assertEqual(calls, ['resync'], 'state was never reconciled')
        finally:
            if saved is not None:
                sys.modules['Quartz'] = saved
            else:
                sys.modules.pop('Quartz', None)


class TapOutageNoiseTests(unittest.TestCase):
    """B6. Our own stop() disables the tap, and macOS answers with the same
    disabled-notice a real outage produces. It must not be logged as one, and
    it must not run the resync against a listener being torn down."""

    def _listener(self):
        from hotkey_fn import FnHotkeyListener

        return FnHotkeyListener(on_start_cb=lambda: None, on_stop_cb=lambda: None)

    def test_a_disable_notice_during_stop_is_ignored(self):
        from unittest.mock import patch

        import Quartz

        listener = self._listener()
        listener._stopping = True
        with patch.object(listener, '_resync_after_tap_outage') as resync, \
                self.assertNoLogs('say.hotkey_fn', level='WARNING'):
            returned = listener._event_callback(
                None, Quartz.kCGEventTapDisabledByUserInput, 'event', None
            )
        self.assertEqual(returned, 'event')
        resync.assert_not_called()

    def test_a_real_outage_still_reconciles(self):
        from unittest.mock import patch

        import Quartz

        listener = self._listener()
        with patch.object(listener, '_resync_after_tap_outage') as resync:
            listener._event_callback(None, Quartz.kCGEventTapDisabledByTimeout, 'event', None)
        resync.assert_called_once()

    def test_start_lowers_the_switch_interval(self):
        import sys

        from hotkey_fn import _LOW_LATENCY_SWITCH_S, apply_low_latency_switch_interval

        saved = sys.getswitchinterval()
        try:
            sys.setswitchinterval(0.005)
            self.assertLessEqual(apply_low_latency_switch_interval(), _LOW_LATENCY_SWITCH_S)
            # Never raises an interval someone already set lower.
            sys.setswitchinterval(0.0005)
            self.assertEqual(apply_low_latency_switch_interval(), 0.0005)
        finally:
            sys.setswitchinterval(saved)



class ChordTests(unittest.TestCase):
    """Fn+V re-inserts, Fn+Z undoes — both fire on Fn *release*, because an
    event posted while Fn is held inherits it and Fn+Delete is forward delete."""

    def _make(self):
        from hotkey_fn import FnHotkeyListener

        events = []

        def cb(name):
            return lambda: events.append(name)

        listener = FnHotkeyListener(
            on_start_cb=cb('start'), on_stop_cb=cb('stop'), on_cancel_cb=cb('cancel'),
            on_disarm_cb=cb('disarm'), on_reinsert_cb=cb('reinsert'), on_undo_cb=cb('undo'),
        )
        return listener, events

    def _settle(self, listener):
        listener._pump.stop(timeout=1.0)

    def test_fn_z_waits_for_release_then_undoes(self):
        import hotkey_fn

        listener, events = self._make()
        listener._handle_fn_down()
        self.assertTrue(listener._handle_chord(hotkey_fn._KEY_Z))
        time.sleep(0.05)
        self.assertNotIn('undo', events)  # not while Fn is held
        listener._handle_fn_up()
        self._settle(listener)
        self.assertIn('undo', events)
        self.assertNotIn('start', events)

    def test_fn_v_reinserts(self):
        import hotkey_fn

        listener, events = self._make()
        listener._handle_fn_down()
        listener._handle_chord(hotkey_fn._KEY_V)
        listener._handle_fn_up()
        self._settle(listener)
        self.assertEqual(events.count('reinsert'), 1)

    def test_a_chord_during_push_to_talk_discards_the_recording_first(self):
        import hotkey_fn

        original = hotkey_fn._PENDING_TIMEOUT
        hotkey_fn._PENDING_TIMEOUT = 0.02
        try:
            listener, events = self._make()
            listener._handle_fn_down()
            time.sleep(0.1)  # now in push-to-talk
            listener._handle_chord(hotkey_fn._KEY_Z)
            listener._handle_fn_up()
            self._settle(listener)
        finally:
            hotkey_fn._PENDING_TIMEOUT = original
        self.assertEqual([e for e in events if e in ('start', 'cancel', 'stop', 'undo')],
                         ['start', 'cancel', 'undo'])

    def test_v_without_fn_is_just_typing(self):
        import hotkey_fn

        listener, events = self._make()
        self.assertFalse(listener._handle_chord(hotkey_fn._KEY_V))
        listener._handle_fn_down()
        listener._handle_fn_up()
        self._settle(listener)
        self.assertNotIn('reinsert', events)

    def test_hands_free_is_left_alone(self):
        import hotkey_fn

        listener, _events = self._make()
        listener._handle_fn_down()
        listener._handle_space_down()  # latch hands-free
        self.assertFalse(listener._handle_chord(hotkey_fn._KEY_V))

    def test_other_letters_are_not_chords(self):
        listener, _events = self._make()
        listener._handle_fn_down()
        self.assertFalse(listener._handle_chord(14))  # E belongs to the emoji picker

    def test_user_inputs_are_counted_not_recorded(self):
        listener, _events = self._make()
        self.assertEqual(listener.user_inputs, 0)
        listener._note_user_input()
        listener._note_user_input()
        self.assertEqual(listener.user_inputs, 2)
