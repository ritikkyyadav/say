"""Single-instance enforcement for Say.

Acquires a non-blocking advisory lock on a file under
~/Library/Application Support/Say/. If a second instance starts while
the first is still running, `acquire()` returns False and the caller
should exit cleanly. The lock is released automatically when the
process dies (the kernel releases flocks on FD close).
"""

from __future__ import annotations

import os
from typing import IO

from logging_setup import get_logger
from settings import SETTINGS_DIR

_log = get_logger('single_instance')

LOCK_PATH = os.path.join(SETTINGS_DIR, 'say.lock')


class _Lock:
    """Module-level container so the FD outlives the function call.

    fcntl.flock is released when the underlying FD is closed; we keep
    the file object on the module so the lock holds for process life.
    """

    fp: IO[str] | None = None


def acquire() -> bool:
    """Try to acquire the single-instance lock. Returns True on success."""
    import fcntl

    try:
        os.makedirs(SETTINGS_DIR, exist_ok=True)
    except Exception as exc:
        _log.warning('Could not create settings dir for lock: %s', exc)

    try:
        # Intentionally not a context manager — the FD must outlive this
        # function so the kernel keeps the flock for process lifetime.
        fp = open(LOCK_PATH, 'w', encoding='utf-8')  # noqa: SIM115
    except Exception as exc:
        _log.warning('Could not open lock file %s: %s', LOCK_PATH, exc)
        return True  # fail open — better to run than block

    try:
        fcntl.flock(fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        # Another instance holds the lock.
        fp.close()
        _log.info('Another Say instance is already running.')
        return False

    fp.write(str(os.getpid()))
    fp.flush()
    _Lock.fp = fp
    return True


def release() -> None:
    """Release the lock. Called on graceful shutdown; not strictly required."""
    import fcntl

    if _Lock.fp is None:
        return
    try:
        fcntl.flock(_Lock.fp.fileno(), fcntl.LOCK_UN)
        _Lock.fp.close()
    except Exception:
        _log.exception('Error releasing lock')
    finally:
        _Lock.fp = None
