"""Runtime management of swappable STT models.

Where `model_catalog.py` *describes* models, this module *acts* on them:

  - download a model into the HF cache with live progress,
  - delete a cached model to reclaim disk,
  - switch the active model and apply it to the running engine immediately
    (no relaunch — the next dictation uses the new model).

A single process-wide `ModelManager` owns the in-flight download state and a
reference to the live `DictationController` so model switches take effect now.
The desktop UI reads progress by polling `GET /models` (no SSE coupling), so a
download surfaces as a rising `percent` on the model's status.

Privacy note: downloads come straight from Hugging Face to this machine's
cache and never touch a Say server. Audio is still never written to disk.
"""

from __future__ import annotations

import errno
import os
import shutil
import threading
from threading import Lock
from typing import Any

import config
import model_catalog
import settings as settings_mod
from logging_setup import get_logger

_log = get_logger('model_manager')

# Download lifecycle states surfaced to the UI.
IDLE = 'idle'
DOWNLOADING = 'downloading'
DONE = 'done'
ERROR = 'error'

# Require this much headroom beyond a model's nominal size before we start a
# download — leaves room for HF's temporary *.incomplete files + metadata.
_DISK_HEADROOM = 1.12


class DownloadCancelled(Exception):
    """Raised inside the download thread to abort a snapshot mid-stream."""


class InsufficientStorage(Exception):
    """Not enough free disk to safely download a model."""


def _gb(n: int) -> str:
    """Compact GB label for user-facing disk messages."""
    gb = n / 1e9
    return f'{gb:.1f} GB' if gb >= 0.1 else f'{max(1, n // 10**6)} MB'


def _free_bytes_for_cache() -> int:
    """Free bytes on the volume that holds the HF cache (−1 if unknown)."""
    path = model_catalog._cache_root()
    while not os.path.exists(path):
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return -1


def _friendly_error(exc: Exception) -> str:
    """Turn a raw download exception into a one-line, user-facing message."""
    if isinstance(exc, OSError) and getattr(exc, 'errno', None) == errno.ENOSPC:
        return 'Not enough disk space to finish the download.'
    low = str(exc).lower()
    if any(
        s in low
        for s in (
            'connection', 'timed out', 'timeout', 'network', 'temporarily',
            'getaddrinfo', 'failed to establish', 'max retries', 'name or service',
        )
    ):
        return 'Network error — check your internet connection and try again.'
    if 'no space' in low or 'enospc' in low:
        return 'Not enough disk space to finish the download.'
    return f'Download failed: {exc}'


def _cancellable_tqdm(cancel_ev: threading.Event):
    """A tqdm subclass that aborts the download when `cancel_ev` is set.

    huggingface_hub calls `update()` for every downloaded chunk; raising there
    propagates out of `snapshot_download`, so cancellation lands within one
    chunk (sub-second) instead of after the whole multi-GB file.
    """
    from tqdm.auto import tqdm as _tqdm

    class _CancellableTqdm(_tqdm):
        def update(self, n: float = 1) -> bool | None:
            if cancel_ev.is_set():
                raise DownloadCancelled()
            return super().update(n)

    return _CancellableTqdm


