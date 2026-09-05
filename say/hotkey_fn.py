# hotkey_fn.py — Wispr Flow-style Fn key listener using Quartz CGEventTap.
#
# Two modes:
#   1. Push-to-talk:  Hold Fn → record → release Fn → transcribe
#   2. Hands-free:    Hold Fn + tap Space → recording latches on → release
#                     Fn, talk freely → press Fn again → stop & transcribe
#
# Space latches hands-free at any point while Fn is held — both during the
# brief PENDING window right after Fn AND after Fn has already fallen through
# to push-to-talk — so the deliberate "hold Fn, then tap Space" gesture works
# without demanding a sub-250ms chord (which no human reliably hits).
#
# The Fn key is a hardware-level modifier on macOS. pynput cannot detect it.
# We use Quartz CGEventTap to watch kCGEventFlagsChanged for the
# kCGEventFlagMaskSecondaryFn (0x800000) modifier bit.

from __future__ import annotations

import sys
import threading
import time
from collections.abc import Callable
from enum import Enum

from callback_pump import CallbackPump
from logging_setup import get_logger

_log = get_logger('hotkey_fn')


class _Mode(Enum):
    IDLE = 'idle'
    PENDING = 'pending'       # Fn just pressed, waiting to see if Space follows
    PUSH_TO_TALK = 'ptt'      # Fn held — push-to-talk recording
    HANDS_FREE = 'hands_free' # Fn+Space toggled — hands-free recording


# Timing constants
_PENDING_TIMEOUT = 0.25   # Max seconds to wait for Space after Fn press
_PTT_START_DELAY = 0.08   # Small debounce before starting PTT recording

# kCGEventFlagMaskSecondaryFn — the Fn/globe modifier bit. Module-level because
# both the event callback and the post-outage resync need to test it.
_FN_FLAG = 0x800000

# The interpreter's thread-switch interval while the tap is live. The tap
# callback shares this process with the decoder, and macOS disables a tap
# whose callback does not return promptly (kCGEventTapDisabledByTimeout — 8 in
# the log); the callback itself is microseconds of Python, so what it is
# waiting on is the interpreter lock. The default interval lets a busy thread
# keep the lock for 5 ms at a time; 1 ms hands it over five times as often.
# A mitigation, not the fix — the fix is a helper process that owns the tap
# and nothing else (see PRODUCTION.md, B6).
_LOW_LATENCY_SWITCH_S = 0.001


def apply_low_latency_switch_interval() -> float:
    """Lower the GIL switch interval for the tap's benefit. Returns the value now in effect."""
    current = sys.getswitchinterval()
    if current > _LOW_LATENCY_SWITCH_S:
        sys.setswitchinterval(_LOW_LATENCY_SWITCH_S)
    return sys.getswitchinterval()


