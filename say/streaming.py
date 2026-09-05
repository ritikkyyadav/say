"""Segment audio at silence so it can be transcribed while the user is still talking.

The wait after releasing the hotkey scales with how long you spoke, because
nothing starts until you stop: a 54-second dictation costs ~9 s of inference,
and the p90 in real use was 17.7 s. Cutting the clip at natural pauses and
transcribing each piece in the background collapses that to whatever is left in
the final segment — roughly constant, regardless of how long you talked.

Two pieces live here:

  * `split_on_silence()` — a pure function over a finished buffer. Used by the
    replay bench so segmentation can be scored offline against real dictations.
  * `SilenceSegmenter` — the same rule applied incrementally to blocks arriving
    from the audio callback, for use at runtime.

Both share `_rms` and the same thresholds, so what the bench measures is
what the engine does.

A note on quality: chopping a clip into separate decoder calls throws away the
cross-window context that gives Whisper its sentence structure. Callers must
carry the tail of the previous segment's text into the next segment's prompt
(see `carry_over_prompt`) or punctuation collapses — measured, not assumed.
"""

from __future__ import annotations

import queue
import threading
import time

import config
import numpy as np
from logging_setup import get_logger

_log = get_logger('streaming')

# A segment shorter than this is not worth a separate decoder call — model
# overhead would cost more than the parallelism saves.
SEGMENT_MIN_SECONDS = 8.0

# Whisper decodes in 30-second windows, so cutting at 30 s costs nothing extra.
SEGMENT_MAX_SECONDS = 30.0

# How much continuous quiet marks a real pause rather than a gap between words.
SEGMENT_SILENCE_SECONDS = 0.45

# RMS below this counts as silence. Normal speech sits around 0.01-0.15; room
# tone is typically well under 0.005.
SILENCE_RMS = 0.004

# How many characters of the previous segment to carry into the next prompt.
# Whisper's prompt window is limited, and the tail is what sets the style.
CARRY_OVER_CHARS = 200

# Below this length a piece is decoded with the *short* prompt: the user's
# terms only, no style seed and no carry-over text. Whole-clip mode already
# drops the seed under `transcriber._STYLE_SEED_MIN_SECONDS` because a strong
# text prior against a fraction of a second of audio makes the decoder follow
# the prompt instead of the sound and loop; streaming used to bypass that
# guard and hand every tail the seed plus 200 characters of carry-over. On
# 2026-09-05 a 0.4 s tail took 19.2 s to decode and came back as one word
# repeated 663 times. Only the tail can ever be this short — mid segments are
# at least SEGMENT_MIN_SECONDS — so no piece carrying real sentence context is
# affected.
SHORT_SEGMENT_SECONDS = 2.0


def _seconds(audio) -> float:
    try:
        return len(audio) / float(config.SAMPLE_RATE)
    except Exception:
        return 0.0


def _rms(block: np.ndarray) -> float:
    if block.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(block, dtype='float64'))))


def carry_over_prompt(base_prompt: str, previous_text: str) -> str:
    """Build the decoder prompt for the next segment.

    Whisper normally prompts each 30-second window with the text of the one
    before it, which is what keeps punctuation and capitalisation consistent
    across a long dictation. Splitting the audio into separate `transcribe()`
    calls breaks that chain, so we rebuild it by hand: style seed first, then
    the tail of what was just decoded.
    """
    tail = (previous_text or '').strip()
    if not tail:
        return base_prompt
    if len(tail) > CARRY_OVER_CHARS:
        tail = tail[-CARRY_OVER_CHARS:]
        # Start at a word boundary so the model isn't handed half a word.
        space = tail.find(' ')
        if space > 0:
            tail = tail[space + 1:]
    return f'{base_prompt} {tail}'.strip()


def split_on_silence(
    audio: np.ndarray,
    sample_rate: int | None = None,
    *,
    min_seconds: float = SEGMENT_MIN_SECONDS,
    max_seconds: float = SEGMENT_MAX_SECONDS,
    silence_seconds: float = SEGMENT_SILENCE_SECONDS,
    threshold: float = SILENCE_RMS,
    block_seconds: float = 0.064,
) -> list[np.ndarray]:
    """Split `audio` into segments, cutting only inside silent runs.

    Cuts land in the middle of a pause, never mid-word, so no audio is lost at
    the seams. A segment is closed when it is at least `min_seconds` long and a
    silent run of `silence_seconds` has elapsed, or unconditionally once it
    reaches `max_seconds`.

    Returns a list whose concatenation is the original audio.
    """
    sample_rate = sample_rate or config.SAMPLE_RATE
    flat = np.asarray(audio, dtype='float32').reshape(-1)
    if flat.size == 0:
        return []

    block = max(1, int(block_seconds * sample_rate))
    min_samples = int(min_seconds * sample_rate)
    max_samples = int(max_seconds * sample_rate)
    silence_blocks_needed = max(1, int(silence_seconds / block_seconds))

    segments: list[np.ndarray] = []
    start = 0
    silent_run = 0

    for pos in range(0, flat.size, block):
        chunk = flat[pos:pos + block]
        end = pos + chunk.size
        silent_run = silent_run + 1 if _rms(chunk) < threshold else 0
        length = end - start

        hit_max = length >= max_samples
        hit_pause = length >= min_samples and silent_run >= silence_blocks_needed
        if hit_max or hit_pause:
            segments.append(flat[start:end])
            start = end
            silent_run = 0

    if start < flat.size:
        segments.append(flat[start:])
    return segments


