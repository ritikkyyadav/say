"""On-device speech-to-text with a pluggable backend.

The Transcriber owns the model identity (`_model_path`, a catalog id) and
the loaded state. It dispatches by the model's `backend` (see
`model_catalog.py`):

  - 'whisper-mlx' — OpenAI Whisper via mlx-whisper (multilingual, HF cache).
  - 'moonshine'   — Moonshine ONNX via `moonshine_voice` (English, no torch),
                    the small bundled default.

Heavy runtimes (mlx_whisper / moonshine_voice) are imported lazily inside
the load path so neither is pulled in until a model of that backend is
actually used. The model can be swapped at runtime via `swap_model(...)`;
the next transcription warms the new model.
"""

from __future__ import annotations

import os
import time
from threading import Lock
from typing import Any

import config
import model_catalog
from logging_setup import get_logger

_log = get_logger('transcriber')


# Phrases that Whisper hallucinates on silence / music / noise. We drop
# the result entirely if the transcription matches one of these (case-
# insensitive, after stripping). Each language gets its own list; the
# union across LANGUAGE_NEUTRAL is also applied.
_HALLUCINATION_FILTER: dict[str, set[str]] = {
    'language_neutral': {
        '',
        '...',
        '. . .',
        '[blank_audio]',
        '[music]',
        '(music)',
        '*music*',
    },
    'en': {
        'thanks for watching',
        'thank you for watching',
        'thanks for watching!',
        'thank you for watching!',
        'subscribe',
        'subscribe to my channel',
        'subscribe to the channel',
        'please subscribe',
        'like and subscribe',
    },
    'es': {
        'gracias',
        '¡gracias!',
        'gracias por ver',
        'gracias por ver el video',
        'suscríbete',
    },
    'fr': {
        'merci',
        'merci d\'avoir regardé',
        'abonnez-vous',
    },
    'de': {
        'danke',
        'danke fürs zuschauen',
        'abonniert',
    },
    'pt': {
        'obrigado',
        'obrigada',
        'inscreva-se',
    },
    'ja': {
        'ありがとうございました',
        'ご視聴ありがとうございました',
        'チャンネル登録',
    },
    'hi': {
        'धन्यवाद',
    },
}


# Punctuation/whitespace Whisper sprinkles around a hallucinated phrase
# ("Thank you.", " Thank you! ", "Thanks…"). Stripping these before the
# membership test means the filter set can stay punctuation-free.
_FILTER_STRIP = ' \t\n.!?,…"\'`-'


def _normalize_for_filter(text: str) -> str:
    return text.lower().strip().strip(_FILTER_STRIP).strip()


# A decode slower than real time is the tell for a throttled or swapping
# machine — 35 to 44 s for 8 to 15 s of audio on 2026-09-05 — and nothing used
# to say so. Warn on any clip at least this long whose real-time factor
# exceeds SLOW_RTF, so the log names the cause of a long wait.
SLOW_RTF = 1.0
_SLOW_MIN_AUDIO_SECONDS = 1.0

# What one decoder call costs however little audio it is given. Whisper pads
# every call to a 30-second window, so on this hardware anything up to 30 s
# takes ~2.1-2.3 s (measured 2026-10-03: 1.2 s of audio -> 2.07 s, 29.6 s ->
# 2.29 s). Judged by RTF alone, every short clip "fell below real time", which
# is what most of the 25 such warnings since Sep 7 were. A decode is only slow
# when it is slow *for that call*.
_CALL_FLOOR_S = {'whisper': 2.3, 'whisper-mlx': 2.3}
_FLOOR_MARGIN = 1.5


def note_realtime(audio_seconds: float, elapsed: float, backend: str) -> float:
    """Log the decode timing; warn when it fell below real time. Returns the RTF."""
    rtf = elapsed / audio_seconds if audio_seconds > 0 else 0.0
    floor = _CALL_FLOOR_S.get(backend, 0.0) * _FLOOR_MARGIN
    if audio_seconds >= _SLOW_MIN_AUDIO_SECONDS and rtf > SLOW_RTF and elapsed > floor:
        _log.warning(
            'Decoding fell below real time: %.1fs of audio took %.1fs (RTF %.2f, %s) — '
            'the machine is throttled or under memory pressure',
            audio_seconds, elapsed, rtf, backend,
        )
    else:
        _log.info('%.1fs audio -> %.1fs inference (%s)', audio_seconds, elapsed, backend)
    return rtf


