"""Local-only metrics ledger for Say.

Appends one JSON object per line to ~/Library/Logs/Say/metrics.jsonl.
Used for self-diagnosis (latency, success rate). Never uploaded —
never even read by the running app — and rotated by hand if the user
wants to reset it.

Records have shape:
  {"ts": <unix>, "event": "transcription", "audio_seconds": 3.2,
   "inference_seconds": 1.1, "model": "...", "success": true,
   "method": "accessibility"}
or
  {"ts": <unix>, "event": "error", "stage": "transcription",
   "message": "..."}
"""

from __future__ import annotations

import json
import os
import time
from threading import Lock

from logging_setup import LOG_DIR, get_logger

LEDGER_PATH = os.path.join(LOG_DIR, 'metrics.jsonl')

_log = get_logger('metrics')


class MetricsLedger:
    def __init__(self, path: str = LEDGER_PATH):
        self._path = path
        self._lock = Lock()
        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
        except Exception:
            # Logging will warn separately; recording is best-effort.
            pass

    def record(self, **fields) -> None:
        """Append a record. Best-effort — never raises."""
        record = {'ts': round(time.time(), 3), **fields}
        try:
            line = json.dumps(record, ensure_ascii=False)
        except Exception as exc:
            _log.warning('Could not serialize metric: %s', exc)
            return

        with self._lock:
            try:
                with open(self._path, 'a', encoding='utf-8') as f:
                    f.write(line + '\n')
            except Exception as exc:
                _log.warning('Could not write metric to %s: %s', self._path, exc)


_ledger: MetricsLedger | None = None


def get_ledger() -> MetricsLedger:
    global _ledger
    if _ledger is None:
        _ledger = MetricsLedger()
    return _ledger
