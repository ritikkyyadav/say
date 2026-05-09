"""Temporarily neutralize macOS's Fn / globe-key system action.

macOS reads `AppleFnUsageType` from NSGlobalDomain on every Fn-key
press and runs the corresponding system action — Show Emoji &
Symbols, Change Input Source, Start Dictation, etc. On Apple Silicon
this dispatch happens *below* the CGEventTap layer, so even an active
HID-level tap cannot fully suppress it. The pragmatic workaround used
by every voice-dictation app on macOS is to flip this default to
"Do Nothing" for the lifetime of the app, then restore the user's
prior value on quit.

Crash safety: we persist the saved value to disk before mutating, so
even if the process dies hard (SIGKILL, panic, force-quit) the next
launch can still restore the original setting.

Reset semantics:
    Launch 1
        backup file absent  → read live value, write to backup, set 0
        backup file present → assume prior run crashed; keep backup,
                              re-apply 0 (don't overwrite with our 0)
    Quit / atexit
        backup file present → restore live value, delete backup
        backup file absent  → nothing to do

Settings opt-out: `config.OVERRIDE_FN_KEY_ACTION = False` skips the
override entirely. Users who don't have the globe-key set to anything
disruptive may prefer to leave their system defaults untouched.
"""

from __future__ import annotations

import json
import os
import threading

from logging_setup import get_logger
from settings import SETTINGS_DIR

_log = get_logger('fn_override')

BACKUP_PATH = os.path.join(SETTINGS_DIR, 'fn_usage_backup.json')
KEY = 'AppleFnUsageType'
TARGET_VALUE = 0  # 0 = Do Nothing
_lock = threading.Lock()


# ── NSUserDefaults helpers ───────────────────────────────────────

def _read_global() -> int | None:
    """Return the current AppleFnUsageType, or None if unset."""
    try:
        from Foundation import NSUserDefaults
        defaults = NSUserDefaults.standardUserDefaults()
        # objectForKey_ returns None when unset; integerForKey_ returns
        # 0 for both "unset" and "0", so we use the object form to
        # distinguish.
        value = defaults.objectForKey_(KEY)
        if value is None:
            return None
        return int(value)
    except Exception:
        _log.exception('Could not read %s', KEY)
        return None


def _write_global(value: int | None) -> bool:
    """Set AppleFnUsageType. Passing None removes the key."""
    try:
        from Foundation import NSUserDefaults
        defaults = NSUserDefaults.standardUserDefaults()
        if value is None:
            defaults.removeObjectForKey_(KEY)
        else:
            defaults.setInteger_forKey_(int(value), KEY)
        defaults.synchronize()
        return True
    except Exception:
        _log.exception('Could not write %s = %r', KEY, value)
        return False


# ── Backup file ──────────────────────────────────────────────────

def _save_backup(value: int | None) -> None:
    try:
        os.makedirs(SETTINGS_DIR, exist_ok=True)
        with open(BACKUP_PATH, 'w', encoding='utf-8') as f:
            json.dump({'value': value}, f)
    except Exception:
        _log.exception('Could not save Fn-usage backup')


def _load_backup() -> dict | None:
    try:
        if os.path.exists(BACKUP_PATH):
            with open(BACKUP_PATH, encoding='utf-8') as f:
                return json.load(f)
    except Exception:
        _log.exception('Could not load Fn-usage backup')
    return None


def _delete_backup() -> None:
    try:
        os.remove(BACKUP_PATH)
    except FileNotFoundError:
        pass
    except Exception:
        _log.exception('Could not delete Fn-usage backup')


# ── Public API ───────────────────────────────────────────────────

def acquire() -> None:
    """Save the user's AppleFnUsageType and force it to Do Nothing.

    Idempotent. If a backup file from a prior run exists we preserve
    it (the live value at this point is our own override, not the
    user's original).
    """
    with _lock:
        backup = _load_backup()
        if backup is None:
            current = _read_global()
            _save_backup(current)
            _log.info('Saved AppleFnUsageType=%r to %s', current, BACKUP_PATH)
        else:
            _log.info(
                'Backup already present (value=%r) — assuming prior crash; '
                'preserving original.', backup.get('value'),
            )

        if _write_global(TARGET_VALUE):
            _log.info(
                'Set AppleFnUsageType=0 (Do Nothing) for the duration of Say.'
            )


def release() -> None:
    """Restore AppleFnUsageType from the backup file. Idempotent."""
    with _lock:
        backup = _load_backup()
        if backup is None:
            return
        value = backup.get('value')
        if _write_global(value):
            _log.info('Restored AppleFnUsageType=%r', value)
        _delete_backup()
