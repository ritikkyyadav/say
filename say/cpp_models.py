"""Pinned whisper.cpp weights, kept outside the Hugging Face snapshot cache."""
from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ModelAsset:
    filename: str
    size: int
    sha256: str


REVISION = '5359861c739e955e79d9a303bcbc70fb988958b1'
ASSETS = {
    'whisper-cpp/tiny': ModelAsset(
        'ggml-tiny.bin', 77_691_713,
        'be07e048e1e599ad46341c8d2a135645097a538221678b7acdd1b1919c6e1b21',
    ),
    'whisper-cpp/small': ModelAsset(
        'ggml-small.bin', 487_601_967,
        '1be3a9b2063867b937e64e2ec7483364a79917e157fa98c5d94b5c1fffea987b',
    ),
}


class DownloadCancelled(Exception):
    pass


def cache_root() -> Path:
    custom = os.environ.get('SAY_WHISPER_CPP_CACHE')
    if custom:
        return Path(custom).expanduser()
    configured = os.environ.get('XDG_CACHE_HOME', '')
    base = Path(configured) if configured and Path(configured).is_absolute() else Path.home() / '.cache'
    return base / 'Say' / 'whisper-cpp'


def model_path(model_id: str) -> Path:
    if model_id not in ASSETS:
        raise ValueError(f'Unknown CPU Whisper model: {model_id}')
    return cache_root() / model_id.rsplit('/', 1)[-1] / ASSETS[model_id].filename


def _checked_path(model_id: str) -> Path:
    path = model_path(model_id)
    if not path.resolve().is_relative_to(cache_root().resolve()):
        raise ValueError('The model path escapes its cache directory.')
    return path


def is_downloaded(model_id: str) -> bool:
    try:
        path = _checked_path(model_id)
        asset = ASSETS[model_id]
        stamp = json.loads(path.with_suffix('.verified.json').read_text())
        stat = path.stat()
        return (path.is_file() and stat.st_size == asset.size
                and stamp.get('sha256') == asset.sha256
                and stamp.get('size') == asset.size
                and stamp.get('mtime_ns') == stat.st_mtime_ns)
    except (OSError, ValueError, KeyError, AttributeError):
        return False


def ensure_downloaded(
    model_id: str, progress: Callable[[int, int], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> Path:
    import fcntl

    import requests

    path = _checked_path(model_id)
    asset = ASSETS[model_id]
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    path.parent.chmod(0o700)
    stopped = cancelled or (lambda: False)
    part = path.with_suffix('.part')
    with (path.parent / 'download.lock').open('a+b') as lock:
        while True:
            if stopped():
                raise DownloadCancelled()
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                time.sleep(0.1)
        if is_downloaded(model_id):
            if progress:
                progress(asset.size, asset.size)
            return path
        try:
            url = f'https://huggingface.co/ggerganov/whisper.cpp/resolve/{REVISION}/{asset.filename}'
            digest = hashlib.sha256()
            received = 0
            flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, 'O_NOFOLLOW', 0)
            with requests.get(url, stream=True, timeout=(10, 15)) as response:
                response.raise_for_status()
                with os.fdopen(os.open(part, flags, 0o600), 'wb') as target:
                    if progress:
                        progress(0, asset.size)
                    for chunk in response.iter_content(chunk_size=256 * 1024):
                        if stopped():
                            raise DownloadCancelled()
                        if not chunk:
                            continue
                        received += len(chunk)
                        if received > asset.size:
                            raise ValueError('Model download exceeds the expected file size.')
                        target.write(chunk)
                        digest.update(chunk)
                        if progress:
                            progress(received, asset.size)
                    target.flush()
                    os.fsync(target.fileno())
            if stopped():
                raise DownloadCancelled()
            if received != asset.size or digest.hexdigest() != asset.sha256:
                raise ValueError('Model integrity check failed; the download was not installed.')
            os.replace(part, path)
            stamp = path.with_suffix('.verified.json')
            temporary = stamp.with_suffix('.tmp')
            temporary.write_text(json.dumps({
                'sha256': asset.sha256, 'size': asset.size,
                'mtime_ns': path.stat().st_mtime_ns, 'revision': REVISION,
            }))
            temporary.chmod(0o600)
            os.replace(temporary, stamp)
            return path
        finally:
            part.unlink(missing_ok=True)
