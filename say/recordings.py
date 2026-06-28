"""On-disk audio receipts for Say.

When the user opts in (`config.SAVE_RECORDINGS`), the controller hands the
raw capture of every successful dictation to `save_for_transcript()`, which
writes a 16 kHz mono WAV next to the SQLite store
(`~/Library/Application Support/Say/recordings/<id>.wav`) and links it to its
transcript row via `store.set_audio_path()`.

Everything here is **local**. Recordings are never uploaded; "export" only
ever produces a ZIP file on this machine (see `export_zip`). The directory
is derived from the active store's path so tests transparently redirect it.

All higher-level operations take the `TranscriptStore` explicitly (rather
than reaching for the singleton) so the FastAPI layer can inject its own
store in tests, exactly like the rest of the server.
"""

from __future__ import annotations

import csv
import io
import json
import os
import wave
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import config
import numpy as np
import settings
from logging_setup import get_logger

if TYPE_CHECKING:
    from server.store import TranscriptStore

_log = get_logger('recordings')

_EXPORT_README = (
    "Oceanus / Say — exported recordings\n"
    "===================================\n\n"
    "This archive was created locally on your Mac. Nothing here was uploaded.\n\n"
    "  clips/         one WAV per dictation (16 kHz mono)\n"
    "  manifest.csv   spreadsheet-friendly index of every clip + its transcript\n"
    "  manifest.json  the same index as JSON\n\n"
    "Each manifest row links a clip file to the text it produced, when it was\n"
    "recorded, which app it was typed into, the model used, and timing.\n"
)


# ── Paths ────────────────────────────────────────────────────────────────────


def _base_dir(store: TranscriptStore) -> Path:
    """The Say application-data directory that holds the store."""
    return Path(store.path).parent


def recordings_dir(store: TranscriptStore) -> Path:
    d = _base_dir(store) / 'recordings'
    d.mkdir(parents=True, exist_ok=True)
    return d


def exports_dir(store: TranscriptStore) -> Path:
    d = _base_dir(store) / 'exports'
    d.mkdir(parents=True, exist_ok=True)
    return d


def wav_path(store: TranscriptStore, transcript_id: int) -> Path:
    return recordings_dir(store) / f'{int(transcript_id)}.wav'


# ── WAV write ────────────────────────────────────────────────────────────────


def _write_wav(path: Path, audio: Any, sample_rate: int) -> None:
    """Write a mono 16-bit PCM WAV atomically (tmp + os.replace)."""
    arr = np.asarray(audio)
    if arr.ndim > 1:
        arr = arr.reshape(-1)
    if arr.dtype.kind == 'f':
        pcm = (np.clip(arr, -1.0, 1.0) * 32767.0).astype('<i2')
    elif arr.dtype == np.int16:
        pcm = arr.astype('<i2')
    else:
        pcm = arr.astype('<i2')

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    with wave.open(str(tmp), 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(sample_rate))
        w.writeframes(pcm.tobytes())
    os.replace(tmp, path)


def save_for_transcript(
    store: TranscriptStore,
    transcript_id: int,
    audio: Any,
    sample_rate: int | None = None,
) -> str | None:
    """Persist `audio` as the WAV for `transcript_id` and link it in the store.

    Returns the path on success, None on any failure (callers must treat
    recording as best-effort and never let it break the dictation pipeline).
    """
    try:
        rate = int(sample_rate or config.SAMPLE_RATE)
        path = wav_path(store, transcript_id)
        _write_wav(path, audio, rate)
        store.set_audio_path(transcript_id, str(path))
        return str(path)
    except Exception:
        _log.exception('Failed to save recording for transcript %s', transcript_id)
        return None


# ── Reads / sizes ────────────────────────────────────────────────────────────


def read_bytes(store: TranscriptStore, transcript_id: int) -> bytes | None:
    p = wav_path(store, transcript_id)
    if not p.exists():
        return None
    try:
        return p.read_bytes()
    except Exception:
        return None


def file_size(store: TranscriptStore, transcript_id: int) -> int:
    p = wav_path(store, transcript_id)
    try:
        return p.stat().st_size if p.exists() else 0
    except Exception:
        return 0


