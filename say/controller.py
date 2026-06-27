# controller.py - Shared dictation state machine.

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

import config
from injector import InjectionResult, inject
from logging_setup import get_logger
from metrics import get_ledger
from notifications import notify_error, notify_transcription_complete
from server.events import get_bus
from server.store import get_frontmost_app, get_store

_log = get_logger('controller')


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


StatusCallback = Callable[[DictationEvent], None]
Logger = Callable[[str], None]
Injector = Callable[[str], InjectionResult]


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
    ):
        self.recorder = recorder
        self.transcriber = transcriber
        self.injector = injector
        self.status_callback = status_callback
        self.logger = logger
        self.state = DictationState.IDLE
        self._recording = False
        self._recording_mode: str | None = None
        self._lock = threading.RLock()

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

    def _emit(
        self,
        state: DictationState,
        message: str,
        preview: str | None = None,
        error: str | None = None,
        mode: str | None = None,
    ) -> None:
        with self._lock:
            self.state = state
        event = DictationEvent(state, message, preview, error, mode)
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
            })
        except Exception:
            pass

    def load_model(self) -> bool:
        """Load the transcription model before accepting hotkeys."""
        self._emit(DictationState.LOADING, f'Loading model: {config.MODEL}')
        try:
            load = getattr(self.transcriber, 'load', None)
            if callable(load):
                load()
            self._emit(DictationState.READY, 'Ready — Fn to dictate')
            return True
        except Exception as exc:
            message = f'Model load failed: {exc}'
            _log.exception('Model load failed')
            self._emit(DictationState.ERROR, message, error=str(exc))
            notify_error(message)
            return False

    def start_recording(self, mode: str = 'ptt') -> bool:
        """Start recording if the app is ready for push-to-talk.

        Args:
            mode: 'ptt' for push-to-talk, 'hands_free' for toggle mode.
        """
        _log.info('start_recording called (mode=%s, current_state=%s)', mode, self.state)
        error_for_notify: str | None = None
        with self._lock:
            if self._recording or self.state in {
                DictationState.LOADING,
                DictationState.RECORDING,
                DictationState.PROCESSING,
            }:
                _log.info('start_recording rejected — already in %s', self.state)
                return False
            self._recording = True
            self._recording_mode = mode
            try:
                self.recorder.start()
                _log.info('AudioRecorder.start() returned — mic stream open')
            except Exception as exc:
                self._recording = False
                self._recording_mode = None
                error_for_notify = f'Microphone start failed: {exc}'
                _log.exception('AudioRecorder.start() raised')
                self._emit(DictationState.ERROR, error_for_notify, error=str(exc))

        if error_for_notify is not None:
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
            self._emit(DictationState.ERROR, message, error=str(exc))
            notify_error(message)
            return False

        duration = self._duration(audio)
        if duration < config.MIN_AUDIO_SECONDS:
            self._emit(DictationState.READY, f'Ready — clip too short ({duration:.1f}s)')
            return True

        self._emit(DictationState.PROCESSING, f'Transcribing {duration:.1f}s locally...')
        t0 = time.time()
        try:
            text = self.transcriber.transcribe(audio)
        except Exception as exc:
            message = f'Transcription failed: {exc}'
            self._emit(DictationState.ERROR, message, error=str(exc))
            notify_error(message)
            return False

        inference_seconds = time.time() - t0
        if not text:
            self._emit(
                DictationState.READY,
                f'Ready — no speech detected ({inference_seconds:.1f}s inference)',
            )
            return True

        transcript_preview = preview_text(text)
        self._emit(
            DictationState.PROCESSING,
            f'Injecting text ({inference_seconds:.1f}s inference)...',
            preview=transcript_preview,
        )

        # Capture frontmost app id BEFORE injection — by the time we inject
        # focus has not changed, but reading first is cheap and avoids any
        # race where a paste could shift focus on certain apps.
        target_app = get_frontmost_app()

        result = self.injector(text)
        if result.success:
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
            try:
                get_store().insert(
                    text=text,
                    audio_seconds=duration,
                    inference_seconds=inference_seconds,
                    model=config.MODEL,
                    inject_method=result.method,
                    target_app=target_app,
                    success=True,
                )
            except Exception:
                _log.exception('Failed to persist transcript to store')
            return True

        message = f'Injection failed: {result.error or "unknown error"}'
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
            success=False,
        )
        return False

    def cancel_recording(self) -> bool:
        """Cancel the current recording without transcribing."""
        with self._lock:
            if not self._recording:
                return False
            self._recording = False
            self._recording_mode = None

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

    def _duration(self, audio) -> float:
        duration = getattr(self.recorder, 'duration', None)
        if callable(duration):
            return float(duration(audio))
        return len(audio) / config.SAMPLE_RATE
