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
from streaming import SilenceSegmenter

_log = get_logger('audio')

# Number of amplitude samples to keep for waveform visualization.
_AMPLITUDE_HISTORY_SIZE = 28

# After a recording ends we keep the mic stream open this long before
# closing it. A second dictation that starts within the window reuses the
# already-open stream — no close/reopen churn (which CoreAudio punishes
# with PaErrorCode -9986). Once idle past the window the stream closes and
# the macOS mic-active indicator goes dark.
_IDLE_CLOSE_DELAY = 0.6

# How long a caller waits for the device to open before giving up. A healthy
# open takes tens of milliseconds and the retry loop below tolerates outright
# failures — but a *hang* is a different animal. CoreAudio's HAL mutex can be
# left held (a driver fault, a device yanked mid-open, or the concurrent
# Pa_Terminate this module used to be capable of), and every later
# `Pa_OpenStream` then blocks forever. Bounding the wait is what turns
# "dictation is silently dead until relaunch" into an error the user can act on.
_OPEN_TIMEOUT = 6.0

# How long the open-worker waits for another thread to be done with the device.
# Comfortably longer than a full retry loop (~2.1 s of backoff) so the ordinary
# arm()-then-start() handoff simply waits and reuses the stream the other thread
# is already opening, rather than opening a second one behind its back.
_DEVICE_BUSY_TIMEOUT = 10.0


