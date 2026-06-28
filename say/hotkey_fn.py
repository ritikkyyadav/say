# hotkey_fn.py — Wispr Flow-style Fn key listener using Quartz CGEventTap.
#
# Two modes:
#   1. Push-to-talk:  Hold Fn → record → release Fn → transcribe
#   2. Hands-free:    Press Fn+Space → recording toggles on → press Fn → stop & transcribe
#
# The Fn key is a hardware-level modifier on macOS. pynput cannot detect it.
# We use Quartz CGEventTap to watch kCGEventFlagsChanged for the
# kCGEventFlagMaskSecondaryFn (0x800000) modifier bit.

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from enum import Enum

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
    ):
        self._on_start = on_start_cb
        self._on_stop = on_stop_cb
        self._on_cancel = on_cancel_cb

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

    @property
    def hotkey_label(self) -> str:
        return 'Fn (hold) / Fn+Space (toggle)'

    def _fire(self, callback: Callable[[], None]) -> None:
        """Fire a callback on a separate thread."""
        threading.Thread(target=callback, daemon=True).start()

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
                # Enter pending mode — wait to see if Space follows
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
                # Fn released quickly without Space → was a short tap
                # Start PTT recording momentarily, then immediately stop
                # Actually: for a very short tap, just ignore it
                self._mode = _Mode.IDLE
                self._cancel_pending_timer()

            elif self._mode == _Mode.PUSH_TO_TALK:
                # Fn released while in push-to-talk → stop recording
                self._mode = _Mode.IDLE
                self._cancel_pending_timer()
                self._stop_recording()

            # Hands-free mode: Fn up is irrelevant (stop is on next Fn down)

    def _handle_space_down(self) -> None:
        """Space key pressed."""
        with self._lock:
            if self._mode == _Mode.PENDING and self._fn_down:
                # Fn+Space → enter hands-free mode
                self._mode = _Mode.HANDS_FREE
                self._cancel_pending_timer()
                self._start_recording()

    def _handle_escape(self) -> None:
        """Escape key pressed — cancel recording."""
        with self._lock:
            if self._mode in (_Mode.PUSH_TO_TALK, _Mode.HANDS_FREE):
                self._mode = _Mode.IDLE
                self._cancel_pending_timer()
                self._cancel_recording()

    def _event_callback(self, proxy, event_type, event, refcon):
        """Quartz CGEventTap callback.

        Returning the event lets it propagate to the system; returning
        None swallows it. We swallow:

          * Fn key state changes — so macOS doesn't fire the
            "Press 🌐 to: Show Emoji & Symbols" / globe-key action when
            the user releases Fn after recording.
          * Space, but only while Fn is held in PENDING mode — so the
            Fn+Space toggle combo doesn't insert a space character into
            the focused app.
          * Escape, but only during active recording — so the user's
            normal Esc keystrokes still work everywhere else.

        macOS may also disable the tap if our callback gets too slow or
        the user grants/revokes permissions. We re-enable in that case
        so the listener is self-healing.
        """
        import Quartz

        FN_FLAG = 0x800000  # kCGEventFlagMaskSecondaryFn

        if event_type in (
            Quartz.kCGEventTapDisabledByTimeout,
            Quartz.kCGEventTapDisabledByUserInput,
        ):
            if self._tap is not None:
                Quartz.CGEventTapEnable(self._tap, True)
                _log.warning('Event tap was disabled (%s); re-enabled.', event_type)
            return event

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
                    swallow = self._mode == _Mode.PENDING and self._fn_down
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