class SilenceSegmenter:
    """Incremental form of `split_on_silence` for the live audio callback.

    `feed()` is called from the sounddevice thread and must stay cheap — it only
    computes an RMS and compares counters. It returns True when the caller
    should close the current segment.
    """

    def __init__(
        self,
        sample_rate: int | None = None,
        *,
        min_seconds: float = SEGMENT_MIN_SECONDS,
        max_seconds: float = SEGMENT_MAX_SECONDS,
        silence_seconds: float = SEGMENT_SILENCE_SECONDS,
        threshold: float = SILENCE_RMS,
    ):
        self._sample_rate = sample_rate or config.SAMPLE_RATE
        self._min_samples = int(min_seconds * self._sample_rate)
        self._max_samples = int(max_seconds * self._sample_rate)
        self._silence_samples_needed = int(silence_seconds * self._sample_rate)
        self._threshold = threshold
        self.reset()

    def reset(self) -> None:
        self._length = 0
        self._silent_samples = 0

    def feed(self, block: np.ndarray) -> bool:
        """Account for one audio block. True means "close the segment now"."""
        n = block.shape[0]
        self._length += n
        if _rms(block) < self._threshold:
            self._silent_samples += n
        else:
            self._silent_samples = 0

        if self._length >= self._max_samples:
            self.reset()
            return True
        if (
            self._length >= self._min_samples
            and self._silent_samples >= self._silence_samples_needed
        ):
            self.reset()
            return True
        return False


class SegmentedSession:
    """Transcribes segments in the background while the user keeps talking.

    Ordinary flow is: stop talking, then wait for the whole clip to decode — so
    the wait grows with how long you spoke. Here each segment is handed off as
    soon as a pause closes it and decoded on a worker thread, so by the time the
    key is released only the final segment is outstanding. The perceived wait
    becomes roughly constant instead of proportional to the recording.

    One worker, strictly in order. That is deliberate: segment N's prompt is
    built from segment N-1's text, and running them concurrently would break
    that chain (and contend for the same model anyway).
    """

    def __init__(self, transcriber, base_prompt: str = '', short_prompt: str | None = None):
        self._transcriber = transcriber
        self._base_prompt = base_prompt
        # What a piece shorter than SHORT_SEGMENT_SECONDS is decoded with.
        # Defaults to the base prompt for callers that predate the guard.
        self._short_prompt = base_prompt if short_prompt is None else short_prompt
        # False if any segment failed or was still pending when finish() gave
        # up. The caller MUST check this: returning a partial transcript is
        # worse than being slow, because the user silently loses words.
        self.complete = True
        # Decoder calls this session made (segments + tail), counted whether
        # or not they succeeded. The controller uses it to tell "decoded and
        # heard nothing" from "never decoded": an empty result from a complete
        # session that attempted at least one decode is a silent clip, and
        # re-decoding the whole buffer — which the log shows happened 22 times
        # — only doubled the wait for a recording that contained nothing.
        self.attempted = 0
        self._queue: queue.Queue = queue.Queue()
        self._parts: list[str] = []
        self._lock = threading.Lock()
        self._cancelled = threading.Event()
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()

    # ── worker ───────────────────────────────────────────────────

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item is None:
                    return
                if self._cancelled.is_set():
                    continue
                text = self._transcribe(item)
                if text:
                    with self._lock:
                        self._parts.append(text)
            except Exception:
                self.complete = False
                _log.exception('Segment transcription failed; marking result incomplete')
            finally:
                self._queue.task_done()

    def _transcribe(self, segment) -> str:
        with self._lock:
            previous = ' '.join(self._parts)
            self.attempted += 1
        if _seconds(segment) < SHORT_SEGMENT_SECONDS:
            prompt = self._short_prompt
        else:
            prompt = carry_over_prompt(self._base_prompt, previous)
        return self._transcriber.transcribe(segment, prompt_override=prompt) or ''

    # ── public API ───────────────────────────────────────────────

    def push(self, segment) -> None:
        """Queue a closed segment. Returns immediately."""
        if not self._cancelled.is_set():
            self._queue.put(segment)

    def finish(self, tail, timeout: float = 180.0) -> str:
        """Decode the tail, wait for earlier segments, and join everything.

        The tail is decoded on the caller's thread — it is the only piece the
        user is actually waiting on, and doing it here avoids a needless hop.
        """
        # Wait for background segments, but never indefinitely — a wedged
        # worker must not hold a dictation hostage. Whatever finished in time
        # is still returned.
        deadline = time.time() + timeout
        while self._queue.unfinished_tasks and time.time() < deadline:
            time.sleep(0.01)
        if self._queue.unfinished_tasks:
            self.complete = False
            _log.warning(
                'Timed out waiting for %d background segment(s); result is '
                'incomplete and the caller should re-decode the whole clip',
                self._queue.unfinished_tasks,
            )

        tail_text = ''
        if tail is not None and len(tail):
            try:
                tail_text = self._transcribe(tail)
            except Exception:
                self.complete = False
                _log.exception('Tail transcription failed')
        with self._lock:
            parts = list(self._parts)
        if tail_text:
            parts.append(tail_text)
        self._shutdown()
        return ' '.join(p for p in parts if p).strip()

    def cancel(self) -> None:
        self._cancelled.set()
        self._shutdown()

    def _shutdown(self) -> None:
        try:
            self._queue.put_nowait(None)
        except Exception:
            pass

    @property
    def segment_count(self) -> int:
        with self._lock:
            return len(self._parts)
