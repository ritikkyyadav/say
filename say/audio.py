"""Microphone capture for Say.

The mic stream is opened on `start()` and closed on `stop()`/`cancel()`,
so it is only live while a recording is actually in progress. The macOS
mic-active indicator therefore lights up exactly when you are dictating
and goes dark the instant you finish — never a persistent "always
listening" stream. A short in-recording pre-roll ring buffer still
guards against first-syllable clipping within a single capture; no audio
is ever written to disk.
"""

import threading
import time
from collections import deque

import config
import numpy as np
import sounddevice as sd
from logging_setup import get_logger

_log = get_logger('audio')

# Number of amplitude samples to keep for waveform visualization.
_AMPLITUDE_HISTORY_SIZE = 28

# After a recording ends we keep the mic stream open this long before
# closing it. A second dictation that starts within the window reuses the
# already-open stream — no close/reopen churn (which CoreAudio punishes
# with PaErrorCode -9986). Once idle past the window the stream closes and
# the macOS mic-active indicator goes dark.
_IDLE_CLOSE_DELAY = 0.6


class AudioRecorder:
    """On-demand recording capture — the stream is live only mid-record.

    Captures 16 kHz mono float32 audio. `start()` opens the InputStream;
    `stop()`/`cancel()` close it again so the system mic indicator only
    shows while a recording is in progress. Samples accumulate into
    `_frames` for transcription while `_capturing` is True.
    """

    def __init__(self):
        self._frames: list[np.ndarray] = []
        self._stream: sd.InputStream | None = None
        self._close_timer: threading.Timer | None = None
        self._lock = threading.Lock()
        self._amplitude_history = deque(
            [0.0] * _AMPLITUDE_HISTORY_SIZE, maxlen=_AMPLITUDE_HISTORY_SIZE
        )
        self._amplitude_callback = None
        self._event_callback = None
        self._max_frames_dispatched = False
        self._capturing = False

        pre_roll_samples = max(int(config.PRE_ROLL_SECONDS * config.SAMPLE_RATE), 1)
        self._pre_roll_buffer: deque = deque(maxlen=pre_roll_samples)

    def set_amplitude_callback(self, callback):
        """Set a callback that receives amplitude values in real-time.

        Signature: callback(amplitude: float) where 0.0 <= amplitude <= 1.0.
        """
        self._amplitude_callback = callback

    def set_event_callback(self, callback):
        """Register a callback for non-amplitude stream events.

        Signature: callback(event_type: str, message: str | None)
        Event types:
          - 'max_duration': MAX_RECORDING_SECONDS reached; recording
            should be finalized. Fired exactly once per session.
          - 'stream_error': an unexpected error inside the audio
            callback (rare; the callback body wraps in try/except).
          - 'device_changed': fired by the device watcher (audio.py
            does not produce this; included here for documentation).

        Always invoked on a fresh daemon thread, never on the audio
        callback thread, so the callee may freely call back into the
        recorder (stop / cancel / shutdown) without deadlocking.
        """
        self._event_callback = callback

    def _dispatch_event(self, event_type: str, message: str | None = None) -> None:
        callback = self._event_callback
        if callback is None:
            return
        threading.Thread(
            target=callback,
            args=(event_type, message),
            daemon=True,
        ).start()

    def _ensure_stream_open(self) -> None:
        """Open the InputStream if it is not already open. Idempotent.

        Because we close the device on every stop(), CoreAudio may still
        be releasing it when the user dictates again a moment later — a
        too-quick re-open then fails with PaErrorCode -9986 ("Internal
        PortAudio error" / Audio Unit -10851). We retry with a short
        backoff and, after a couple of misses, hard-reset PortAudio, so a
        rapid second recording never surfaces a spurious mic error.
        """
        if self._stream is not None:
            return

        last_exc: Exception | None = None
        for attempt in range(6):
            try:
                stream = sd.InputStream(
                    samplerate=config.SAMPLE_RATE,
                    channels=1,
                    dtype='float32',
                    blocksize=1024,
                    callback=self._callback,
                )
                stream.start()
                self._stream = stream
                if attempt:
                    _log.info('Mic stream opened on retry %d', attempt)
                return
            except Exception as exc:
                last_exc = exc
                _log.warning(
                    'InputStream open failed (attempt %d/6): %s', attempt + 1, exc
                )
                # Let CoreAudio finish releasing the device from the prior close.
                time.sleep(0.1 * (attempt + 1))
                # Persistent failure → hard-reset PortAudio and try again.
                if attempt == 2:
                    try:
                        sd._terminate()
                        sd._initialize()
                        _log.info('Reset PortAudio after repeated open failures')
                    except Exception:
                        _log.debug('PortAudio reset failed', exc_info=True)

        assert last_exc is not None
        raise last_exc

    def _close_stream(self) -> None:
        """Stop and close the InputStream so the mic indicator goes dark.

        Must run OUTSIDE `self._lock`: sounddevice may fire the audio
        callback during stop()/close(), and that callback also takes the
        lock. Safe to call when the stream is already closed.
        """
        stream = self._stream
        self._stream = None
        if stream is None:
            return
        try:
            stream.stop()
        except Exception:
            pass
        try:
            stream.close()
        except Exception:
            pass

    def _cancel_close_timer(self) -> None:
        """Cancel a pending deferred close (called when recording resumes)."""
        timer = self._close_timer
        self._close_timer = None
        if timer is not None:
            timer.cancel()

    def _schedule_close(self) -> None:
        """Close the stream after `_IDLE_CLOSE_DELAY` unless we record again."""
        self._cancel_close_timer()
        timer = threading.Timer(_IDLE_CLOSE_DELAY, self._deferred_close)
        timer.daemon = True
        self._close_timer = timer
        timer.start()

    def _deferred_close(self) -> None:
        """Timer callback: close the mic only if still idle."""
        with self._lock:
            if self._capturing:
                return  # a new recording started within the window — keep it
            self._close_timer = None
        self._close_stream()

    def start(self):
        """Begin a recording. Pre-roll ring buffer is prepended."""
        with self._lock:
            # Keep any still-open stream from a recent recording — reusing
            # it avoids a close/reopen cycle (and the -9986 race).
            self._cancel_close_timer()
            self._frames = []
            self._max_frames_dispatched = False
            self._amplitude_history = deque(
                [0.0] * _AMPLITUDE_HISTORY_SIZE, maxlen=_AMPLITUDE_HISTORY_SIZE
            )

            if config.PRE_ROLL_SECONDS > 0 and len(self._pre_roll_buffer) > 0:
                seed = np.fromiter(
                    self._pre_roll_buffer, dtype='float32', count=len(self._pre_roll_buffer)
                ).reshape(-1, 1)
                self._frames.append(seed)

            self._capturing = True

        self._ensure_stream_open()

    def _callback(self, indata, frames, time, status):
        """sounddevice callback — feed pre-roll, accumulate when capturing."""
        try:
            if status:
                _log.warning('Audio status flag: %s', status)

            # Feed the pre-roll ring buffer first — fills even when idle.
            if config.PRE_ROLL_SECONDS > 0:
                self._pre_roll_buffer.extend(indata[:, 0])

            fire_max = False
            with self._lock:
                if not self._capturing:
                    capturing = False
                else:
                    capturing = True
                    max_frames = config.SAMPLE_RATE * config.MAX_RECORDING_SECONDS
                    total_samples = sum(frame.shape[0] for frame in self._frames)
                    if total_samples < max_frames:
                        self._frames.append(indata.copy())
                        if (
                            total_samples + indata.shape[0] >= max_frames
                            and not self._max_frames_dispatched
                        ):
                            self._max_frames_dispatched = True
                            fire_max = True

            if fire_max:
                self._dispatch_event(
                    'max_duration',
                    f'Reached {config.MAX_RECORDING_SECONDS}s recording cap',
                )

            if not capturing:
                # While idle, skip amplitude work so the overlay stays inert.
                return

            rms = float(np.sqrt(np.mean(indata ** 2)))
            # Normalise to 0–1 range (typical speech RMS is 0.01–0.15).
            amplitude = min(1.0, rms * 8.0)
            self._amplitude_history.append(amplitude)

            if self._amplitude_callback is not None:
                try:
                    self._amplitude_callback(amplitude)
                except Exception:
                    pass  # Never let visualization errors break recording.
        except Exception as exc:
            _log.exception('Audio callback exception')
            self._dispatch_event('stream_error', f'Audio callback failed: {exc}')

    def stop(self) -> np.ndarray:
        """Finish a recording and return the captured audio.

        Closes the mic stream so the macOS mic-active indicator turns off
        the instant recording ends. The pre-roll buffer is cleared so a
        later start() never prepends stale audio from this session.
        """
        with self._lock:
            self._capturing = False
            self._pre_roll_buffer.clear()
            if not self._frames:
                audio = np.array([], dtype='float32')
            else:
                audio = np.concatenate(self._frames, axis=0).flatten()
            self._frames = []
        self._schedule_close()
        return audio

    def cancel(self) -> None:
        """Cancel an active recording, discard samples, close the mic."""
        with self._lock:
            self._capturing = False
            self._frames = []
            self._pre_roll_buffer.clear()
            self._amplitude_history = deque(
                [0.0] * _AMPLITUDE_HISTORY_SIZE, maxlen=_AMPLITUDE_HISTORY_SIZE
            )
        self._schedule_close()

    def shutdown(self) -> None:
        """Tear down the audio stream entirely. Call once on app quit.

        Also called by the controller when the device watcher fires:
        the next start() will reopen against whatever device is the
        new system default.
        """
        with self._lock:
            self._cancel_close_timer()
            self._capturing = False
            self._frames = []
            self._pre_roll_buffer.clear()

        self._close_stream()

    @property
    def amplitude_history(self) -> list[float]:
        """Return a snapshot of recent amplitude values for waveform display."""
        return list(self._amplitude_history)

    @property
    def is_active(self) -> bool:
        """True if the stream is currently capturing into a recording."""
        return self._capturing

    @staticmethod
    def duration(audio: np.ndarray) -> float:
        """Return audio duration in seconds."""
        return len(audio) / config.SAMPLE_RATE

    @staticmethod
    def list_devices() -> str:
        """Return available audio devices for diagnostics."""
        return str(sd.query_devices())
