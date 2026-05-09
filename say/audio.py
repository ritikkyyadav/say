"""Microphone capture for Say.

The stream stays open between recordings while `config.PRE_ROLL_SECONDS > 0`
so that a small ring buffer of the most-recent audio can seed the next
recording. This eliminates first-syllable clipping from hotkey debounce
and human reaction time. The macOS mic-active LED is the user's honest
indicator that the stream is live; no audio is written to disk.
"""

import threading
from collections import deque

import config
import numpy as np
import sounddevice as sd
from logging_setup import get_logger

_log = get_logger('audio')

# Number of amplitude samples to keep for waveform visualization.
_AMPLITUDE_HISTORY_SIZE = 28


class AudioRecorder:
    """Always-on monitor stream with on-demand recording capture.

    Captures 16 kHz mono float32 audio. While `_capturing` is False the
    stream still feeds the pre-roll ring buffer; while `_capturing` is
    True it also accumulates samples into `_frames` for transcription.
    """

    def __init__(self):
        self._frames: list[np.ndarray] = []
        self._stream: sd.InputStream | None = None
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
        """Open the InputStream if it is not already open. Idempotent."""
        if self._stream is not None:
            return
        self._stream = sd.InputStream(
            samplerate=config.SAMPLE_RATE,
            channels=1,
            dtype='float32',
            blocksize=1024,
            callback=self._callback,
        )
        self._stream.start()

    def start(self):
        """Begin a recording. Pre-roll ring buffer is prepended."""
        with self._lock:
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
        """Finish a recording. Returns the captured audio.

        The underlying stream stays open so the pre-roll buffer keeps
        filling for the next start(). Use shutdown() to fully close.
        """
        with self._lock:
            self._capturing = False
            if not self._frames:
                return np.array([], dtype='float32')
            audio = np.concatenate(self._frames, axis=0).flatten()
            self._frames = []
            return audio

    def cancel(self) -> None:
        """Cancel an active recording and discard captured samples."""
        with self._lock:
            self._capturing = False
            self._frames = []
            self._amplitude_history = deque(
                [0.0] * _AMPLITUDE_HISTORY_SIZE, maxlen=_AMPLITUDE_HISTORY_SIZE
            )

    def shutdown(self) -> None:
        """Tear down the audio stream entirely. Call once on app quit.

        Also called by the controller when the device watcher fires:
        the next start() will reopen against whatever device is the
        new system default.
        """
        with self._lock:
            self._capturing = False
            self._frames = []
            self._pre_roll_buffer.clear()

        if self._stream is not None:
            try:
                self._stream.stop()
            except Exception:
                pass
            try:
                self._stream.close()
            except Exception:
                pass
            self._stream = None

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