def total_bytes(store: TranscriptStore) -> int:
    total = 0
    try:
        for p in recordings_dir(store).glob('*.wav'):
            try:
                total += p.stat().st_size
            except Exception:
                continue
    except Exception:
        return 0
    return total


def count(store: TranscriptStore) -> int:
    try:
        return sum(1 for _ in recordings_dir(store).glob('*.wav'))
    except Exception:
        return 0


# ── Deletes ──────────────────────────────────────────────────────────────────


def delete_recording(store: TranscriptStore, transcript_id: int) -> bool:
    """Delete one clip's WAV and unlink it from its transcript row."""
    p = wav_path(store, transcript_id)
    existed = p.exists()
    try:
        p.unlink(missing_ok=True)
    except Exception:
        _log.exception('Failed to delete recording %s', transcript_id)
    try:
        store.clear_audio_path(transcript_id)
    except Exception:
        _log.exception('Failed to clear audio_path for %s', transcript_id)
    return existed


def delete_all(store: TranscriptStore) -> int:
    """Delete every saved WAV and clear all audio links. Returns # removed."""
    removed = 0
    try:
        for p in recordings_dir(store).glob('*.wav'):
            try:
                p.unlink()
                removed += 1
            except Exception:
                continue
    except Exception:
        _log.exception('Failed during delete_all')
    for r in store.recordings():
        try:
            store.clear_audio_path(r['id'])
        except Exception:
            continue
    return removed


# ── Retention ────────────────────────────────────────────────────────────────


def _parse_ts(ts: str) -> datetime | None:
    try:
        return datetime.fromisoformat(ts.replace('Z', '+00:00'))
    except Exception:
        return None


def enforce_retention(store: TranscriptStore) -> int:
    """Prune recordings according to the active policy. Returns # deleted.

    'keep_all' -> no-op. 'days' -> drop clips older than the cutoff.
    'size' -> keep newest clips within the byte budget, drop the oldest.
    """
    mode = getattr(config, 'RECORDINGS_RETENTION_MODE', 'keep_all')
    rows = store.recordings()  # newest first
    to_delete: list[int] = []

    if mode == 'days':
        days = max(1, int(getattr(config, 'RECORDINGS_RETENTION_DAYS', 30)))
        cutoff = datetime.now(UTC) - timedelta(days=days)
        for r in rows:
            ts = _parse_ts(r['ts_utc'])
            if ts is not None and ts < cutoff:
                to_delete.append(r['id'])
    elif mode == 'size':
        budget = max(0, int(getattr(config, 'RECORDINGS_MAX_BYTES', 0)))
        running = 0
        for r in rows:  # newest first — keep until the budget is spent
            size = file_size(store, r['id'])
            if running + size <= budget:
                running += size
            else:
                to_delete.append(r['id'])

    for tid in to_delete:
        delete_recording(store, tid)
    if to_delete:
        _log.info('Retention (%s) pruned %d recording(s)', mode, len(to_delete))
    return len(to_delete)


# ── Config (settings-backed) ─────────────────────────────────────────────────

_MIN_SIZE_BUDGET = 100 * 1024 * 1024  # never let the cap drop below 100 MB


def recordings_config(store: TranscriptStore) -> dict[str, Any]:
    return {
        'save_recordings': bool(getattr(config, 'SAVE_RECORDINGS', False)),
        'retention_mode': getattr(config, 'RECORDINGS_RETENTION_MODE', 'keep_all'),
        'retention_days': int(getattr(config, 'RECORDINGS_RETENTION_DAYS', 30)),
        'max_bytes': int(getattr(config, 'RECORDINGS_MAX_BYTES', 0)),
        'total_bytes': total_bytes(store),
        'count': count(store),
    }


