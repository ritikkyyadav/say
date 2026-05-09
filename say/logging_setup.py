"""Centralized logging configuration for Say.

Logs go to:
  - stderr (so terminal/debug mode users see them)
  - ~/Library/Logs/Say/say.log, rotated at 10 MB × 5 generations.

Modules import `get_logger('component')` to obtain a child logger under
the `say` namespace. Call `configure_logging()` once at startup before
anything that logs.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from logging.handlers import RotatingFileHandler

LOG_DIR = os.path.expanduser('~/Library/Logs/Say')
LOG_FILE = os.path.join(LOG_DIR, 'say.log')

_FMT = logging.Formatter(
    '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)


def configure_logging(level: int = logging.INFO) -> logging.Logger:
    """Initialize the `say` logger. Idempotent — safe to call twice."""
    root = logging.getLogger('say')
    if root.handlers:
        root.setLevel(level)
        return root

    root.setLevel(level)
    root.propagate = False

    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        file_handler = RotatingFileHandler(
            LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=5
        )
        file_handler.setFormatter(_FMT)
        root.addHandler(file_handler)
    except Exception as exc:
        # Filesystem failure should not prevent the app from running.
        sys.stderr.write(
            f'[say] Warning: could not open log file {LOG_FILE}: {exc}\n'
        )

    stream_handler = logging.StreamHandler(sys.stderr)
    stream_handler.setFormatter(_FMT)
    root.addHandler(stream_handler)

    return root


def get_logger(name: str) -> logging.Logger:
    """Return a child logger under the `say` namespace."""
    return logging.getLogger(f'say.{name}')


def install_crash_handler() -> None:
    """Route uncaught exceptions (main thread + workers) into the log."""
    log = get_logger('crash')

    def main_hook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        log.error(
            'Uncaught exception in main thread',
            exc_info=(exc_type, exc_value, exc_tb),
        )

    sys.excepthook = main_hook

    if hasattr(threading, 'excepthook'):
        def thread_hook(args):
            log.error(
                'Uncaught exception in thread %s',
                args.thread.name if args.thread else 'unknown',
                exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
            )

        threading.excepthook = thread_hook
