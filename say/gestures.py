"""The seam between the hotkey listener and everything it drives.

Both entry points — the menu-bar `SayApp` and the shipped `--no-menubar`
sidecar — used to hand the listener five ad-hoc closures apiece, and both got
the same thing wrong in the same way: the trigger key going down showed the
glyph and opened the microphone *unconditionally*, before anyone had asked
whether the engine could accept a recording at all.

That is how "the overlay disappears while I am talking" happened. With a
Whisper model active, a long dictation can spend a minute in PROCESSING
(streaming segments still decoding, then injection), and someone who wants to
keep dictating presses Fn again inside that window. The controller refuses the
recording — measured on the shipped engine's own log: 58 refusals, every one
during PROCESSING — but nothing told the glyph or the microphone. So the mark
went up, followed the user's voice as though it were listening, and was then
torn down by the *previous* job's READY event, mid-sentence; the words were
never captured; and because the refused path never called `disarm()`, the mic
stream stayed open with the system indicator lit.

The rule is now in one place: a gesture only touches the glyph and the mic if
the engine can take a recording, and a refused start retracts both. The
controller owns the "can I record" predicate (`can_start_recording`), so this
module cannot drift from the state machine it is fronting.

Pure Python: no AppKit, no threads of its own. Every method runs on whatever
thread the listener fires it from, and nothing here may raise back into that
thread — the listener's `_fire` gives each callback a fresh thread with no one
to catch it.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from controller import DictationEvent, DictationState
from logging_setup import get_logger

_log = get_logger('gestures')

OverlayGetter = Callable[[], Any]
ListenerGetter = Callable[[], Any]


class GestureBridge:
    """Route hotkey gestures to the recorder, the controller and the glyph.

    `overlay` and `listener` are getters rather than objects because both are
    late-bound: the overlay needs a Cocoa run loop that does not exist when
    the hotkey is wired, and the listener is only known once `make_listener`
    returns — which needs these callbacks first.
    """

    def __init__(
        self,
        controller: Any,
        recorder: Any,
        *,
        overlay: OverlayGetter | None = None,
        listener: ListenerGetter | None = None,
    ) -> None:
        self._controller = controller
        self._recorder = recorder
        self._overlay = overlay or (lambda: None)
        self._listener = listener or (lambda: None)
        self._warned: set[str] = set()

    # ── the glyph ────────────────────────────────────────────────

    def overlay_do(self, action: str, *args: Any) -> None:
        """Call one overlay method, if there is an overlay, and never raise.

        Warning, not debug, and once per action: the overlay is the only
        feedback the user gets that dictation is live, and it fails silently
        by design so a broken glyph can never stop a recording. A dead overlay
        would otherwise leave no trace at the engine's INFO level — which is
        exactly how one (the window sitting at the wrong level) went unnoticed.
        """
        overlay = self._overlay()
        if overlay is None:
            return
        try:
            getattr(overlay, action)(*args)
        except Exception:
            if action not in self._warned:
                self._warned.add(action)
                _log.warning(
                    'Overlay %s failed; the glyph will not appear', action, exc_info=True
                )

    # ── listener callbacks ───────────────────────────────────────

    def on_arm(self) -> None:
        """Trigger key down.

        Overlay first: `recorder.arm()` can block for seconds while CoreAudio
        finishes releasing the device, and the glyph's whole promise is that it
        is on screen by the time the key finishes travelling.

        Gated on the engine: if a recording cannot start right now, neither the
        glyph nor the microphone should pretend otherwise. Left alone, the mark
        would go up, respond to the voice, and be dismissed by the busy job's
        completion event — the user talks into nothing and then watches the
        glyph vanish. The mic stays shut for the same reason: the refused start
        would never have closed it.
        """
        if not self._can_record():
            _log.info(
                'Trigger key pressed while the engine is %s — nothing armed until it is ready',
                self._state_name(),
            )
            return
        self.overlay_do('arm')
        self._safe(self._recorder.arm, 'recorder.arm')

    def on_disarm(self) -> None:
        """The gesture turned out to be a tap or an ordinary shortcut."""
        self.overlay_do('release_arm')
        self._safe(self._recorder.disarm, 'recorder.disarm')

    def on_start(self) -> None:
        """The gesture resolved into a recording — or tried to."""
        if self._safe(lambda: self._controller.start_recording(mode=self._mode()), 'start'):
            return
        # Refused (engine busy) or failed (the controller already emitted the
        # ERROR). Either way the gesture is over: take back whatever the arm
        # put up, close the mic the arm opened, and let the listener forget
        # the gesture so the next key press starts clean instead of being
        # swallowed as the "stop" of a recording that never began.
        self.overlay_do('release_arm')
        self._safe(self._recorder.disarm, 'recorder.disarm')
        listener = self._listener()
        force_idle = getattr(listener, 'force_idle', None)
        if callable(force_idle):
            self._safe(force_idle, 'listener.force_idle')

    def on_stop(self) -> None:
        self._safe(self._controller.stop_recording, 'stop')

    def on_cancel(self) -> None:
        self._safe(self._controller.cancel_recording, 'cancel')

    # ── controller events ────────────────────────────────────────

    def on_status(self, event: DictationEvent) -> None:
        """Mirror the state machine onto the glyph."""
        if event.state == DictationState.RECORDING:
            self.overlay_do('listen', event.mode or 'ptt')
        elif event.state == DictationState.PROCESSING:
            self.overlay_do('process')
        else:
            self.overlay_do('dismiss')

    # ── internals ────────────────────────────────────────────────

    def _can_record(self) -> bool:
        check = getattr(self._controller, 'can_start_recording', None)
        if not callable(check):
            return True
        try:
            return bool(check())
        except Exception:
            _log.debug('can_start_recording raised; assuming ready', exc_info=True)
            return True

    def _state_name(self) -> str:
        state = getattr(self._controller, 'state', None)
        return getattr(state, 'value', str(state))

    def _mode(self) -> str:
        listener = self._listener()
        mode = getattr(listener, 'mode', None) if listener is not None else None
        return str(mode) if mode else 'ptt'

    @staticmethod
    def _safe(fn: Callable[[], Any], what: str) -> Any:
        try:
            return fn()
        except Exception:
            _log.exception('%s raised inside a hotkey callback', what)
            return None