def apply_recordings_config(
    store: TranscriptStore,
    *,
    save_recordings: bool | None = None,
    retention_mode: str | None = None,
    retention_days: int | None = None,
    max_bytes: int | None = None,
) -> dict[str, Any]:
    """Persist the provided recording settings, prune, return fresh config.

    Raises ValueError on an unknown retention mode.
    """
    valid_modes = getattr(config, 'VALID_RETENTION_MODES', {'keep_all', 'days', 'size'})
    if retention_mode is not None and retention_mode not in valid_modes:
        raise ValueError(f'Unknown retention mode: {retention_mode!r}')

    if save_recordings is not None:
        settings.set_value('SAVE_RECORDINGS', bool(save_recordings))
    if retention_mode is not None:
        settings.set_value('RECORDINGS_RETENTION_MODE', retention_mode)
    if retention_days is not None:
        settings.set_value('RECORDINGS_RETENTION_DAYS', max(1, int(retention_days)))
    if max_bytes is not None:
        settings.set_value('RECORDINGS_MAX_BYTES', max(_MIN_SIZE_BUDGET, int(max_bytes)))

    enforce_retention(store)
    return recordings_config(store)


# ── Listing (for the Recordings tab) ─────────────────────────────────────────


def list_recordings(store: TranscriptStore, limit: int | None = 500) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in store.recordings(limit=limit):
        size = file_size(store, r['id'])
        out.append({
            'id': r['id'],
            'ts_utc': r['ts_utc'],
            'text': r['text'],
            'word_count': r['word_count'],
            'audio_seconds': r['audio_seconds'],
            'model': r['model'],
            'inject_method': r['inject_method'],
            'target_app': r['target_app'],
            'success': r['success'],
            'size_bytes': size,
            'has_audio': size > 0,
        })
    return out


# ── Export (local ZIP) ───────────────────────────────────────────────────────

_MANIFEST_FIELDS = [
    'id', 'file', 'timestamp_utc', 'timestamp_local', 'text', 'word_count',
    'audio_seconds', 'inference_seconds', 'model', 'inject_method',
    'target_app', 'success',
]


def _local_str(ts_utc: str) -> str:
    dt = _parse_ts(ts_utc)
    if dt is None:
        return ts_utc
    try:
        return dt.astimezone().strftime('%Y-%m-%d %H:%M:%S')
    except Exception:
        return ts_utc


def export_zip(store: TranscriptStore) -> dict[str, Any]:
    """Bundle every saved recording + a manifest into a local ZIP.

    Returns {path, count, bytes}. `path` is None when there is nothing to
    export. No network access — the archive is written under the Say data
    directory's `exports/` folder for the shell to reveal in Finder.
    """
    rows = store.recordings()  # newest first
    if not rows:
        return {'path': None, 'count': 0, 'bytes': 0}

    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    dest = exports_dir(store) / f'Oceanus-Recordings-{stamp}.zip'

    manifest: list[dict[str, Any]] = []
    with zipfile.ZipFile(dest, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
        for r in rows:
            p = wav_path(store, r['id'])
            if not p.exists():
                continue
            arcname = f'clips/recording_{int(r["id"]):06d}.wav'
            zf.write(p, arcname)
            manifest.append({
                'id': r['id'],
                'file': arcname,
                'timestamp_utc': r['ts_utc'],
                'timestamp_local': _local_str(r['ts_utc']),
                'text': r['text'],
                'word_count': r['word_count'],
                'audio_seconds': r['audio_seconds'],
                'inference_seconds': r['inference_seconds'],
                'model': r['model'],
                'inject_method': r['inject_method'],
                'target_app': r['target_app'] or '',
                'success': r['success'],
            })

        if not manifest:
            # Every referenced file was missing — don't leave an empty ZIP.
            pass
        else:
            csv_buf = io.StringIO()
            writer = csv.DictWriter(csv_buf, fieldnames=_MANIFEST_FIELDS)
            writer.writeheader()
            writer.writerows(manifest)
            zf.writestr('manifest.csv', csv_buf.getvalue())
            zf.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2))
            zf.writestr('README.txt', _EXPORT_README)

    if not manifest:
        try:
            dest.unlink(missing_ok=True)
        except Exception:
            pass
        return {'path': None, 'count': 0, 'bytes': 0}

    return {'path': str(dest), 'count': len(manifest), 'bytes': dest.stat().st_size}