class ModelManager:
    """Owns download state + the live engine handle for model swaps."""

    def __init__(self) -> None:
        self._lock = Lock()
        # model_id -> {'state', 'percent', 'error'}
        self._status: dict[str, dict[str, Any]] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._cancels: dict[str, threading.Event] = {}
        self._engine: Any = None  # DictationController (duck-typed: .transcriber)

    # ── Engine wiring ────────────────────────────────────────────────────

    def register_engine(self, engine: Any) -> None:
        """Bind the live controller so `set_active` can swap the model now."""
        with self._lock:
            self._engine = engine

    # ── Active model ─────────────────────────────────────────────────────

    def active_model(self) -> str:
        return str(settings_mod.get('MODEL', config.MODEL))

    def set_active(self, model_id: str) -> str:
        """Persist the active model and apply it to the running engine.

        Returns the new active id. Raises ValueError for unknown models.
        The swap is lazy in the engine: `swap_model` flips the identity and
        the *next* transcription warms it, so this returns promptly.
        """
        if not model_catalog.is_known(model_id):
            raise ValueError(f'Unknown model: {model_id}')

        settings_mod.set_value('MODEL', model_id)

        with self._lock:
            engine = self._engine
        if engine is not None:
            try:
                engine.transcriber.swap_model(model_id)
                _log.info('Active model swapped live to %s', model_id)
            except Exception:
                _log.exception('Live model swap failed for %s', model_id)
        return model_id

    # ── Status ───────────────────────────────────────────────────────────

    def _status_for(self, model_id: str) -> dict[str, Any]:
        """Current download status for one model (never raises)."""
        with self._lock:
            st = self._status.get(model_id)
            st = dict(st) if st else {'state': IDLE, 'percent': 0, 'error': None}
        # A model that finished (or was already cached) reads as done.
        if st['state'] != DOWNLOADING and model_catalog.is_downloaded(model_id):
            st = {'state': DONE, 'percent': 100, 'error': None}
        return st

    def list_models(self) -> list[dict[str, Any]]:
        """Catalog enriched with cache state, active flag, and download status."""
        active = self.active_model()
        out: list[dict[str, Any]] = []
        for info in model_catalog.catalog():
            row = model_catalog.to_dict(info)
            row['active'] = info.id == active
            row['download'] = self._status_for(info.id)
            out.append(row)
        return out

    def total_disk_bytes(self) -> int:
        """Total bytes occupied by all downloaded catalog models."""
        return sum(
            model_catalog.disk_usage_bytes(info.id)
            for info in model_catalog.catalog()
            if model_catalog.is_downloaded(info.id)
        )

    # ── Download ─────────────────────────────────────────────────────────

    def start_download(self, model_id: str) -> dict[str, Any]:
        """Begin (or resume reporting) a background download.

        Idempotent: returns the live status if already downloading, a done
        status if already cached. Raises ValueError for unknown models and
        InsufficientStorage if the volume can't fit the model.
        """
        if not model_catalog.is_known(model_id):
            raise ValueError(f'Unknown model: {model_id}')

        if model_catalog.is_downloaded(model_id):
            return {'state': DONE, 'percent': 100, 'error': None}

        # Pre-flight disk check — fail loudly *before* writing gigabytes that
        # would otherwise abort mid-stream with a confusing ENOSPC.
        info = model_catalog.get(model_id)
        need = int((info.download_bytes if info else 0) * _DISK_HEADROOM)
        free = _free_bytes_for_cache()
        if free >= 0 and need > 0 and free < need:
            raise InsufficientStorage(
                f'Needs ~{_gb(need)} free, but only {_gb(free)} available.'
            )

        with self._lock:
            existing = self._status.get(model_id)
            if existing and existing['state'] == DOWNLOADING:
                return dict(existing)
            self._status[model_id] = {'state': DOWNLOADING, 'percent': 0, 'error': None}
            self._cancels[model_id] = threading.Event()
            t = threading.Thread(
                target=self._run_download,
                args=(model_id,),
                name=f'say-dl-{model_id.split("/")[-1]}',
                daemon=True,
            )
            self._threads[model_id] = t
            t.start()
            return dict(self._status[model_id])

    def cancel_download(self, model_id: str) -> bool:
        """Request cancellation of an in-flight download. No-op otherwise."""
        with self._lock:
            ev = self._cancels.get(model_id)
            st = self._status.get(model_id)
            active = bool(ev and st and st['state'] == DOWNLOADING)
        if active and ev is not None:
            ev.set()
            _log.info('Cancellation requested for %s', model_id)
        return active

    def download_status(self, model_id: str) -> dict[str, Any]:
        return self._status_for(model_id)

    def _set_status(self, model_id: str, **fields: Any) -> None:
        with self._lock:
            st = self._status.setdefault(
                model_id, {'state': IDLE, 'percent': 0, 'error': None}
            )
            st.update(fields)

    def _cleanup_partial(self, model_id: str) -> None:
        """Remove a half-written cache dir (after cancel) — cache-root guarded."""
        repo = model_catalog.repo_dir(model_id)
        cache_root = os.path.realpath(model_catalog._cache_root())
        if os.path.realpath(repo).startswith(cache_root) and os.path.isdir(repo):
            shutil.rmtree(repo, ignore_errors=True)

    def _run_download(self, model_id: str) -> None:
        info = model_catalog.get(model_id)
        expected = info.download_bytes if info else 0
        with self._lock:
            cancel_ev = self._cancels.get(model_id) or threading.Event()

        stop_poll = threading.Event()
        poller = threading.Thread(
            target=self._poll_progress,
            args=(model_id, expected, stop_poll),
            name=f'say-dlpoll-{model_id.split("/")[-1]}',
            daemon=True,
        )
        poller.start()

        try:
            from huggingface_hub import snapshot_download

            _log.info('Downloading model %s', model_id)
            snapshot_download(repo_id=model_id, tqdm_class=_cancellable_tqdm(cancel_ev))
            self._set_status(model_id, state=DONE, percent=100, error=None)
            _log.info('Model download complete: %s', model_id)
        except DownloadCancelled:
            _log.info('Download cancelled: %s', model_id)
            self._cleanup_partial(model_id)
            with self._lock:
                self._status.pop(model_id, None)
        except Exception as exc:  # network error, disk full, bad repo …
            _log.exception('Model download failed for %s', model_id)
            self._set_status(model_id, state=ERROR, error=_friendly_error(exc))
        finally:
            stop_poll.set()
            with self._lock:
                self._cancels.pop(model_id, None)

    def _poll_progress(self, model_id: str, expected: int, stop: threading.Event) -> None:
        """Drive percent from *real* bytes-on-disk until the download finishes.

        The numerator is the actual blob bytes landed in the cache; only the
        denominator (catalog size) is approximate. Monotonic and capped at 99
        so the bar only hits 100 when the download thread returns success.
        """
        while not stop.is_set():
            if expected > 0:
                have = model_catalog.disk_usage_bytes(model_id)
                pct = min(99, int(have * 100 / expected))
                with self._lock:
                    st = self._status.get(model_id)
                    if st and st['state'] == DOWNLOADING and pct > st['percent']:
                        st['percent'] = pct
            stop.wait(0.5)

    # ── Delete ───────────────────────────────────────────────────────────

    def delete(self, model_id: str) -> None:
        """Remove a cached model from disk. Raises ValueError if not allowed."""
        if not model_catalog.is_known(model_id):
            raise ValueError(f'Unknown model: {model_id}')
        if model_id == self.active_model():
            raise ValueError('Cannot delete the active model. Switch first.')
        if model_catalog.bundled_dir(model_id) is not None:
            raise ValueError('This model ships inside the app and cannot be deleted.')

        with self._lock:
            st = self._status.get(model_id)
            if st and st['state'] == DOWNLOADING:
                raise ValueError('Download in progress. Try again when it finishes.')

        repo = model_catalog.repo_dir(model_id)
        cache_root = os.path.realpath(model_catalog._cache_root())
        # Safety: only ever delete inside the HF cache root.
        if not os.path.realpath(repo).startswith(cache_root):
            raise ValueError('Refusing to delete outside the model cache.')
        if os.path.isdir(repo):
            shutil.rmtree(repo, ignore_errors=True)
            _log.info('Deleted cached model %s', model_id)
        with self._lock:
            self._status.pop(model_id, None)


# ── Module singleton ─────────────────────────────────────────────────────

_manager: ModelManager | None = None
_manager_lock = Lock()


def get_manager() -> ModelManager:
    """Process-wide singleton, mirroring get_bus()/get_store()."""
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = ModelManager()
        return _manager