class FnHotkeyListener:
    """Wispr Flow-style Fn key listener for macOS.

    - Hold Fn: push-to-talk (record while held, transcribe on release)
    - Fn+Space: hands-free toggle (start recording, press Fn again to stop)
    """

    def __init__(
        self,
        on_start_cb: Callable[[], None],
        on_stop_cb: Callable[[], None],
        on_cancel_cb: Callable[[], None] | None = None,
        on_arm_cb: Callable[[], None] | None = None,
        on_disarm_cb: Callable[[], None] | None = None,
    ):
        self._on_start = on_start_cb
        self._on_stop = on_stop_cb
        self._on_cancel = on_cancel_cb
        # Fired the moment Fn goes down / the gesture is abandoned, so the
        # recorder can open the mic and pre-fill its pre-roll buffer during the
        # tap-vs-hold window instead of losing that audio.
        self._on_arm = on_arm_cb
        self._on_disarm = on_disarm_cb

        self._mode = _Mode.IDLE
        self._fn_down = False
        self._fn_press_time = 0.0
        self._lock = threading.Lock()
        self._tap = None
        self._loop_ref = None
        self._run_loop_source = None
        self._thread: threading.Thread | None = None
        self._pending_timer: threading.Timer | None = None
        self._recording_active = False
        self._loop_started = threading.Event()
        self._tap_creation_failed = False
        # Set for the duration of stop(): disabling our own tap makes macOS
        # deliver a disabled-notice that used to be logged as an outage (56 of
        # the 64 "Event tap was disabled" warnings in the log were this) and
        # ran the resync against a listener that is being torn down.
        self._stopping = False
        # Callbacks run off the tap thread, but in order: a thread per
        # callback let a quick release's `on_stop` overtake its own `on_start`
        # and leave a recording that nothing ever stopped. See callback_pump.
        self._pump = CallbackPump(name='say-fn-callbacks')

    @property
    def hotkey_label(self) -> str:
        return 'Fn (hold) / Fn+Space (toggle)'

    def _fire(self, callback: Callable[[], None]) -> None:
        """Run a callback off the tap thread, after every callback fired before it."""
        self._pump.fire(callback)

    def _start_recording(self) -> None:
        """Begin recording (idempotent)."""
        if not self._recording_active:
            self._recording_active = True
            self._fire(self._on_start)

    def _stop_recording(self) -> None:
        """Stop recording and transcribe (idempotent)."""
        if self._recording_active:
            self._recording_active = False
            self._fire(self._on_stop)

    def _cancel_recording(self) -> None:
        """Cancel recording without transcribing."""
        if self._recording_active:
            self._recording_active = False
            if self._on_cancel:
                self._fire(self._on_cancel)

    def _disarm(self) -> None:
        """Release a mic armed for a gesture that never became a recording."""
        if self._on_disarm is not None:
            self._fire(self._on_disarm)

    def _cancel_pending_timer(self) -> None:
        if self._pending_timer is not None:
            self._pending_timer.cancel()
            self._pending_timer = None

    def _on_pending_timeout(self) -> None:
        """Called when pending timer expires — Fn was held without Space."""
        with self._lock:
            if self._mode == _Mode.PENDING and self._fn_down:
                # Fn still held → switch to push-to-talk
                self._mode = _Mode.PUSH_TO_TALK
                self._start_recording()

    def _handle_fn_down(self) -> None:
        """Fn key pressed."""
        _log.info('Fn down')
        with self._lock:
            self._fn_down = True
            self._fn_press_time = time.time()

            if self._mode == _Mode.IDLE:
                # Enter pending mode — wait to see if Space follows. Arm the
                # mic now so the audio spoken during this window is captured
                # rather than thrown away.
                if self._on_arm is not None:
                    self._fire(self._on_arm)
                self._mode = _Mode.PENDING
                self._cancel_pending_timer()
                self._pending_timer = threading.Timer(
                    _PENDING_TIMEOUT, self._on_pending_timeout
                )
                self._pending_timer.daemon = True
                self._pending_timer.start()

            elif self._mode == _Mode.HANDS_FREE:
                # In hands-free mode, Fn press stops recording
                self._mode = _Mode.IDLE
                self._cancel_pending_timer()
                self._stop_recording()

    def _handle_fn_up(self) -> None:
        """Fn key released."""
        with self._lock:
            self._fn_down = False

            if self._mode == _Mode.PENDING:
                # Fn released quickly without Space → a short tap, not a
                # dictation. Release the mic we armed on key-down.
                self._mode = _Mode.IDLE
                self._cancel_pending_timer()
                self._disarm()

            elif self._mode == _Mode.PUSH_TO_TALK:
                # Fn released while in push-to-talk → stop recording
                self._mode = _Mode.IDLE
                self._cancel_pending_timer()
                self._stop_recording()

            # Hands-free mode: Fn up is irrelevant (stop is on next Fn down)

    def _handle_space_down(self) -> None:
        """Space pressed while Fn is held → latch hands-free mode.

        Accepts Space at any point while Fn is down — both during the brief
        PENDING window right after Fn AND after Fn has already fallen through
        to push-to-talk. This makes the deliberate "hold Fn, then tap Space"
        gesture reliable instead of demanding a sub-250ms chord. If push-to-
        talk was already recording, the same clip continues uninterrupted
        (start is idempotent); we simply stop honoring Fn-release as the stop
        trigger — the user releases Fn, talks freely, and presses Fn again to
        stop.
        """
        with self._lock:
            if self._fn_down and self._mode in (_Mode.PENDING, _Mode.PUSH_TO_TALK):
                self._mode = _Mode.HANDS_FREE
                self._cancel_pending_timer()
                self._start_recording()  # idempotent — no-op if PTT already live

    def _handle_escape(self) -> None:
        """Escape key pressed — cancel recording."""
        with self._lock:
            if self._mode in (_Mode.PUSH_TO_TALK, _Mode.HANDS_FREE):
                self._mode = _Mode.IDLE
                self._cancel_pending_timer()
                self._cancel_recording()

    def _handle_other_key(self) -> None:
        """A key other than Space/Esc was pressed while Fn is held.

        Fn is a modifier before it is a dictation trigger: Fn+arrows are
        Home/End/PageUp/PageDown, Fn+Delete is forward-delete, Fn+F-keys are
        the function row. Without this guard, holding Fn for any of those
        longer than the pending window started a real recording, opened the
        microphone, and on release transcribed whatever was audible — which
        could then be injected into the focused document.

        So any other keystroke means "this was a keyboard shortcut, not a
        dictation": abandon the gesture and discard anything captured. The
        key event itself is never swallowed, so the shortcut still works.

        Hands-free is deliberately exempt — the user latched that on purpose
        and may well type while it runs.
        """
        with self._lock:
            if self._mode == _Mode.PENDING:
                self._mode = _Mode.IDLE
                self._cancel_pending_timer()
                self._disarm()
            elif self._mode == _Mode.PUSH_TO_TALK:
                self._mode = _Mode.IDLE
                self._cancel_pending_timer()
                self._cancel_recording()

    def _fn_physically_down(self) -> bool | None:
        """Is the Fn/globe key held right now, according to the hardware?

        Read straight from the event source rather than from our cached
        `_fn_down`, which is only as fresh as the last event the tap delivered.
        Returns None when the state cannot be read, so callers can tell
        "unknown" apart from "not held" and leave their state alone.
        """
        try:
            import Quartz

            flags = Quartz.CGEventSourceFlagsState(
                Quartz.kCGEventSourceStateCombinedSessionState
            )
        except Exception:  # pragma: no cover - defensive, never break the tap
            return None
        if flags is None:
            return None
        return bool(flags & _FN_FLAG)

    def _resync_after_tap_outage(self) -> None:
        """Reconcile the gesture state after macOS disabled and we re-enabled the tap.

        macOS disables the tap on callback timeout or on certain user input,
        and delivers **nothing** in the meantime. If the user releases Fn
        inside that window the release is lost forever, and the listener stays
        convinced the key is still held: the mode never leaves PENDING /
        PUSH_TO_TALK, the in-flight recording is never stopped, and every
        later Fn press is rejected by the controller as "already recording".
        Dictation is then dead until the app is restarted — which is exactly
        what a tap outage mid-gesture used to cause.

        So after re-enabling, ask the hardware whether Fn is actually still
        down. If it is not, run the normal release path; that stops a
        push-to-talk clip (transcribing what was captured, rather than
        discarding it) or disarms a gesture that never became one. A hardware
        state we cannot read is left alone — guessing here would cancel live
        recordings on a spurious read.
        """
        with self._lock:
            mid_gesture = self._fn_down or self._mode in (
                _Mode.PENDING,
                _Mode.PUSH_TO_TALK,
            )
        if not mid_gesture:
            return

        still_down = self._fn_physically_down()
        if still_down is not False:
            return  # genuinely still held, or unreadable — leave it be

        _log.warning('Fn was released while the event tap was disabled; recovering.')
        self._fn_down = False
        self._handle_fn_up()

    def _event_callback(self, proxy, event_type, event, refcon):
        """Quartz CGEventTap callback.

        Returning the event lets it propagate to the system; returning
        None swallows it. We swallow:

          * Fn key state changes — so macOS doesn't fire the
            "Press 🌐 to: Show Emoji & Symbols" / globe-key action when
            the user releases Fn after recording.
          * Space, but only while Fn is held (PENDING or push-to-talk) —
            so the Fn+Space hands-free latch doesn't insert a space
            character into the focused app.
          * Escape, but only during active recording — so the user's
            normal Esc keystrokes still work everywhere else.

        macOS may also disable the tap if our callback gets too slow or
        the user grants/revokes permissions. We re-enable in that case
        so the listener is self-healing.
        """
        import Quartz

        FN_FLAG = _FN_FLAG

        if event_type in (
            Quartz.kCGEventTapDisabledByTimeout,
            Quartz.kCGEventTapDisabledByUserInput,
        ):
            if self._stopping:
                # Our own stop() disabled the tap. Not an outage: nothing to
                # re-enable and nothing to reconcile.
                return event
            if self._tap is not None:
                Quartz.CGEventTapEnable(self._tap, True)
                _log.warning('Event tap was disabled (%s); re-enabled.', event_type)
            # Re-enabling the tap is only half the recovery: nothing was
            # delivered while it was dead, so a release inside that window was
            # never observed. Reconcile against the hardware before carrying on.
            self._resync_after_tap_outage()
            return event

        # Our own injected keystrokes come back through this tap. They must be
        # passed straight through: treated as user input, each chunk of a
        # transcript being typed out looks like a keystroke, and one arriving
        # while the trigger key is held abandons the gesture — so beginning a
        # new dictation while the previous one is still landing would cancel it.
        try:
            from text_inject_cgevent import is_injected_event

            if is_injected_event(Quartz, event):
                return event
        except Exception:
            pass

        if event_type == Quartz.kCGEventFlagsChanged:
            flags = Quartz.CGEventGetFlags(event)
            fn_now_down = bool(flags & FN_FLAG)
            prev_fn_down = self._fn_down

            if fn_now_down and not prev_fn_down:
                self._handle_fn_down()
            elif not fn_now_down and prev_fn_down:
                self._handle_fn_up()

            # Swallow only when this flag-change actually toggled Fn —
            # other modifier changes (Cmd/Shift/Ctrl/Opt) must pass
            # through or we'd break system-wide modifier handling.
            if fn_now_down != prev_fn_down:
                return None
            return event

        elif event_type == Quartz.kCGEventKeyDown:
            keycode = Quartz.CGEventGetIntegerValueField(
                event, Quartz.kCGKeyboardEventKeycode
            )
            if keycode == 49:  # Space
                with self._lock:
                    swallow = self._fn_down and self._mode in (
                        _Mode.PENDING, _Mode.PUSH_TO_TALK
                    )
                if swallow:
                    self._handle_space_down()
                    return None
                return event
            if keycode == 53:  # Escape
                with self._lock:
                    swallow = self._mode in (_Mode.PUSH_TO_TALK, _Mode.HANDS_FREE)
                if swallow:
                    self._handle_escape()
                    return None
                return event

            # Any other key while Fn is held means the user is doing an
            # ordinary Fn shortcut (Fn+arrows, Fn+Delete, Fn+F-keys), not
            # starting a dictation. Abandon the gesture, but let the key
            # through so the shortcut still does its job.
            with self._lock:
                fn_chord = self._fn_down and self._mode in (
                    _Mode.PENDING, _Mode.PUSH_TO_TALK
                )
            if fn_chord:
                self._handle_other_key()
            return event

        return event

    def _run_tap(self) -> None:
        """Run the event tap on a background thread."""
        import Quartz
        from Foundation import CFRunLoopGetCurrent, CFRunLoopRun

        event_mask = (
            (1 << Quartz.kCGEventFlagsChanged)
            | (1 << Quartz.kCGEventKeyDown)
        )

        # Active session-level tap. The system globe-key action
        # (Show Emoji / Change Input / Start Dictation) on Apple
        # Silicon is dispatched below this layer and cannot be
        # suppressed here even with kCGHIDEventTap; that's handled
        # separately by `fn_override.acquire()` flipping the
        # AppleFnUsageType user default to "Do Nothing" while Say is
        # running. The active mode (not listen-only) is still needed
        # so this callback can swallow Fn / Fn+Space / Esc-during-
        # record events from reaching focused apps.
        self._tap = Quartz.CGEventTapCreate(
            Quartz.kCGSessionEventTap,
            Quartz.kCGHeadInsertEventTap,
            Quartz.kCGEventTapOptionDefault,
            event_mask,
            self._event_callback,
            None,
        )

        if self._tap is None:
            _log.error(
                'Failed to create CGEventTap. Grant Accessibility permission '
                'in System Settings > Privacy & Security > Accessibility.'
            )
            self._tap_creation_failed = True
            self._loop_started.set()
            return

        self._run_loop_source = Quartz.CFMachPortCreateRunLoopSource(
            None, self._tap, 0
        )
        self._loop_ref = CFRunLoopGetCurrent()
        Quartz.CFRunLoopAddSource(
            self._loop_ref, self._run_loop_source, Quartz.kCFRunLoopCommonModes
        )
        Quartz.CGEventTapEnable(self._tap, True)

        _log.info('Fn hotkey listener active (Quartz CGEventTap)')
        self._loop_started.set()
        CFRunLoopRun()
        # Returns when CFRunLoopStop is called from stop().

    def start(self, timeout: float = 2.0) -> bool:
        """Start listening for Fn key events.

        Blocks until the run loop is actually live (so the first hotkey
        events are not missed). Returns True on success, False if the
        event tap could not be created (usually missing Accessibility
        permission) or did not come up within `timeout` seconds.
        """
        if self._thread is not None and self._thread.is_alive():
            return not self._tap_creation_failed

        self._loop_started.clear()
        self._tap_creation_failed = False
        self._stopping = False
        apply_low_latency_switch_interval()
        self._thread = threading.Thread(target=self._run_tap, daemon=True)
        self._thread.start()

        if not self._loop_started.wait(timeout):
            _log.warning('Fn hotkey listener did not start within %.1fs', timeout)
            return False
        return not self._tap_creation_failed

    def join(self) -> None:
        """Block until the listener thread exits."""
        if self._thread is not None:
            self._thread.join()

    def stop(self) -> None:
        """Stop the event tap and listener thread cleanly.

        Idempotent. Disables the tap, removes the run-loop source,
        invalidates the CFMachPort, stops the run loop, and joins
        the thread.
        """
        import Quartz

        self._stopping = True
        tap = self._tap
        loop_ref = self._loop_ref
        source = self._run_loop_source

        if tap is not None:
            Quartz.CGEventTapEnable(tap, False)
        if loop_ref is not None and source is not None:
            try:
                Quartz.CFRunLoopRemoveSource(
                    loop_ref, source, Quartz.kCFRunLoopCommonModes
                )
            except Exception:
                pass
        if tap is not None:
            try:
                Quartz.CFMachPortInvalidate(tap)
            except Exception:
                pass
        if loop_ref is not None:
            try:
                Quartz.CFRunLoopStop(loop_ref)
            except Exception:
                pass

        self._tap = None
        self._loop_ref = None
        self._run_loop_source = None
        self._cancel_pending_timer()

        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        self._thread = None
        self._pump.stop()

    @property
    def is_recording(self) -> bool:
        return self._recording_active

    @property
    def mode(self) -> str:
        return self._mode.value

    def force_idle(self) -> None:
        """Snap the listener to IDLE without firing callbacks.

        Used when recording is ended out-of-band of the hotkey state
        machine — e.g. the overlay's cancel/finish buttons, or the
        controller transitioning to ERROR. Idempotent.
        """
        with self._lock:
            self._mode = _Mode.IDLE
            self._recording_active = False
            self._cancel_pending_timer()

    def current_state(self) -> str:
        """Return the current listener mode value ('idle' / 'pending' / 'ptt' / 'hands_free')."""
        with self._lock:
            return self._mode.value
