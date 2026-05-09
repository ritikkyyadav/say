"""On-device Whisper transcription through mlx-whisper.

The Transcriber owns the model identity (`_model_path`) and the loaded
state. mlx-whisper internally caches loaded model weights keyed by
`path_or_hf_repo`, so repeated `transcribe()` calls with the same path
do not reload — but this is implementation detail of mlx-whisper, so
the library version is pinned in requirements.txt.

The model can be swapped at runtime via `swap_model(...)`. The next
transcription warms the new model.
"""

from __future__ import annotations

import os
import time
from threading import Lock

import config
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


def _build_active_filter(language: str | None) -> set[str]:
    base = set(_HALLUCINATION_FILTER['language_neutral'])
    if language:
        lang = language.lower().split('-')[0].split('_')[0]
        base.update(_HALLUCINATION_FILTER.get(lang, set()))
    else:
        # Auto-detect mode — apply every language filter we know about.
        for lang_set in _HALLUCINATION_FILTER.values():
            base.update(lang_set)
    return {phrase.lower().strip() for phrase in base}


class Transcriber:
    """On-device Whisper transcription engine.

    Uses mlx-whisper to run OpenAI Whisper models natively on Apple
    Silicon. Model weights are downloaded once from Hugging Face (or
    loaded from a bundled path) and cached for the process lifetime.
    """

    def __init__(self, model_path: str | None = None):
        self._model_path = model_path or config.MODEL
        self._mlx_whisper = None
        self._loaded = False
        self._lock = Lock()

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    @property
    def model_path(self) -> str:
        return self._model_path

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
        """Load and warm up the Whisper model. Idempotent."""
        with self._lock:
            if self._loaded:
                return

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

            elapsed = time.time() - t0
            self._loaded = True
            _log.info('Model loaded in %.1fs', elapsed)

    def swap_model(self, new_model_path: str) -> None:
        """Switch to a different Whisper model. The next transcribe() call warms it."""
        with self._lock:
            if new_model_path == self._model_path and self._loaded:
                return
            self._model_path = new_model_path
            self._loaded = False

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

        elapsed = time.time() - t0
        duration = len(audio) / config.SAMPLE_RATE
        _log.info('%.1fs audio -> %.1fs inference', duration, elapsed)

        active_filter = _build_active_filter(config.LANGUAGE)
        if text.lower().strip() in active_filter:
            return ''

        return text
