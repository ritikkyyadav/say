"""Curated catalog of on-device speech-to-text models + cache state.

Say's differentiator is that the transcription model is the user's choice:
trade speed for accuracy, or storage for capability, without ever sending a
byte off-device. This module is the **single source of truth** for which
models Say can run and what each one costs in disk + compute. It also answers
the only two runtime questions the UI ever asks about a model: *is it
downloaded?* and *how big is it on disk?*

Everything here is pure-Python and dependency-light so it stays trivially
unit-testable: point `HF_HUB_CACHE` at a temp dir and the cache-state helpers
read from there. Downloading and swapping live models is the job of
`model_manager.py`; this module only describes and inspects.

All shipped models are mlx-whisper compatible (the engine in
`transcriber.py` loads them via `mlx_whisper.transcribe(path_or_hf_repo=...)`).
The `family` field exists so non-Whisper families (e.g. Parakeet) can be added
later behind a transcribe-dispatch without reshaping the catalog.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass

from logging_setup import get_logger

_log = get_logger('model_catalog')


@dataclass(frozen=True)
class ModelInfo:
    """Static description of one installable STT model."""

    id: str               # Hugging Face repo id, e.g. 'mlx-community/whisper-...'
    name: str             # Short display name, e.g. 'Large v3 Turbo'
    family: str           # Model family, e.g. 'whisper'
    params: str           # Human parameter count, e.g. '809M'
    download_bytes: int   # Approx download / on-disk size in bytes (for progress + sort)
    speed: int            # 1 (slow) … 5 (fastest) — relative on Apple Silicon
    accuracy: int         # 1 (roughest) … 5 (best)
    languages: str        # 'multilingual' (99 langs) or 'english'
    recommended: bool     # Surface as the suggested default
    order: int            # Stable display order (lower = first)
    description: str       # One-line plain-language summary


# ── The catalog ──────────────────────────────────────────────────────────
#
# Ordered best-daily-driver first. Sizes are approximate fp16 weights as
# published by mlx-community; they drive the download progress denominator
# and the storage readout, not correctness, so "close enough" is fine.

CATALOG: tuple[ModelInfo, ...] = (
    ModelInfo(
        id='mlx-community/whisper-large-v3-turbo',
        name='Large v3 Turbo',
        family='whisper',
        params='809M',
        download_bytes=1_620_000_000,
        speed=4,
        accuracy=4,
        languages='multilingual',
        recommended=True,
        order=0,
        description='Near large-v3 accuracy at ~4× the speed. The best daily driver.',
    ),
    ModelInfo(
        id='mlx-community/whisper-large-v3-mlx',
        name='Large v3',
        family='whisper',
        params='1.55B',
        download_bytes=3_090_000_000,
        speed=2,
        accuracy=5,
        languages='multilingual',
        recommended=False,
        order=1,
        description='Highest accuracy. Best for accents, noise, and rare vocabulary.',
    ),
    ModelInfo(
        id='mlx-community/whisper-medium-mlx',
        name='Medium',
        family='whisper',
        params='769M',
        download_bytes=1_530_000_000,
        speed=3,
        accuracy=4,
        languages='multilingual',
        recommended=False,
        order=2,
        description='Balanced accuracy and speed when Turbo is unavailable.',
    ),
    ModelInfo(
        id='mlx-community/whisper-small-mlx',
        name='Small',
        family='whisper',
        params='244M',
        download_bytes=502_000_000,
        speed=4,
        accuracy=3,
        languages='multilingual',
        recommended=False,
        order=3,
        description='Light on storage and memory, good for short clear speech.',
    ),
    ModelInfo(
        id='mlx-community/whisper-base-mlx',
        name='Base',
        family='whisper',
        params='74M',
        download_bytes=152_000_000,
        speed=5,
        accuracy=2,
        languages='multilingual',
        recommended=False,
        order=4,
        description='Very fast and tiny. Handy on older hardware or tight disks.',
    ),
    ModelInfo(
        id='mlx-community/whisper-tiny-mlx',
        name='Tiny',
        family='whisper',
        params='39M',
        download_bytes=78_000_000,
        speed=5,
        accuracy=1,
        languages='multilingual',
        recommended=False,
        order=5,
        description='Smallest and fastest. Best for testing or quick notes.',
    ),
)

_BY_ID: dict[str, ModelInfo] = {m.id: m for m in CATALOG}

# Mirror of the ids for config.VALID_MODELS to validate against. Kept as a
# frozenset so callers can do membership checks without rebuilding it.
CATALOG_IDS: frozenset[str] = frozenset(_BY_ID)


def catalog() -> list[ModelInfo]:
    """Return the catalog in stable display order."""
    return sorted(CATALOG, key=lambda m: m.order)


def get(model_id: str) -> ModelInfo | None:
    """Look up a model by repo id, or None if unknown."""
    return _BY_ID.get(model_id)


def is_known(model_id: str) -> bool:
    return model_id in _BY_ID


# ── Cache + disk state ───────────────────────────────────────────────────


def _cache_root() -> str:
    """Resolve the Hugging Face hub cache dir, honouring env overrides.

    Read fresh on every call (not import-time) so tests can redirect the
    cache by setting `HF_HUB_CACHE` / `HF_HOME` to a temp dir — the same
    variables `huggingface_hub` itself honours when downloading.
    """
    env = os.environ.get('HF_HUB_CACHE') or os.environ.get('HUGGINGFACE_HUB_CACHE')
    if env:
        return env
    hf_home = os.environ.get('HF_HOME')
    if hf_home:
        return os.path.join(hf_home, 'hub')
    return os.path.expanduser('~/.cache/huggingface/hub')


def _repo_folder(model_id: str) -> str:
    """HF cache folder name for a repo id ('a/b' → 'models--a--b')."""
    return 'models--' + model_id.replace('/', '--')


def repo_dir(model_id: str) -> str:
    """Absolute path to the model's folder in the HF cache (may not exist)."""
    return os.path.join(_cache_root(), _repo_folder(model_id))


