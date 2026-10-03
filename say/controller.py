# controller.py - Shared dictation state machine.

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

import config
import recall
from injector import InjectionResult, inject
from logging_setup import get_logger
from metrics import get_ledger
from notifications import notify_error, notify_transcription_complete
from server.events import get_bus
from server.store import get_frontmost_app, get_store

_log = get_logger('controller')

# Below this much callback-delivered audio, a recording is treated as having
# captured nothing at all. One 1024-frame block at 16 kHz is 64 ms, so this is
# a couple of blocks — enough to clear scheduling jitter on a healthy stream
# while still catching one that never started.
_MIN_LIVE_SECONDS = 0.15

# Every key press ends in exactly one of these, written to the metrics ledger
# as {"event": "press", "outcome": ...}. Without it "how often does pressing the
# key give me text" was an impression; tools/scorecard.py turns it into the
# reliability score. Only `delivered` is the press doing its job; `held`,
# `silence`, `too_short` and the cancels are honest non-deliveries with a
# reason, the `error:*` ones are failures.
PRESS_OUTCOMES = frozenset({
    'delivered', 'held', 'silence', 'nothing_to_insert', 'too_short',
    'cancelled', 'cancelled_by_system', 'refused_busy', 'no_live_audio',
    'error:mic_start', 'error:mic_stop', 'error:transcription',
    'error:injection', 'error:audio_stream', 'error:device_changed',
})


def record_press(outcome: str, **fields: Any) -> None:
    """Append one press outcome to the ledger. Never raises."""
    if outcome not in PRESS_OUTCOMES:
        _log.warning('Unknown press outcome %r recorded anyway', outcome)
    get_ledger().record(event='press', outcome=outcome, **fields)


class DictationState(str, Enum):
    IDLE = 'idle'
    LOADING = 'loading'
    READY = 'ready'
    RECORDING = 'recording'
    PROCESSING = 'processing'
    ERROR = 'error'


@dataclass(frozen=True)
class DictationEvent:
    state: DictationState
    message: str
    preview: str | None = None
    error: str | None = None
    mode: str | None = None   # 'ptt' or 'hands_free'
    # (decoded, queued) segments while PROCESSING, so a long wait can be
    # shown as "3 of 7" rather than an unexplained spinner. None otherwise.
    progress: tuple[int, int] | None = None


StatusCallback = Callable[[DictationEvent], None]
Logger = Callable[[str], None]
Injector = Callable[[str], InjectionResult]


def focus_policy(started_in: str | None, now_in: str | None) -> str:
    """Should the transcript be typed, given where the recording began and
    where the keyboard focus is now? Returns 'inject' or 'hold'.

    The keystrokes go wherever focus is at the instant transcription ends,
    and with a large Whisper model that is 2 to 44 seconds after the key was
    released — long enough to switch to Slack. Typing a dictation meant for
    one document into another window is the worst thing this engine can do
    with a correct transcript, so a moved focus holds the text instead:
    clipboard, history, a notification saying where it went.

    Unknown on either side means the old behaviour: no AppKit, or an app with
    no bundle identifier, is not evidence that focus moved.
    """
    if not started_in or not now_in:
        return 'inject'
    return 'inject' if started_in == now_in else 'hold'


def preview_text(text: str, limit: int = 80) -> str:
    """Return a short, non-persistent transcript preview."""
    clean = ' '.join(text.split())
    if len(clean) <= limit:
        return clean
    return clean[: limit - 3] + '...'


