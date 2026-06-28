"""Local-only SQLite transcript store for Say.

Persists every successful dictation so the desktop UI can show history
and aggregate stats. The store lives in the user's application-data
directory:

  macOS    ~/Library/Application Support/Say/transcripts.db
  Windows  %APPDATA%/Say/transcripts.db
  Linux    ~/.local/share/Say/transcripts.db   (development only)

By default no audio is written — only the transcribed text and a small
set of metrics. When the user opts into saving recordings (see
`recordings.py` / `config.SAVE_RECORDINGS`), each row's `audio_path`
points at the on-disk WAV for that dictation. The store is completely
local — never uploaded, never read by anything outside the user's machine.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import threading
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

# ── Path resolution ─────────────────────────────────────────────────────────


def _app_data_dir() -> Path:
    """Resolve the per-user application data directory for Say.

    Prefers `platformdirs` when available (handles Windows/Linux), with
    a manual macOS-correct fallback so the engine still boots in a
    minimal install.
    """
    try:
        from platformdirs import user_data_dir  # type: ignore

        return Path(user_data_dir('Say', appauthor=False))
    except Exception:
        if sys.platform == 'darwin':
            return Path.home() / 'Library' / 'Application Support' / 'Say'
        if sys.platform.startswith('win'):
            base = os.environ.get('APPDATA') or str(Path.home() / 'AppData' / 'Roaming')
            return Path(base) / 'Say'
        return Path.home() / '.local' / 'share' / 'Say'


def default_db_path() -> Path:
    return _app_data_dir() / 'transcripts.db'


# ── Frontmost-app capture ───────────────────────────────────────────────────


def get_frontmost_app() -> str | None:
    """Return the bundle id (mac) or process name (other) of the active app.

    Returns None on any failure — capture is best-effort and must never
    interfere with the dictation pipeline.
    """
    if sys.platform != 'darwin':
        return None
    try:
        from AppKit import NSWorkspace  # type: ignore

        ws = NSWorkspace.sharedWorkspace()
        app = ws.frontmostApplication()
        if app is None:
            return None
        bundle_id = app.bundleIdentifier()
        if bundle_id:
            return str(bundle_id)
        name = app.localizedName()
        return str(name) if name else None
    except Exception:
        return None


# ── Schema + connection ─────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS transcripts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc TEXT NOT NULL,
    text TEXT NOT NULL,
    word_count INTEGER NOT NULL,
    audio_seconds REAL NOT NULL,
    inference_seconds REAL NOT NULL,
    model TEXT NOT NULL,
    inject_method TEXT NOT NULL,
    target_app TEXT,
    success INTEGER NOT NULL,
    audio_path TEXT
);
CREATE INDEX IF NOT EXISTS idx_ts ON transcripts(ts_utc);
"""


@dataclass(frozen=True)
class Transcript:
    id: int
    ts_utc: str
    text: str
    word_count: int
    audio_seconds: float
    inference_seconds: float
    model: str
    inject_method: str
    target_app: str | None
    success: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            'id': self.id,
            'ts_utc': self.ts_utc,
            'text': self.text,
            'word_count': self.word_count,
            'audio_seconds': self.audio_seconds,
            'inference_seconds': self.inference_seconds,
            'model': self.model,
            'inject_method': self.inject_method,
            'target_app': self.target_app,
            'success': self.success,
        }


def _row_to_transcript(row: sqlite3.Row) -> Transcript:
    return Transcript(
        id=row['id'],
        ts_utc=row['ts_utc'],
        text=row['text'],
        word_count=row['word_count'],
        audio_seconds=row['audio_seconds'],
        inference_seconds=row['inference_seconds'],
        model=row['model'],
        inject_method=row['inject_method'],
        target_app=row['target_app'],
        success=bool(row['success']),
    )


def _word_count(text: str) -> int:
    return len([w for w in text.split() if w])


# ── Store ────────────────────────────────────────────────────────────────────


