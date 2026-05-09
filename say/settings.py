"""Persistent user settings for Say.

Settings live at:
    ~/Library/Application Support/Say/settings.json

Resolution rule: any key that exists in the JSON file overrides the
matching attribute on `config.py` (which holds shipped defaults).
Callers that already read `config.X` will see the user's override
because `apply_to_config()` is invoked at startup.

Programmatic API:
    settings.get('MODEL') -> str
    settings.set_value('MODEL', 'mlx-community/whisper-small-mlx')
    settings.is_first_run() / settings.mark_first_run_complete()
"""

from __future__ import annotations

import json
import os
from threading import Lock
from typing import Any

import config
from logging_setup import get_logger

_log = get_logger('settings')

SETTINGS_DIR = os.path.expanduser('~/Library/Application Support/Say')
SETTINGS_FILE = os.path.join(SETTINGS_DIR, 'settings.json')
FIRST_RUN_MARKER = os.path.join(SETTINGS_DIR, 'first_run_complete')

# Whitelist of keys that may be persisted. Prevents accidental writes
# of arbitrary keys (which could collide with internal config attrs).
PERSISTABLE_KEYS = {
    'MODEL',
    'LANGUAGE',
    'INJECT_METHOD',
    'INITIAL_PROMPT',
    'SHOW_NOTIFICATIONS',
    'SHOW_OVERLAY',
    'OVERLAY_POSITION',
    'PRE_ROLL_SECONDS',
    'MAX_RECORDING_SECONDS',
    'MIN_AUDIO_SECONDS',
    'DEVICE_WATCH_INTERVAL_S',
}

_lock = Lock()


def _ensure_dir() -> None:
    try:
        os.makedirs(SETTINGS_DIR, exist_ok=True)
    except Exception as exc:
        _log.warning('Could not create settings dir %s: %s', SETTINGS_DIR, exc)


def _read() -> dict[str, Any]:
    if not os.path.exists(SETTINGS_FILE):
        return {}
    try:
        with open(SETTINGS_FILE, encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        _log.warning('Could not parse %s: %s', SETTINGS_FILE, exc)
        return {}


def _write(data: dict[str, Any]) -> None:
    _ensure_dir()
    tmp = SETTINGS_FILE + '.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False, sort_keys=True)
        os.replace(tmp, SETTINGS_FILE)
    except Exception as exc:
        _log.warning('Could not persist %s: %s', SETTINGS_FILE, exc)
        try:
            os.remove(tmp)
        except Exception:
            pass


def all_settings() -> dict[str, Any]:
    """Return a fresh dict of persisted overrides (no defaults)."""
    with _lock:
        return _read()


def get(key: str, default: Any = None) -> Any:
    """Return the persisted override, falling back to the config default."""
    with _lock:
        data = _read()
    if key in data:
        return data[key]
    return getattr(config, key, default)


def set_value(key: str, value: Any) -> None:
    """Persist a setting and mirror it onto the live config module."""
    if key not in PERSISTABLE_KEYS:
        raise KeyError(f'{key!r} is not a persistable setting')
    with _lock:
        data = _read()
        data[key] = value
        _write(data)
    if hasattr(config, key):
        setattr(config, key, value)
    _log.info('Setting updated: %s = %r', key, value)


def apply_to_config() -> None:
    """Mirror every persisted override onto the live `config` module.

    Call once at startup, after `import config` but before validation.
    Unknown or non-attribute keys are ignored with a debug log.
    """
    with _lock:
        data = _read()
    for key, value in data.items():
        if key in PERSISTABLE_KEYS and hasattr(config, key):
            setattr(config, key, value)
        else:
            _log.debug('Skipping non-persistable setting %s', key)


def is_first_run() -> bool:
    return not os.path.exists(FIRST_RUN_MARKER)


def mark_first_run_complete() -> None:
    _ensure_dir()
    try:
        with open(FIRST_RUN_MARKER, 'w', encoding='utf-8') as f:
            f.write('')
    except Exception as exc:
        _log.warning('Could not write first-run marker: %s', exc)
