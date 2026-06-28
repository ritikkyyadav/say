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
        'you',
        'thank you',
        'thanks',
        'thanks for watching',
        'thank you for watching',
        'thanks for watching!',
        'thank you for watching!',
        'subscribe',
        'subscribe to my channel',
        'subscribe to the channel',
        'please subscribe',
        'like and subscribe',
        'bye',
        'bye!',
        'bye-bye',
        'goodbye',
        'see you',
        'see you next time',
        'see you later',
        'okay',
        'ok.',
        'thank you so much',
        'i love you',
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
        self._mlx_whisper = None
        self._ms = None            # moonshine_voice Transcriber, when active
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

    def transcribe(self, audio) -> str:
        """Transcribe an audio buffer to text.

        Returns an empty string if the audio is too short or the
        transcription matches a known hallucination phrase.
        """
        if not self._loaded:
            self.load()

        min_samples = int(config.MIN_AUDIO_SECONDS * config.SAMPLE_RATE)
        if len(audio) < min_samples:
            return ''

        if self._backend == 'moonshine':
            text = self._transcribe_moonshine(audio)
        else:
            text = self._transcribe_whisper(audio)

        active_filter = _build_active_filter(config.LANGUAGE)
        if _normalize_for_filter(text) in active_filter:
            return ''
        return text

    def _transcribe_whisper(self, audio) -> str:
        t0 = time.time()
        kwargs = {
            'path_or_hf_repo': getattr(self, '_resolved_path', self._model_path),
            'fp16': True,
        }
        if config.LANGUAGE:
            kwargs['language'] = config.LANGUAGE
        if config.INITIAL_PROMPT:
            kwargs['initial_prompt'] = config.INITIAL_PROMPT

        result = self._mlx_whisper.transcribe(audio, **kwargs)
        text = result.get('text', '').strip()
        _log.info(
            '%.1fs audio -> %.1fs inference',
            len(audio) / config.SAMPLE_RATE, time.time() - t0,
        )
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
        _log.info(
            '%.1fs audio -> %.1fs inference (moonshine)',
            len(audio) / config.SAMPLE_RATE, time.time() - t0,
        )
        return text