# Below this much audio, the style seed is dropped from the decoder prompt.
# Two sentences of text prior against a fraction of a second of sound is a
# contest the audio loses: the decoder follows the prompt and loops. Long
# enough clips have plenty of acoustic evidence and benefit from the seed.
_STYLE_SEED_MIN_SECONDS = 2.0

# Whole-piece RMS below which nothing is decoded at all. Whisper does not
# return '' for silence, it invents text ("Monica Monica.", "Three programs."),
# and the phrase filter can only catch inventions it has seen before. Measured
# over 482 stored dictations: every clip under this level produced garbage
# (most are digital silence from a stream that had not started delivering),
# and the quietest genuine long dictation sits 3.7x above it. It is checked
# here, not in the controller, because streaming segments and whole clips
# both come through `transcribe()`.
_SILENCE_RMS = 0.0015


def _is_silent(audio: Any) -> bool:
    """True when the piece carries no energy worth decoding. Never raises."""
    try:
        import numpy as np

        arr = np.asarray(audio, dtype=np.float32)
        if arr.size == 0:
            return True
        return float(np.sqrt(np.mean(arr ** 2))) < _SILENCE_RMS
    except Exception:
        # An audio buffer we cannot measure is decoded as before.
        return False


def _asr_prompt(audio_seconds: float | None = None) -> str:
    """Decoder bias string: style seed + user vocabulary + user prompt.

    Imported lazily so `postprocess` stays an optional collaborator and a
    failure there can never stop a transcription from happening.

    `audio_seconds` lets the prompt scale to the clip: short recordings get the
    vocabulary but not the style seed, because a strong text prior with almost
    no audio behind it is what drives the decoder into a repetition loop.
    """
    try:
        import postprocess

        include_seed = (
            audio_seconds is None or audio_seconds >= _STYLE_SEED_MIN_SECONDS
        )
        return postprocess.asr_prompt(include_style_seed=include_seed)
    except Exception:
        _log.exception('asr_prompt() failed; falling back to the raw user prompt')
        return (getattr(config, 'INITIAL_PROMPT', '') or '').strip()


def _build_active_filter(language: str | None) -> set[str]:
    base = set(_HALLUCINATION_FILTER['language_neutral'])
    if language:
        lang = language.lower().split('-')[0].split('_')[0]
        base.update(_HALLUCINATION_FILTER.get(lang, set()))
    else:
        # Auto-detect mode — apply every language filter we know about.
        for lang_set in _HALLUCINATION_FILTER.values():
            base.update(lang_set)
    return {_normalize_for_filter(phrase) for phrase in base}