class MicrophoneUnavailable(RuntimeError):
    """The audio device did not open within a bounded time.

    Raised instead of blocking forever so the controller can report an error,
    fall back to READY and let the user try again — a wedged CoreAudio must
    cost one dictation, not the whole session.
    """


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
        # Serialises every call into CoreAudio — open, start, stop, close and
        # the PortAudio reset. Deliberately NOT `self._lock`: the audio callback
        # takes that one, and holding it across a device call deadlocks (see
        # `_close_stream`). Held by the open-worker for its whole life, so a
        # wedged device parks exactly one thread inside CoreAudio instead of one
        # per keypress.
        self._device_lock = threading.Lock()
        self._amplitude_history = deque(
            [0.0] * _AMPLITUDE_HISTORY_SIZE, maxlen=_AMPLITUDE_HISTORY_SIZE
        )
        self._amplitude_callback = None
        self._event_callback = None
        self._monitor_callback = None
        # Streaming: frames accumulated since the last segment boundary, plus
        # the detector that decides where those boundaries fall.
        self._segment_frames: list[np.ndarray] = []
        self._segment_callback = None
        self._segmenter = None
        # Samples delivered by the audio callback during the current recording,
        # excluding the pre-roll seed. Zero here after stop() means the mic
        # never produced anything — see `live_seconds`.
        self._live_samples = 0
        # Audio captured since the last segment boundary, set aside by stop()
        # so the caller can transcribe just the tail rather than the whole clip.
        self._last_tail: np.ndarray = np.array([], dtype='float32')
        self._max_frames_dispatched = False
        self._capturing = False
        # Running count of captured samples for the current recording. Lets the
        # audio callback enforce the duration cap in O(1) instead of re-summing
        # every frame, which matters for long (multi-minute) dictations.
        self._captured_samples = 0

        pre_roll_samples = max(int(config.PRE_ROLL_SECONDS * config.SAMPLE_RATE), 1)
        self._pre_roll_buffer: deque = deque(maxlen=pre_roll_samples)

    def set_amplitude_callback(self, callback):
        """Set a callback that receives amplitude values in real-time.

        Signature: callback(amplitude: float) where 0.0 <= amplitude <= 1.0.
        """
        self._amplitude_callback = callback

    def set_monitor_callback(self, callback):
        """Register a live tap on the raw microphone stream.

        Signature: callback(block: np.ndarray) — 1-D mono float32.

        Unlike `set_amplitude_callback` this fires whenever audio is flowing,
        including the armed-but-not-yet-recording window, so a visualiser is
        already responding to the room while the hotkey is still deciding
        whether the gesture is a tap or a hold.

        Called *on the audio thread*: it must be fast, must not raise, and must
        copy anything it keeps — the block is a view onto a buffer PortAudio
        reuses. Exceptions are swallowed; a visualiser can never break capture.
        """
        self._monitor_callback = callback

    def set_segment_callback(self, callback):
        """Register a callback that receives closed audio segments mid-recording.

        Signature: callback(segment: np.ndarray)

        Enables streaming transcription: instead of waiting for the whole clip,
        the recorder hands off each chunk of speech as soon as a natural pause
        closes it, so most of the decoding happens while the user is still
        talking. Set to None to go back to whole-clip behaviour.

        Always invoked on a fresh daemon thread, never on the audio callback
        thread — transcription must never block audio capture.
        """
        self._segment_callback = callback
        self._segmenter = SilenceSegmenter() if callback is not None else None

    def _dispatch_segment(self, segment: np.ndarray) -> None:
        callback = self._segment_callback
        if callback is None or segment.size == 0:
            return
        threading.Thread(target=callback, args=(segment,), daemon=True).start()

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

        Every open runs on a worker that owns `_device_lock` for its whole
        life, and the caller waits on it for at most `_OPEN_TIMEOUT`. Both
        halves are load-bearing:

        *Serialised* because `arm()` and `start()` arrive on different threads
        ~250 ms apart (the hotkey's tap-vs-hold window), and the old
        check-then-act guard — `if self._stream is not None: return` outside any
        lock — let `start()` sail past while `arm()` was still blocked inside
        `sd.InputStream(...)` with `_stream` not yet assigned. Two threads then
        opened the same device, both hit -9986, and both ran the recovery below:
        one called `sd._terminate()` while the other sat inside `Pa_OpenStream`.
        That tears the host API down under a live HAL call and leaves CoreAudio's
        `HALB_Mutex` held forever, after which *every* open blocks — a sample of
        the wedged engine showed 35 threads parked in `Pa_OpenStream`, one per
        Fn press since the collision. Dictation was dead until relaunch.

        *Bounded* because `controller.start_recording()` calls us while holding
        its own RLock, so an unbounded open does not merely fail — it strands the
        state machine and every later press logs `start_recording called` and
        blocks silently. A wedged worker keeps the device lock, so subsequent
        attempts fail fast rather than queueing more threads inside CoreAudio,
        and the moment the driver recovers the worker finishes and the app heals
        itself with no restart.
        """
        if self._stream is not None:
            return

        done = threading.Event()
        abandoned = threading.Event()
        box: dict[str, Exception] = {}

        def _open_worker() -> None:
            try:
                if not self._device_lock.acquire(timeout=_DEVICE_BUSY_TIMEOUT):
                    box['exc'] = MicrophoneUnavailable(
                        'audio device is busy — an earlier open never returned'
                    )
                    return
                try:
                    # Whoever held the lock may have opened it already. That is
                    # the normal arm()/start() handoff, not a failure.
                    if self._stream is None:
                        self._open_locked()
                except Exception as exc:
                    box['exc'] = exc
                finally:
                    self._device_lock.release()
            finally:
                done.set()
                if abandoned.is_set() and self._stream is not None and not self._capturing:
                    # We came back after the caller gave up. Don't leave a mic
                    # live with nobody left to close it.
                    self._schedule_close()

        threading.Thread(target=_open_worker, daemon=True, name='say-mic-open').start()

        if not done.wait(_OPEN_TIMEOUT):
            abandoned.set()
            _log.error(
                'Microphone did not open within %.0fs — audio device wedged',
                _OPEN_TIMEOUT,
            )
            raise MicrophoneUnavailable(
                f'audio device did not respond within {_OPEN_TIMEOUT:.0f}s'
            )

        exc = box.get('exc')
        if exc is not None:
            raise exc

    def _open_locked(self) -> None:
        """Open and start the InputStream. Caller must hold `_device_lock`.

        Because we close the device on every stop(), CoreAudio may still
        be releasing it when the user dictates again a moment later — a
        too-quick re-open then fails with PaErrorCode -9986 ("Internal
        PortAudio error" / Audio Unit -10851). We retry with a short
        backoff and, after a couple of misses, hard-reset PortAudio, so a
        rapid second recording never surfaces a spurious mic error.

        The reset is only safe under `_device_lock`: `sd._terminate()` tears
        down the host API beneath any other thread inside `Pa_OpenStream`.
        """
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

        Runs under `_device_lock`, which is the other half of the wedge
        described in `_ensure_stream_open`: a close racing an open puts two
        threads inside CoreAudio just as surely as two opens do, and the
        `shutdown()` the device watcher fires on a device change arrives on its
        own thread. Best-effort — if the device is wedged we leave it rather
        than block the caller, since closing is only ever cleanup.

        Still deliberately OUTSIDE `self._lock`: sounddevice may fire the audio
        callback during stop()/close(), and that callback also takes it. Safe to
        call when the stream is already closed.
        """
        if not self._device_lock.acquire(timeout=_DEVICE_BUSY_TIMEOUT):
            _log.warning('Skipped mic close — audio device busy')
            return
        try:
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
        finally:
            self._device_lock.release()

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

    def arm(self) -> None:
        """Open the mic and start filling the pre-roll buffer, without capturing.

        Pre-roll only works if audio is already flowing when `start()` is
        called, and the stream is closed while idle — so before this existed the
        ring buffer was *always* empty at the moment it was read, and pre-roll
        silently contributed nothing. Combined with the hotkey's tap/hold
        discrimination window, roughly the first 300-450 ms of every dictation
        was discarded, which is usually the first word.

        The hotkey listener calls this the instant the trigger key goes down, so
        by the time the gesture resolves into a recording the buffer holds the
        audio spoken in between. `disarm()` undoes it if the gesture turns out
        to be an ordinary keyboard shortcut. Idempotent.
        """
        if config.PRE_ROLL_SECONDS <= 0:
            return
        with self._lock:
            self._cancel_close_timer()
        try:
            self._ensure_stream_open()
        except Exception as exc:
            # Arming is an optimisation — it only fills the pre-roll ring. The
            # call the user is actually waiting on is `start()`, which reports
            # a dead device properly; raising here would surface a mic error on
            # a key-down that may never become a recording at all, and would
            # escape into an unowned hotkey thread.
            _log.warning('Could not arm the microphone: %s', exc)

    def disarm(self) -> None:
        """Release a mic armed for a gesture that never became a recording."""
        with self._lock:
            if self._capturing:
                return  # a real recording started — leave it alone
            self._pre_roll_buffer.clear()
        self._schedule_close()

    def start(self):
        """Begin a recording. Pre-roll ring buffer is prepended."""
        # Open the device BEFORE the recording is marked live. Re-opening can
        # block for seconds while CoreAudio finishes releasing the device from
        # the previous recording (the -9986 retry path below sleeps up to
        # ~2.1 s and may hard-reset PortAudio). Any time spent in there with
        # `_capturing` already True is time the audio callback is not running,
        # so the user is speaking into a stream that does not exist yet.
        #
        # That is exactly how a ten-second sentence came back as a 0.6 s clip
        # containing only the pre-roll seed — which the decoder, handed a long
        # style-seed prompt and almost no audio, then padded out into a
        # repetition loop ("available available available…"). Opening first
        # also lets the pre-roll ring keep filling during the wait, so the
        # words spoken while the device settles are captured rather than lost.
        #
        # Keep any still-open stream from a recent recording — reusing it
        # avoids a close/reopen cycle (and the -9986 race) entirely.
        with self._lock:
            self._cancel_close_timer()
        self._ensure_stream_open()

        with self._lock:
            self._cancel_close_timer()
            self._frames = []
            self._segment_frames = []
            if self._segmenter is not None:
                self._segmenter.reset()
            self._max_frames_dispatched = False
            self._amplitude_history = deque(
                [0.0] * _AMPLITUDE_HISTORY_SIZE, maxlen=_AMPLITUDE_HISTORY_SIZE
            )

            if config.PRE_ROLL_SECONDS > 0 and len(self._pre_roll_buffer) > 0:
                seed = np.fromiter(
                    self._pre_roll_buffer, dtype='float32', count=len(self._pre_roll_buffer)
                ).reshape(-1, 1)
                self._frames.append(seed)

            self._captured_samples = sum(f.shape[0] for f in self._frames)
            self._segment_frames = list(self._frames)
            # Counts only audio delivered by the callback during this
            # recording, i.e. excluding the pre-roll seed. `live_seconds` is
            # what tells the controller whether the microphone actually
            # produced anything, as opposed to handing the decoder a clip made
            # entirely of pre-roll.
            self._live_samples = 0
            self._capturing = True

    def _callback(self, indata, frames, time, status):
        """sounddevice callback — feed pre-roll, accumulate when capturing."""
        try:
            if status:
                _log.warning('Audio status flag: %s', status)

            # Feed the pre-roll ring buffer first — fills even when idle.
            if config.PRE_ROLL_SECONDS > 0:
                self._pre_roll_buffer.extend(indata[:, 0])

            # Live tap, before the not-capturing early return: the overlay is
            # on screen from the moment the trigger key goes down, which is
            # ~300 ms before the gesture resolves into a recording.
            if self._monitor_callback is not None:
                try:
                    self._monitor_callback(indata[:, 0])
                except Exception:
                    pass  # visualisation must never break capture

            fire_max = False
            segment_to_emit = None
            with self._lock:
                if not self._capturing:
                    capturing = False
                else:
                    capturing = True
                    max_frames = config.SAMPLE_RATE * config.MAX_RECORDING_SECONDS
                    total_samples = self._captured_samples
                    if total_samples < max_frames:
                        block = indata.copy()
                        self._frames.append(block)
                        self._captured_samples = total_samples + indata.shape[0]
                        self._live_samples += indata.shape[0]
                        if self._segmenter is not None:
                            self._segment_frames.append(block)
                            if self._segmenter.feed(block):
                                segment_to_emit = np.concatenate(
                                    self._segment_frames, axis=0
                                ).flatten()
                                self._segment_frames = []
                        if (
                            self._captured_samples >= max_frames
                            and not self._max_frames_dispatched
                        ):
                            self._max_frames_dispatched = True
                            fire_max = True

            if segment_to_emit is not None:
                self._dispatch_segment(segment_to_emit)

            if fire_max:
                self._dispatch_event(
                    'max_duration',
                    f'Reached {config.MAX_RECORDING_SECONDS}s recording cap',
                )

            if not capturing:
                # While idle, skip amplitude work — nothing consumes it.
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
            if self._segment_frames:
                self._last_tail = np.concatenate(
                    self._segment_frames, axis=0
                ).flatten()
            else:
                self._last_tail = np.array([], dtype='float32')
            self._segment_frames = []
            self._captured_samples = 0
        self._schedule_close()
        return audio

    def cancel(self) -> None:
        """Cancel an active recording, discard samples, close the mic."""
        with self._lock:
            self._capturing = False
            self._frames = []
            self._segment_frames = []
            self._captured_samples = 0
            self._live_samples = 0
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
            self._segment_frames = []
            self._captured_samples = 0
            self._pre_roll_buffer.clear()

        self._close_stream()

    def pop_tail(self) -> np.ndarray:
        """Return the audio captured after the final segment boundary.

        Only meaningful in streaming mode, and only immediately after `stop()`:
        every earlier segment has already been handed to the segment callback,
        so this is the one piece still needing transcription — and the only
        thing the user is actually waiting on.
        """
        tail = self._last_tail
        self._last_tail = np.array([], dtype='float32')
        return tail

    @property
    def live_seconds(self) -> float:
        """Seconds of audio the callback actually delivered for this recording.

        Deliberately excludes the pre-roll seed, and is left intact by `stop()`
        so the controller can read it immediately afterwards. A clip whose
        duration is entirely pre-roll means the microphone never delivered a
        single block — a dead or still-opening stream — and must not be sent to
        the decoder, which will confabulate rather than return nothing.
        """
        return self._live_samples / float(config.SAMPLE_RATE)

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