class TranscriptStore:
    """Thread-safe SQLite wrapper. One connection per process, serialised by lock."""

    def __init__(self, path: str | os.PathLike[str] | None = None):
        self.path = Path(path) if path is not None else default_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._migrate()
        self._conn.commit()

    def _migrate(self) -> None:
        """Apply additive schema migrations to pre-existing databases.

        SQLite has no `ADD COLUMN IF NOT EXISTS`, so we inspect the table
        and add any missing columns. Safe to run on every startup.
        """
        cols = {r['name'] for r in self._conn.execute('PRAGMA table_info(transcripts)')}
        if 'audio_path' not in cols:
            self._conn.execute('ALTER TABLE transcripts ADD COLUMN audio_path TEXT')

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass

    # -- Writes -------------------------------------------------------------

    def insert(
        self,
        *,
        text: str,
        audio_seconds: float,
        inference_seconds: float,
        model: str,
        inject_method: str,
        target_app: str | None,
        success: bool = True,
        ts_utc: str | None = None,
    ) -> int:
        ts = ts_utc or datetime.now(UTC).isoformat(timespec='seconds').replace('+00:00', 'Z')
        wc = _word_count(text)
        with self._lock:
            cur = self._conn.execute(
                """
                INSERT INTO transcripts
                  (ts_utc, text, word_count, audio_seconds, inference_seconds,
                   model, inject_method, target_app, success)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ts,
                    text,
                    wc,
                    float(audio_seconds),
                    float(inference_seconds),
                    model,
                    inject_method,
                    target_app,
                    1 if success else 0,
                ),
            )
            self._conn.commit()
            return int(cur.lastrowid or 0)

    def set_audio_path(self, transcript_id: int, audio_path: str) -> None:
        """Link a saved WAV to its transcript row."""
        with self._lock:
            self._conn.execute(
                'UPDATE transcripts SET audio_path = ? WHERE id = ?',
                (audio_path, int(transcript_id)),
            )
            self._conn.commit()

    def clear_audio_path(self, transcript_id: int) -> None:
        """Forget a recording (row stays; only the audio link is removed)."""
        with self._lock:
            self._conn.execute(
                'UPDATE transcripts SET audio_path = NULL WHERE id = ?',
                (int(transcript_id),),
            )
            self._conn.commit()

    def audio_path_for(self, transcript_id: int) -> str | None:
        with self._lock:
            row = self._conn.execute(
                'SELECT audio_path FROM transcripts WHERE id = ?',
                (int(transcript_id),),
            ).fetchone()
        return row['audio_path'] if row and row['audio_path'] else None

    # -- Reads --------------------------------------------------------------

    def recordings(self, *, limit: int | None = None) -> list[dict[str, Any]]:
        """Return rows that have a saved recording, newest first.

        Includes the full metadata needed by the Recordings tab and by the
        ZIP export manifest. `limit=None` returns every recording (used for
        export + retention sweeps).
        """
        sql = (
            'SELECT id, ts_utc, text, word_count, audio_seconds, inference_seconds, '
            'model, inject_method, target_app, success, audio_path '
            'FROM transcripts WHERE audio_path IS NOT NULL ORDER BY ts_utc DESC'
        )
        params: tuple[Any, ...] = ()
        if limit is not None:
            sql += ' LIMIT ?'
            params = (max(1, int(limit)),)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [
            {
                'id': r['id'],
                'ts_utc': r['ts_utc'],
                'text': r['text'],
                'word_count': r['word_count'],
                'audio_seconds': r['audio_seconds'],
                'inference_seconds': r['inference_seconds'],
                'model': r['model'],
                'inject_method': r['inject_method'],
                'target_app': r['target_app'],
                'success': bool(r['success']),
                'audio_path': r['audio_path'],
            }
            for r in rows
        ]

    def list(self, limit: int = 200, before: str | None = None) -> list[Transcript]:
        """Return transcripts in reverse-chronological order."""
        limit = max(1, min(int(limit), 1000))
        with self._lock:
            if before:
                cur = self._conn.execute(
                    """
                    SELECT * FROM transcripts
                    WHERE ts_utc < ?
                    ORDER BY ts_utc DESC
                    LIMIT ?
                    """,
                    (before, limit),
                )
            else:
                cur = self._conn.execute(
                    'SELECT * FROM transcripts ORDER BY ts_utc DESC LIMIT ?',
                    (limit,),
                )
            return [_row_to_transcript(r) for r in cur.fetchall()]

    def count(self) -> int:
        with self._lock:
            cur = self._conn.execute('SELECT COUNT(*) AS n FROM transcripts')
            return int(cur.fetchone()['n'])

    def summary(self, *, today: date | None = None) -> dict[str, Any]:
        """Aggregate stats for the Insights screen."""
        today = today or datetime.now(UTC).date()
        cutoff_30 = (datetime.combine(today, datetime.min.time()) - timedelta(days=30)).isoformat() + 'Z'
        cutoff_60 = (datetime.combine(today, datetime.min.time()) - timedelta(days=60)).isoformat() + 'Z'

        with self._lock:
            total = self._conn.execute(
                'SELECT COALESCE(SUM(word_count), 0) AS w FROM transcripts WHERE success = 1'
            ).fetchone()['w']

            row_30 = self._conn.execute(
                """
                SELECT COALESCE(SUM(word_count), 0) AS w,
                       COALESCE(SUM(audio_seconds), 0.0) AS s
                FROM transcripts
                WHERE success = 1 AND ts_utc >= ?
                """,
                (cutoff_30,),
            ).fetchone()
            words_30 = int(row_30['w'])
            seconds_30 = float(row_30['s'])

            words_prev = int(self._conn.execute(
                """
                SELECT COALESCE(SUM(word_count), 0) AS w
                FROM transcripts
                WHERE success = 1 AND ts_utc >= ? AND ts_utc < ?
                """,
                (cutoff_60, cutoff_30),
            ).fetchone()['w'])

            day_rows = self._conn.execute(
                """
                SELECT substr(ts_utc, 1, 10) AS d
                FROM transcripts
                WHERE success = 1
                ORDER BY ts_utc DESC
                """
            ).fetchall()

        wpm_30d = (words_30 / (seconds_30 / 60.0)) if seconds_30 > 0 else 0.0

        # Streak — consecutive days ending today (or the most recent day) with at least one transcript.
        days_seen: set[date] = set()
        for r in day_rows:
            try:
                days_seen.add(date.fromisoformat(r['d']))
            except Exception:
                continue
        streak = 0
        cursor = today
        # Allow a one-day grace at the head: streak counts from today backward;
        # if user hasn't dictated today yet, anchor to yesterday so the streak
        # from prior days is still visible.
        if cursor not in days_seen and (cursor - timedelta(days=1)) in days_seen:
            cursor = cursor - timedelta(days=1)
        while cursor in days_seen:
            streak += 1
            cursor -= timedelta(days=1)

        return {
            'total_words': int(total),
            'wpm_30d': round(wpm_30d, 1),
            'streak_days': streak,
            'fixes_count': 0,  # TODO: engine doesn't emit corrections yet.
            'words_this_month': words_30,
            'words_prev_month': words_prev,
            'transcript_count': self.count(),
        }

    def desktop_usage(self, *, days: int = 30, today: date | None = None) -> list[dict[str, Any]]:
        """Per-app usage rollup over the last `days` days, sorted by count desc."""
        today = today or datetime.now(UTC).date()
        cutoff = (datetime.combine(today, datetime.min.time()) - timedelta(days=days)).isoformat() + 'Z'
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT target_app, COUNT(*) AS n
                FROM transcripts
                WHERE success = 1 AND ts_utc >= ?
                GROUP BY target_app
                ORDER BY n DESC
                """,
                (cutoff,),
            ).fetchall()

        total = sum(int(r['n']) for r in rows) or 1
        return [
            {
                'target_app': r['target_app'] or 'Unknown',
                'count': int(r['n']),
                'share': round(int(r['n']) / total, 4),
            }
            for r in rows
        ]

    def heatmap(self, *, days: int = 180, today: date | None = None) -> list[dict[str, Any]]:
        """Per-day transcript counts for the last `days` days. Includes zeros."""
        today = today or datetime.now(UTC).date()
        start = today - timedelta(days=days - 1)
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT substr(ts_utc, 1, 10) AS d, COUNT(*) AS n
                FROM transcripts
                WHERE success = 1 AND ts_utc >= ?
                GROUP BY d
                """,
                (start.isoformat() + 'T00:00:00Z',),
            ).fetchall()

        counts: dict[date, int] = {}
        for r in rows:
            try:
                counts[date.fromisoformat(r['d'])] = int(r['n'])
            except Exception:
                continue

        out: list[dict[str, Any]] = []
        d = start
        while d <= today:
            out.append({'date': d.isoformat(), 'count': counts.get(d, 0)})
            d += timedelta(days=1)
        return out


# ── Singleton ──────────────────────────────────────────────────────────────

_store: TranscriptStore | None = None
_store_lock = threading.Lock()


def get_store() -> TranscriptStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = TranscriptStore()
        return _store


def reset_store_for_tests(path: str | os.PathLike[str] | None = None) -> TranscriptStore:
    """Replace the singleton with one bound to `path`. Tests only."""
    global _store
    with _store_lock:
        if _store is not None:
            _store.close()
        _store = TranscriptStore(path=path)
        return _store