class DictationController:
    """Coordinates audio capture, local inference, and text injection."""

    def __init__(
        self,
        recorder: Any,
        transcriber: Any,
        injector: Injector = inject,
        status_callback: StatusCallback | None = None,
        logger: Logger | None = print,
        backspacer: Callable[[int], Any] | None = None,
    ):
        self.recorder = recorder
        self.transcriber = transcriber
        # Presses Backspace n times, for undo. Injectable so tests never post
        # real key events.
        self._backspacer = backspacer
        self.injector = injector
        self.status_callback = status_callback
        self.logger = logger
        self.state = DictationState.IDLE
        self._recording = False
        self._recording_mode: str | None = None
        # The app that had focus when the recording began — where the words
        # were meant to go. Compared against the focus at injection time.
        self._target_app: str | None = None
        self._lock = threading.RLock()
        # Streaming transcription session for the current recording, if any.
        self._session: Any = None
        # Correction read-back watcher, built on first use (see readback.py).
        self._corrections: Any = None
        # The press in flight, so exactly one outcome is recorded for it no
        # matter how many exit paths it brushes past (see `_close_press`).
        self._press_open = False
        self._press_mode: str | None = None
        # What Say last typed, for undo (recall.py). Memory only, never stored.
        self._last_delivery: recall.Delivery | None = None
        # Key presses + clicks seen by the Fn tap; None when no tap can see them.
        self._input_source: Callable[[], int | None] = lambda: None
        # Drops the model after MODEL_IDLE_UNLOAD_S of quiet (transcriber.unload_idle).
        self._idle_timer: threading.Timer | None = None

        set_event = getattr(recorder, 'set_event_callback', None)
        if callable(set_event):
            set_event(self._on_audio_event)

    def _on_audio_event(self, event_type: str, message: str | None) -> None:
        """Handle out-of-band events from the audio recorder / device watcher.

        Runs on a fresh daemon thread, so it is safe to re-enter the
        recorder (stop/cancel/shutdown) here.
        """
        if event_type == 'max_duration':
            with self._lock:
                if not self._recording:
                    return
            if self.logger is not None:
                self.logger(f'[recording] {message or "Max duration reached"}')
            # Finalize the recording the same way the user releasing Fn would.
            self.stop_recording()
            return

        if event_type in ('stream_error', 'device_changed'):
            was_recording = False
            with self._lock:
                if self._recording:
                    was_recording = True
                    self._recording = False
                    self._recording_mode = None

            # Hard-reset the audio stream so the next recording reopens
            # against the current default device.
            self._reset_recorder()

            if not was_recording:
                if self.logger is not None:
                    self.logger(f'[audio] {message or event_type} (idle)')
                if event_type == 'stream_error':
                    notify_error(message or 'Audio stream error')
                return

            error_text = message or (
                'Audio stream error'
                if event_type == 'stream_error'
                else 'Input device changed'
            )
            self._close_press(
                'error:audio_stream' if event_type == 'stream_error' else 'error:device_changed'
            )
            self._emit(DictationState.ERROR, error_text, error=error_text)
            notify_error(error_text)

    def _reset_recorder(self) -> None:
        """Hard-reset the audio stream — prefer shutdown(), fall back to stop()."""
        try:
            shutdown = getattr(self.recorder, 'shutdown', None)
            if callable(shutdown):
                shutdown()
                return
        except Exception:
            pass
        try:
            cancel = getattr(self.recorder, 'cancel', None)
            if callable(cancel):
                cancel()
                return
        except Exception:
            pass
        try:
            self.recorder.stop()
        except Exception:
            pass

    def set_input_source(self, source: Callable[[], int | None]) -> None:
        """Where to read the user's key-press/click count from (the Fn tap)."""
        self._input_source = source

    def _inputs(self) -> int | None:
        try:
            value = self._input_source()
        except Exception:
            return None
        return value if isinstance(value, int) else None

    def _remember_delivery(self, transcript_id: int | None, text: str, app: str | None) -> None:
        self._last_delivery = recall.Delivery(
            transcript_id=transcript_id, text=text, target_app=app,
            at=time.monotonic(), keystrokes=self._inputs(),
        )

    def _close_press(self, outcome: str, **fields: Any) -> None:
        """Record how the press in flight ended — once, whichever path gets here first."""
        with self._lock:
            if not self._press_open:
                return
            self._press_open = False
            mode = self._press_mode
        record_press(outcome, mode=mode, **fields)

    def _emit(
        self,
        state: DictationState,
        message: str,
        preview: str | None = None,
        error: str | None = None,
        mode: str | None = None,
        progress: tuple[int, int] | None = None,
    ) -> None:
        with self._lock:
            self.state = state
        event = DictationEvent(state, message, preview, error, mode, progress)
        if self.logger is not None:
            self.logger(f'[{state.value}] {message}')
        if self.status_callback is not None:
            self.status_callback(event)
        # Mirror onto the in-process event bus for the desktop UI's SSE
        # stream. Best-effort; never blocks the dictation pipeline.
        try:
            get_bus().publish_threadsafe({
                'state': state.value,
                'message': message,
                'mode': mode,
                'error': error,
                'progress': (
                    {'done': int(progress[0]), 'total': int(progress[1])}
                    if progress is not None else None
                ),
            })
        except Exception:
            pass

    def _on_stream_progress(self, done: int, total: int) -> None:
        """A segment finished decoding. Only worth announcing once the user is
        waiting — while they are still talking there is nothing to wait for."""
        with self._lock:
            waiting = self.state == DictationState.PROCESSING
        if waiting:
            self._emit(
                DictationState.PROCESSING,
                f'Transcribing… {done} of {total}',
                progress=(done, total),
            )

    def load_model(self) -> bool:
        """Load the transcription model before accepting hotkeys."""
        self._emit(DictationState.LOADING, f'Loading model: {config.MODEL}')
        try:
            load = getattr(self.transcriber, 'load', None)
            if callable(load):
                load()
            self._emit(DictationState.READY, 'Ready — Fn to dictate')
            # An engine launched and never used should not hold its model forever.
            self._schedule_idle_unload()
            return True
        except Exception as exc:
            message = f'Model load failed: {exc}'
            _log.exception('Model load failed')
            self._emit(DictationState.ERROR, message, error=str(exc))
            notify_error(message)
            return False

    def _can_start_locked(self) -> bool:
        """The one rule for whether a recording may begin. Caller holds the lock."""
        return not self._recording and self.state not in {
            DictationState.LOADING,
            DictationState.RECORDING,
            DictationState.PROCESSING,
        }

    def can_start_recording(self) -> bool:
        """Would `start_recording()` accept a recording right now?

        Exposed so the hotkey wiring can ask *before* it shows the glyph and
        opens the microphone on key-down. Without this the mark went up and
        followed the user's voice during PROCESSING, only to be torn down by
        the busy job's completion event mid-sentence — and the refused start
        never closed the mic it had armed. The answer is advisory (the state
        can change between the check and the call), which is why
        `start_recording` re-checks under the lock rather than trusting it.
        """
        with self._lock:
            return self._can_start_locked()

    def start_recording(self, mode: str = 'ptt') -> bool:
        """Start recording if the app is ready for push-to-talk.

        Args:
            mode: 'ptt' for push-to-talk, 'hands_free' for toggle mode.
        """
        _log.info('start_recording called (mode=%s, current_state=%s)', mode, self.state)
        error_for_notify: str | None = None
        refused_in: str | None = None
        with self._lock:
            if not self._can_start_locked():
                _log.info('start_recording rejected — already in %s', self.state)
                refused_in = self.state.value
            else:
                self._cancel_idle_unload()
                self._recording = True
                self._recording_mode = mode
                self._press_open = True
                self._press_mode = mode
                # Captured now, not after transcription: this is the document
                # the user was looking at when they pressed the key.
                self._target_app = get_frontmost_app()
                try:
                    self._begin_streaming()
                    self.recorder.start()
                    _log.info('AudioRecorder.start() returned — mic stream open')
                except Exception as exc:
                    self._recording = False
                    self._recording_mode = None
                    self._end_streaming(cancel=True)
                    error_for_notify = f'Microphone start failed: {exc}'
                    _log.exception('AudioRecorder.start() raised')
                    self._emit(DictationState.ERROR, error_for_notify, error=str(exc))

        if refused_in is not None:
            # Recorded outside the lock: the ledger write is file I/O.
            record_press('refused_busy', mode=mode, state=refused_in)
            return False

        if error_for_notify is not None:
            self._close_press('error:mic_start')
            notify_error(error_for_notify)
            return False

        mode_label = 'Hands-free' if mode == 'hands_free' else 'Push-to-talk'
        self._emit(
            DictationState.RECORDING,
            f'Recording ({mode_label})...',
            mode=mode,
        )
        return True

    def stop_recording(self) -> bool:
        """Stop recording, transcribe locally, and inject the result."""
        try:
            return self._stop_and_deliver()
        finally:
            self._after_dictation()

    def _after_dictation(self) -> None:
        """Memory housekeeping once the words have landed, off anyone's wait."""
        release = getattr(self.transcriber, 'release_cache', None)
        if callable(release):
            try:
                release()
            except Exception:
                _log.debug('release_cache raised', exc_info=True)
        self._schedule_idle_unload()

    def _cancel_idle_unload(self) -> None:
        timer, self._idle_timer = self._idle_timer, None
        if timer is not None:
            timer.cancel()

    def _schedule_idle_unload(self) -> None:
        self._cancel_idle_unload()
        delay = float(getattr(config, 'MODEL_IDLE_UNLOAD_S', 0) or 0)
        if delay <= 0 or not callable(getattr(self.transcriber, 'unload_idle', None)):
            return
        timer = threading.Timer(delay, self._unload_if_idle)
        timer.daemon = True
        self._idle_timer = timer
        timer.start()

    def _unload_if_idle(self) -> None:
        if self._busy() is not None:
            self._schedule_idle_unload()  # someone is dictating; look again later
            return
        try:
            self.transcriber.unload_idle()
        except Exception:
            _log.debug('unload_idle raised', exc_info=True)

    def _stop_and_deliver(self) -> bool:
        with self._lock:
            if not self._recording:
                return False
            self._recording = False
            self._recording_mode = None

        self._emit(DictationState.PROCESSING, 'Processing audio...')

        try:
            audio = self.recorder.stop()
        except Exception as exc:
            message = f'Microphone stop failed: {exc}'
            self._close_press('error:mic_stop')
            self._emit(DictationState.ERROR, message, error=str(exc))
            notify_error(message)
            self._end_streaming(cancel=True)
            return False

        duration = self._duration(audio)
        if duration < config.MIN_AUDIO_SECONDS:
            # Nothing worth decoding — but the streaming session still owns a
            # worker thread and the recorder's segment callback, so tear it down
            # explicitly rather than leaking one per discarded tap.
            self._end_streaming(cancel=True)
            self._close_press('too_short', audio_seconds=round(duration, 3))
            self._emit(DictationState.READY, f'Ready — clip too short ({duration:.1f}s)')
            return True

        # A clip made entirely of pre-roll means the microphone stream never
        # delivered a block for this recording — it was still opening, or it
        # died. The audio is real but it is the half-second that preceded the
        # hotkey, not what the user said, so decoding it produces confident
        # nonsense: handed a long style-seed prompt and no acoustic evidence,
        # the decoder loops on a single token until it hits its cap ("available
        # available available…" — 222 words out of 0.6 s). Fail loudly instead;
        # a visible error the user can retry beats silent garbage in their
        # document.
        live_seconds = getattr(self.recorder, 'live_seconds', None)
        if live_seconds is not None and live_seconds < _MIN_LIVE_SECONDS:
            self._end_streaming(cancel=True)
            message = (
                'Microphone delivered no audio — the input stream was still '
                'opening or unavailable. Nothing was transcribed; please try '
                'again.'
            )
            _log.error(
                'Pre-roll-only clip: %.2fs total but only %.3fs live audio — '
                'refusing to transcribe',
                duration, live_seconds,
            )
            self._close_press('no_live_audio', audio_seconds=round(duration, 3))
            self._emit(DictationState.ERROR, message, error='no_live_audio')
            notify_error(message)
            return False

        session = self._session
        self._emit(
            DictationState.PROCESSING,
            f'Transcribing {duration:.1f}s locally...',
            progress=getattr(session, 'progress', None) if session is not None else None,
        )
        t0 = time.time()
        try:
            text = self._transcribe(audio)
        except Exception as exc:
            message = f'Transcription failed: {exc}'
            self._close_press('error:transcription', audio_seconds=round(duration, 3))
            self._emit(DictationState.ERROR, message, error=str(exc))
            notify_error(message)
            self._end_streaming(cancel=True)
            return False

        # In streaming mode this measures only what the user actually waited
        # for after releasing the key — every earlier segment was decoded while
        # they were still talking.
        inference_seconds = time.time() - t0
        if not text:
            self._close_press(
                'silence', audio_seconds=round(duration, 3),
                wait_seconds=round(inference_seconds, 3),
            )
            self._emit(
                DictationState.READY,
                f'Ready — no speech detected ({inference_seconds:.1f}s inference)',
            )
            return True

        # Where the recording began is where the words were meant to go; where
        # focus is now decides whether typing them is still safe.
        started_in = self._target_app
        now_in = get_frontmost_app()
        target_app = started_in or now_in
        decision = focus_policy(started_in, now_in)

        raw_asr = text
        try:
            from provenance import snapshot

            pipeline = snapshot()
        except Exception:
            _log.debug('Could not capture pipeline provenance', exc_info=True)
            pipeline = {}
        text = self._postprocess(text, target_app)
        if not text:
            self._close_press(
                'nothing_to_insert', audio_seconds=round(duration, 3),
                wait_seconds=round(inference_seconds, 3),
            )
            self._emit(DictationState.READY, 'Ready — nothing to insert')
            return True

        transcript_preview = preview_text(text)
        self._emit(
            DictationState.PROCESSING,
            f'Injecting text ({inference_seconds:.1f}s inference)...',
            preview=transcript_preview,
        )

        # Persist BEFORE injecting. Injection is the step most likely to fail
        # (secure input fields, apps that reject synthetic events, a revoked
        # Accessibility grant), and it used to fail by throwing the user's words
        # away entirely — the store write lived inside the success branch. Now
        # the transcript is durable the moment it exists, and the row is updated
        # with whatever actually happened.
        store = None
        transcript_id = None
        try:
            store = get_store()
            transcript_id = store.insert(
                text=text,
                audio_seconds=duration,
                inference_seconds=inference_seconds,
                model=config.MODEL,
                inject_method='pending',
                target_app=target_app,
                success=False,
                raw_asr=raw_asr,
                observation_status='pending',
                **pipeline,
            )
        except Exception:
            _log.exception('Failed to persist transcript before injection')

        if decision == 'hold':
            return self._hold_transcript(
                store, transcript_id, text, audio, transcript_preview,
                started_in, now_in, duration, inference_seconds,
            )

        result = self.injector(text)

        # Arm the read-back before anything else touches disk: the snapshot is
        # only valid while the field still holds exactly what we typed.
        if result.success and store is not None and transcript_id:
            self._observe_correction(store, transcript_id, text, target_app)

        if store is not None and transcript_id:
            try:
                store.set_outcome(
                    transcript_id, success=result.success, inject_method=result.method
                )
                # Save the audio receipt regardless of whether injection landed.
                # The clip is a record of what was said; a failed insert is
                # exactly when you most want to hear it back.
                self._save_recording(store, transcript_id, audio)
            except Exception:
                _log.exception('Failed to record injection outcome')

        if result.success:
            self._remember_delivery(transcript_id, text, target_app)
            self._close_press(
                'delivered', audio_seconds=round(duration, 3),
                wait_seconds=round(inference_seconds, 3), words=len(text.split()),
                method=result.method, target_app=target_app,
            )
            self._emit(
                DictationState.READY,
                f'Inserted via {result.method}: {transcript_preview}',
                preview=transcript_preview,
            )
            notify_transcription_complete(transcript_preview)
            get_ledger().record(
                event='transcription',
                audio_seconds=round(duration, 3),
                inference_seconds=round(inference_seconds, 3),
                model=config.MODEL,
                method=result.method,
                success=True,
            )
            return True

        # Injection failed. The text is already in history; put it on the
        # clipboard too so recovering it is one ⌘V rather than a trip to the
        # Home tab.
        recovered = self._copy_to_clipboard(text)
        self._close_press(
            'error:injection', audio_seconds=round(duration, 3),
            wait_seconds=round(inference_seconds, 3), method=result.method,
            target_app=target_app, recovered_to_clipboard=recovered,
        )
        detail = 'copied to clipboard' if recovered else 'saved to history'
        message = f'Injection failed ({result.error or "unknown error"}) — {detail}'
        self._emit(
            DictationState.ERROR,
            message,
            preview=transcript_preview,
            error=result.error,
        )
        notify_error(message)
        get_ledger().record(
            event='error',
            stage='injection',
            method=result.method,
            audio_seconds=round(duration, 3),
            inference_seconds=round(inference_seconds, 3),
            model=config.MODEL,
            message=str(result.error or 'unknown'),
            recovered_to_clipboard=recovered,
            success=False,
        )
        return False

    def _hold_transcript(
        self, store, transcript_id, text, audio, transcript_preview,
        started_in, now_in, duration, inference_seconds,
    ) -> bool:
        """Focus moved while we were transcribing: keep the words, do not type.

        The transcript is already in history; putting it on the clipboard too
        makes recovery one paste. Recorded as `held`, not as a failure of the
        injector — nothing broke, the user simply went somewhere else.
        """
        recovered = self._copy_to_clipboard(text)
        self._close_press(
            'held', audio_seconds=round(duration, 3),
            wait_seconds=round(inference_seconds, 3), recovered_to_clipboard=recovered,
        )
        if store is not None and transcript_id:
            try:
                store.set_outcome(transcript_id, success=False, inject_method='held')
                self._save_recording(store, transcript_id, audio)
            except Exception:
                _log.exception('Failed to record the held transcript')
        where = now_in or 'another app'
        detail = 'copied to the clipboard' if recovered else 'saved to history'
        message = f'Focus moved to {where} — transcript {detail}'
        _log.info('Held transcript: began in %s, focus now in %s', started_in, now_in)
        self._emit(DictationState.READY, message, preview=transcript_preview)
        notify_error(message)
        get_ledger().record(
            event='held',
            stage='injection',
            audio_seconds=round(duration, 3),
            inference_seconds=round(inference_seconds, 3),
            model=config.MODEL,
            started_in=started_in,
            now_in=now_in,
            recovered_to_clipboard=recovered,
            success=False,
        )
        return True

    @staticmethod
    def _copy_to_clipboard(text: str) -> bool:
        """Last-resort recovery path for text we could not inject."""
        try:
            from AppKit import NSPasteboard, NSPasteboardTypeString

            pb = NSPasteboard.generalPasteboard()
            pb.clearContents()
            return bool(pb.setString_forType_(text, NSPasteboardTypeString))
        except Exception:
            _log.exception('Could not copy the failed transcript to the clipboard')
            return False

    # ── getting the last dictation back (recall.py) ──────────────

    def _busy(self) -> str | None:
        with self._lock:
            if self._recording or self.state in (
                DictationState.LOADING, DictationState.RECORDING, DictationState.PROCESSING,
            ):
                return self.state.value
        return None

    def _latest_transcript(self) -> tuple[int | None, str]:
        """The newest dictation, delivered or not, in the user's version if they fixed it."""
        try:
            store = get_store()
            rows = store.list(limit=1)
            if rows:
                row = rows[0]
                fixed = store.manual_corrections().get(row.id)
                return row.id, fixed or row.text
        except Exception:
            _log.debug('Could not read the latest transcript', exc_info=True)
        last = self._last_delivery
        return (last.transcript_id, last.text) if last else (None, '')

    def reinsert_last(self) -> bool:
        """Type the most recent dictation again, into whatever has focus now.

        The recovery for every way a dictation can go missing after Say has
        it: held because focus moved, failed to inject, cleared by mistake.
        """
        busy = self._busy()
        if busy is not None:
            get_ledger().record(event='recall', action='reinsert', outcome='refused_busy', state=busy)
            return False
        transcript_id, text = self._latest_transcript()
        if not text:
            notify_error('Nothing to re-insert yet.')
            get_ledger().record(event='recall', action='reinsert', outcome='nothing')
            return False
        app = get_frontmost_app()
        result = self.injector(text)
        if result.success:
            self._remember_delivery(transcript_id, text, app)
            get_ledger().record(event='recall', action='reinsert', outcome='delivered',
                                words=len(text.split()), method=result.method)
            return True
        recovered = self._copy_to_clipboard(text)
        notify_error(
            f'Re-insert failed ({result.error or "unknown error"})'
            + (' — copied to the clipboard' if recovered else '')
        )
        get_ledger().record(event='recall', action='reinsert', outcome='error:injection',
                            recovered_to_clipboard=recovered)
        return False

    def undo_last(self) -> bool:
        """Delete what Say just typed — only when that is provably safe.

        See recall.undo_refusal for the rule. Any doubt refuses and says why:
        Backspaces sent to the wrong place delete the user's own text.
        """
        busy = self._busy()
        if busy is not None:
            get_ledger().record(event='recall', action='undo', outcome='refused_busy', state=busy)
            return False
        last = self._last_delivery
        reason = recall.undo_refusal(
            last, now=time.monotonic(), frontmost=get_frontmost_app(), keystrokes=self._inputs(),
        )
        if reason is not None or last is None:
            notify_error(reason or 'Nothing to undo.')
            get_ledger().record(event='recall', action='undo', outcome='refused', reason=reason)
            return False
        count = recall.backspaces_for(last.text) or 0
        backspacer = self._backspacer
        if backspacer is None:
            from text_inject_cgevent import delete_backward

            backspacer = delete_backward
        result = backspacer(count)
        if getattr(result, 'success', False):
            self._last_delivery = None
            get_ledger().record(event='recall', action='undo', outcome='done', chars=count)
            return True
        notify_error(f'Undo failed ({getattr(result, "error", None) or "unknown error"}).')
        get_ledger().record(event='recall', action='undo', outcome='error')
        return False

    def cancel_recording(self, reason: str = 'user') -> bool:
        """Cancel the current recording without transcribing.

        `reason` is 'user' (Esc, another key) or 'system' (sleep, screen lock),
        so the ledger can tell a person changing their mind from the machine
        taking the recording away.
        """
        with self._lock:
            if not self._recording:
                return False
            self._recording = False
            self._recording_mode = None

        self._close_press('cancelled_by_system' if reason == 'system' else 'cancelled')
        self._end_streaming(cancel=True)
        try:
            cancel = getattr(self.recorder, 'cancel', None)
            if callable(cancel):
                cancel()
            else:
                self.recorder.stop()  # Fallback: stop and discard
        except Exception as exc:
            message = f'Cancel failed: {exc}'
            self._emit(DictationState.ERROR, message, error=str(exc))
            return False

        self._emit(DictationState.READY, 'Ready — recording cancelled')
        return True

    # ── streaming transcription ──────────────────────────────────

    def _streaming_enabled(self) -> bool:
        if not getattr(config, 'STREAMING_TRANSCRIPTION', True):
            return False
        return callable(getattr(self.recorder, 'set_segment_callback', None))

    def _begin_streaming(self) -> None:
        """Start decoding segments in the background as the user speaks.

        Without this, nothing happens until the key is released and the whole
        clip is decoded at once — so the wait grows with how long you talked
        (p90 was 17.7 s on real usage). With it, only the final segment is
        outstanding at release, which makes the wait roughly constant.

        Best-effort: any failure here silently falls back to whole-clip
        transcription rather than breaking dictation.
        """
        self._session = None
        if not self._streaming_enabled():
            return
        try:
            import postprocess
            import streaming

            session = streaming.SegmentedSession(
                self.transcriber,
                base_prompt=postprocess.asr_prompt(),
                # Terms only, for a tail too short to carry a style seed — the
                # same rule whole-clip mode applies (see streaming.SHORT_SEGMENT_SECONDS).
                short_prompt=postprocess.asr_prompt(include_style_seed=False),
                on_progress=self._on_stream_progress,
            )
            self.recorder.set_segment_callback(session.push)
            self._session = session
        except Exception:
            _log.exception('Could not start streaming session; using whole-clip mode')
            self._session = None
            try:
                self.recorder.set_segment_callback(None)
            except Exception:
                pass

    def _end_streaming(self, cancel: bool = False) -> None:
        session, self._session = self._session, None
        try:
            self.recorder.set_segment_callback(None)
        except Exception:
            pass
        if session is not None and cancel:
            try:
                session.cancel()
            except Exception:
                pass

    def _transcribe(self, audio) -> str:
        """Decode the recording, using the streaming session when one is live."""
        session, self._session = self._session, None
        try:
            self.recorder.set_segment_callback(None)
        except Exception:
            pass
        if session is None:
            return self.transcriber.transcribe(audio)
        try:
            tail = self.recorder.pop_tail()
        except Exception:
            _log.exception('Could not read the streaming tail; decoding whole clip')
            session.cancel()
            return self.transcriber.transcribe(audio)
        text = session.finish(tail)
        complete = getattr(session, 'complete', True)
        if text and complete:
            _log.info('Streaming: joined %d background segment(s) + tail',
                      session.segment_count)
            return text
        attempted = int(getattr(session, 'attempted', 0) or 0)
        if not text and complete and attempted > 0:
            # Every decode ran and every one heard nothing: a silent clip.
            # Decoding the whole buffer again would only double the wait for
            # the same empty answer (22 such re-decodes in the log).
            _log.info('Streaming: %d decode(s) found no speech; not re-decoding', attempted)
            return ''
        # A segment failed / timed out and the result is missing words, or
        # nothing was ever decoded. Re-decode the whole clip: being slow is
        # always better than silently handing the user a truncated dictation.
        _log.warning(
            'Streaming result unusable (text=%s complete=%s attempted=%d); '
            're-decoding the whole clip',
            bool(text), complete, attempted,
        )
        return self.transcriber.transcribe(audio)

    def _postprocess(self, text: str, target_app: str | None) -> str:
        """Deterministic clean-up between transcription and injection.

        Best-effort by construction: if anything in the post-processing stage
        misbehaves, the user still gets their raw transcript rather than
        nothing. Imported lazily so the module stays optional.
        """
        try:
            import postprocess

            cleaned = postprocess.apply(text, target_app=target_app)
        except Exception:
            _log.exception('Post-processing failed; injecting the raw transcript')
            return text
        if cleaned != text:
            _log.debug('postprocess: %r -> %r', preview_text(text), preview_text(cleaned))
        return cleaned

    def _observe_correction(
        self, store: Any, transcript_id: int, text: str, target_app: str | None
    ) -> None:
        """Watch for the user fixing what we just typed.

        Pure measurement, and structurally incapable of affecting the
        dictation that has already been delivered: the work happens on a timer
        thread, every failure path is swallowed, and the module fails closed
        without the Accessibility grant. See readback.py for what it may and
        may not read.
        """
        if not getattr(config, 'LEARN_FROM_CORRECTIONS', True):
            try:
                store.finish_observation(transcript_id, 'disabled')
            except Exception:
                _log.debug('Could not persist disabled observation', exc_info=True)
            return
        try:
            if self._corrections is None:
                import readback

                self._corrections = readback.CorrectionWatcher(store)
            self._corrections.observe(transcript_id, text, target_app)
        except Exception:
            _log.debug('Could not arm correction read-back', exc_info=True)
            try:
                store.finish_observation(transcript_id, 'unarmed')
            except Exception:
                _log.debug('Could not persist failed read-back', exc_info=True)

    def _save_recording(self, store: Any, transcript_id: int, audio: Any) -> None:
        """Persist the clip's audio when the user has opted into recordings.

        Best-effort and fully self-contained: a failure here must never
        affect a dictation that has already been transcribed and injected.
        """
        if not getattr(config, 'SAVE_RECORDINGS', False):
            return
        try:
            import recordings

            if recordings.save_for_transcript(store, transcript_id, audio):
                recordings.enforce_retention(store)
        except Exception:
            _log.exception('Recording save failed (non-fatal)')

    def _duration(self, audio) -> float:
        duration = getattr(self.recorder, 'duration', None)
        if callable(duration):
            return float(duration(audio))
        return len(audio) / config.SAMPLE_RATE