class Transcriber:
    """On-device Whisper transcription engine.

    Uses mlx-whisper to run OpenAI Whisper models natively on Apple
    Silicon. Model weights are downloaded once from Hugging Face (or
    loaded from a bundled path) and cached for the process lifetime.
    """

    def __init__(self, model_path: str | None = None):
        self._model_path = model_path or config.MODEL
        self._mlx_whisper: Any = None
        self._ms: Any = None       # moonshine_voice Transcriber, when active
        self._backend: str | None = None
        self._loaded = False
        self._lock = Lock()

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    @property
    def model_path(self) -> str:
        return self._model_path

    @staticmethod
    def _backend_for(model_id: str) -> str:
        info = model_catalog.get(model_id)
        return info.backend if info is not None else 'whisper-mlx'

    def _resolve_model_path(self, requested: str) -> str:
        """Prefer a bundled copy of the model under Resources/models/<name>/.

        When the app is shipped as a .app bundle (Phase 7), the default
        model is copied into the bundle so first-launch is offline. The
        Hugging Face repo id is still passed to mlx-whisper if no
        bundled path exists.
        """
        import sys

        if os.path.isdir(requested):
            return requested

        candidates: list[str] = []

        env_dir = os.environ.get('SAY_BUNDLED_MODELS_DIR')
        if env_dir:
            candidates.append(env_dir)

        meipass = getattr(sys, '_MEIPASS', None)
        if meipass:
            candidates.append(os.path.join(meipass, 'models'))

        here = os.path.dirname(os.path.abspath(__file__))
        candidates.append(os.path.join(here, 'models'))

        # Walk up from `here` looking for a sibling Resources/models dir.
        # Covers the PyInstaller-onedir layout: Contents/Resources/models.
        cursor = here
        for _ in range(6):
            candidates.append(os.path.join(cursor, 'Resources', 'models'))
            parent = os.path.dirname(cursor)
            if parent == cursor:
                break
            cursor = parent

        leaf = requested.split('/')[-1]
        for base in candidates:
            local = os.path.join(base, leaf)
            if os.path.isdir(local):
                return local
        return requested

    def predownload(self, status_cb=None) -> None:
        """Pre-fetch the model into the HF cache before load().

        Without this step, mlx-whisper downloads lazily on the first
        `transcribe()` call inside `load()`. On a fresh machine that
        means the user sees "loading" with no progress for several
        minutes. Calling `predownload()` first surfaces a clear "started
        download" status to the menu bar even if we cannot wire fine-
        grained byte progress.

        `status_cb` is called with short status strings.
        """
        if status_cb is None:
            status_cb = _log.info

        if self._backend_for(self._model_path) == 'moonshine':
            if (
                model_catalog.bundled_dir(self._model_path)
                or model_catalog.is_downloaded(self._model_path)
            ):
                status_cb('Model already present.')
                return
            status_cb(f'Downloading {self._model_path}...')
            try:
                from pathlib import Path

                import moonshine_voice as mv

                mv.get_model_for_language(
                    'en',
                    mv.ModelArch.MEDIUM_STREAMING,
                    cache_root=Path(model_catalog.moonshine_cache_root()),
                )
            except Exception as exc:
                status_cb(f'Download failed: {exc}')
                raise
            status_cb('Model download complete.')
            return

        resolved = self._resolve_model_path(self._model_path)
        if os.path.isdir(resolved):
            status_cb('Model already cached.')
            return

        try:
            from huggingface_hub import snapshot_download
        except ImportError:
            status_cb('huggingface_hub unavailable; will download lazily.')
            return

        status_cb(f'Downloading {self._model_path}...')
        try:
            snapshot_download(repo_id=self._model_path)
        except Exception as exc:
            status_cb(f'Download failed: {exc}')
            raise
        status_cb('Model download complete.')

    def load(self) -> None:
        """Load and warm up the active model. Idempotent. Dispatches by backend."""
        with self._lock:
            if self._loaded:
                return
            self._backend = self._backend_for(self._model_path)
            if self._backend == 'moonshine':
                self._load_moonshine()
            else:
                self._load_whisper()
            self._loaded = True

    def _load_whisper(self) -> None:
        """Load + warm an mlx-whisper model (call under self._lock)."""
        resolved = self._resolve_model_path(self._model_path)
        _log.info('Loading model: %s', resolved)
        t0 = time.time()

        import mlx_whisper
        import numpy as np

        self._mlx_whisper = mlx_whisper
        self._resolved_path = resolved

        warmup_samples = max(
            int(config.MIN_AUDIO_SECONDS * config.SAMPLE_RATE),
            int(0.2 * config.SAMPLE_RATE),
        )
        warmup = np.zeros(warmup_samples, dtype='float32')
        self._mlx_whisper.transcribe(
            warmup,
            path_or_hf_repo=resolved,
            language=config.LANGUAGE if config.LANGUAGE else None,
            fp16=True,
        )
        _log.info('Whisper model loaded in %.1fs', time.time() - t0)

    def _load_moonshine(self) -> None:
        """Load + warm the Moonshine ONNX model (call under self._lock).

        Prefers a bundled copy under Resources/models/<leaf>/ so first launch
        is fully offline; otherwise resolves (downloading if needed) into the
        moonshine cache.
        """
        from pathlib import Path

        import moonshine_voice as mv
        from moonshine_voice.transcriber import Transcriber as _MoonshineRT

        bundled = model_catalog.bundled_dir(self._model_path)
        if bundled:
            model_dir: str = bundled
        else:
            resolved, _arch = mv.get_model_for_language(
                'en',
                mv.ModelArch.MEDIUM_STREAMING,
                cache_root=Path(model_catalog.moonshine_cache_root()),
            )
            model_dir = str(resolved)

        _log.info('Loading Moonshine model: %s', model_dir)
        t0 = time.time()
        self._ms = _MoonshineRT(model_dir, model_arch=mv.ModelArch.MEDIUM_STREAMING)
        # Warm up so the first real dictation isn't slowed by lazy init.
        try:
            warm = [0.0] * int(0.3 * config.SAMPLE_RATE)
            self._ms.transcribe_without_streaming(warm, sample_rate=config.SAMPLE_RATE)
        except Exception:
            _log.debug('Moonshine warmup skipped', exc_info=True)
        _log.info('Moonshine model loaded in %.1fs', time.time() - t0)

    def swap_model(self, new_model_path: str) -> None:
        """Switch models. The next transcribe() call loads + warms the new one."""
        with self._lock:
            if new_model_path == self._model_path and self._loaded:
                return
            if self._ms is not None:
                try:
                    self._ms.close()
                except Exception:
                    pass
                self._ms = None
            self._model_path = new_model_path
            self._loaded = False
            self._backend = None

    # ── memory (measured 2026-10-03, see CLAUDE.md) ──────────────────

    def release_cache(self) -> None:
        """Give back MLX's buffer cache, keeping the model loaded. Never raises.

        The cache grows with every decode: one process went 2.3 GB -> 4.1 GB
        over seven clips, and the shipped engine sat at 3.7 GB idle. Clearing
        it freed 2.3 GB with the model still in place and the next decode took
        2.17 s against 2.05-2.12 s before — so it runs after a dictation has
        landed, off the path anyone waits on.
        """
        if self._backend != 'whisper-mlx':
            return
        try:
            import mlx.core as mx

            mx.clear_cache()
        except Exception:
            _log.debug('MLX cache release skipped', exc_info=True)

    def unload_idle(self) -> bool:
        """Drop the Whisper weights while nobody is dictating. Never raises.

        mlx-whisper keeps the model on a class-level holder and reloads it on
        the next call if it is empty, so `_loaded` stays True and nothing has
        to warm up again: the first decode after an unload measured 2.13 s
        against 2.06 s warm. Freed ~2.1 GB. Returns True when it unloaded.
        """
        if self._backend != 'whisper-mlx':
            return False
        try:
            import gc

            import mlx.core as mx
            from mlx_whisper.transcribe import ModelHolder

            with self._lock:
                if ModelHolder.model is None:
                    return False
                ModelHolder.model = None
                ModelHolder.model_path = None
            gc.collect()
            mx.clear_cache()
            _log.info('Whisper weights unloaded while idle; they reload on the next dictation')
            return True
        except Exception:
            _log.debug('Idle unload skipped', exc_info=True)
            return False

    def transcribe(self, audio, prompt_override: str | None = None) -> str:
        """Transcribe an audio buffer to text.

        Returns an empty string if the audio is too short or the
        transcription matches a known hallucination phrase.

        `prompt_override` replaces the decoder bias string for this call only
        on Whisper. Moonshine's current Python runtime has no prompt argument;
        configured terms are repaired in the shared post-processing stage.
        """
        if not self._loaded:
            self.load()

        min_samples = int(config.MIN_AUDIO_SECONDS * config.SAMPLE_RATE)
        if len(audio) < min_samples:
            return ''

        if _is_silent(audio):
            _log.info('Audio below the speech floor; skipping decode')
            return ''

        if self._backend == 'moonshine':
            # Moonshine's Python API has no prompt parameter. Its vocabulary
            # repairs run later in the shared post-processing stage.
            text = self._transcribe_moonshine(audio)
        else:
            text = self._transcribe_whisper(audio, prompt_override=prompt_override)

        active_filter = _build_active_filter(config.LANGUAGE)
        if _normalize_for_filter(text) in active_filter:
            return ''
        return text

    def _transcribe_whisper(self, audio, prompt_override: str | None = None) -> str:
        t0 = time.time()
        kwargs = {
            'path_or_hf_repo': getattr(self, '_resolved_path', self._model_path),
            'fp16': True,
            # Whisper decodes in 30-second windows and, when conditioning is on,
            # prompts each window with the previous window's output. That carries
            # style as well as terminology, so one unpunctuated window cascades
            # through the rest of a long dictation. See config.py for the details.
            'condition_on_previous_text': bool(
                getattr(config, 'CONDITION_ON_PREVIOUS_TEXT', False)
            ),
        }
        if config.LANGUAGE:
            kwargs['language'] = config.LANGUAGE

        # Style seed + user vocabulary + user prompt, assembled in one place.
        # The prompt scales with the clip — see `_asr_prompt`.
        audio_seconds = len(audio) / float(config.SAMPLE_RATE)
        prompt = (
            prompt_override
            if prompt_override is not None
            else _asr_prompt(audio_seconds)
        )
        if prompt:
            kwargs['initial_prompt'] = prompt

        result = self._mlx_whisper.transcribe(audio, **kwargs)
        text = result.get('text', '').strip()
        note_realtime(len(audio) / config.SAMPLE_RATE, time.time() - t0, 'whisper')
        return text

    def _transcribe_moonshine(self, audio) -> str:
        t0 = time.time()
        try:
            data = audio.tolist()
        except AttributeError:
            data = list(audio)
        tx = self._ms.transcribe_without_streaming(data, sample_rate=config.SAMPLE_RATE)
        lines = getattr(tx, 'lines', None) or []
        text = ' '.join(getattr(ln, 'text', '') for ln in lines).strip()
        note_realtime(len(audio) / config.SAMPLE_RATE, time.time() - t0, 'moonshine')
        return text