def bundled_dir(model_id: str) -> str | None:
    """Return a bundled copy of the model shipped inside the .app, if any.

    Mirrors the discovery in `transcriber._resolve_model_path` but only to
    answer "is a local copy present", so the catalog can report a bundled
    model as already-downloaded (and undeletable). Pure path checks only.
    """
    import sys

    leaf = model_id.split('/')[-1]
    candidates: list[str] = []

    env_dir = os.environ.get('SAY_BUNDLED_MODELS_DIR')
    if env_dir:
        candidates.append(env_dir)

    meipass = getattr(sys, '_MEIPASS', None)
    if meipass:
        candidates.append(os.path.join(meipass, 'models'))

    here = os.path.dirname(os.path.abspath(__file__))
    candidates.append(os.path.join(here, 'models'))

    cursor = here
    for _ in range(6):
        candidates.append(os.path.join(cursor, 'Resources', 'models'))
        parent = os.path.dirname(cursor)
        if parent == cursor:
            break
        cursor = parent

    for base in candidates:
        local = os.path.join(base, leaf)
        if os.path.isdir(local):
            return local
    return None


def _weights_present(repo: str) -> bool:
    """True if a snapshot under `repo` has real (non-empty) model weights.

    A repo folder can exist with only refs/partial blobs mid-download; we
    only call a model "downloaded" once a snapshot links to a weights file
    that actually resolves to a present blob of meaningful size.
    """
    snap_root = os.path.join(repo, 'snapshots')
    if not os.path.isdir(snap_root):
        return False
    try:
        revisions = os.listdir(snap_root)
    except OSError:
        return False
    for rev in revisions:
        rev_dir = os.path.join(snap_root, rev)
        if not os.path.isdir(rev_dir):
            continue
        for fn in os.listdir(rev_dir):
            if not fn.endswith(('.safetensors', '.npz')):
                continue
            real = os.path.realpath(os.path.join(rev_dir, fn))
            try:
                if os.path.getsize(real) > 1_000_000:
                    return True
            except OSError:
                continue
    return False


def is_downloaded(model_id: str) -> bool:
    """Whether the model is ready to load offline (cached or bundled)."""
    if bundled_dir(model_id) is not None:
        return True
    return _weights_present(repo_dir(model_id))


def _dir_real_bytes(path: str) -> int:
    """Sum of real (non-symlink) file sizes under `path`, recursively."""
    total = 0
    for root, _dirs, files in os.walk(path):
        for fn in files:
            fp = os.path.join(root, fn)
            try:
                st = os.lstat(fp)
            except OSError:
                continue
            # Count real bytes only — skip symlinks so HF blobs (real) are
            # tallied once and the snapshot symlinks pointing at them aren't
            # double-counted.
            if not os.path.islink(fp):
                total += st.st_size
    return total


def disk_usage_bytes(model_id: str) -> int:
    """Actual bytes this model occupies on disk right now (0 if absent).

    For a bundled model this is the bundle copy; otherwise it's the real
    blob bytes in the HF cache (including any in-flight ``*.incomplete``
    files, so a download in progress reports a rising number).
    """
    bundled = bundled_dir(model_id)
    if bundled is not None:
        return _dir_real_bytes(bundled)
    repo = repo_dir(model_id)
    blobs = os.path.join(repo, 'blobs')
    if os.path.isdir(blobs):
        return _dir_real_bytes(blobs)
    if os.path.isdir(repo):
        return _dir_real_bytes(repo)
    return 0


def to_dict(info: ModelInfo) -> dict:
    """Catalog entry plus live cache state, ready for JSON."""
    bundled = bundled_dir(info.id) is not None
    return {
        **asdict(info),
        'downloaded': is_downloaded(info.id),
        'bundled': bundled,
        'size_bytes': disk_usage_bytes(info.id),
    }
