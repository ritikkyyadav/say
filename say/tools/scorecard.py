#!/usr/bin/env python3
"""The product scorecard: what Say actually scores, from what it actually did.

Why this exists
---------------
"Say is better than X" is an argument until there is a number behind it, and a
number is only honest if it comes from the product's own record. This reads
that record — the press ledger, the transcript store, the review labels, the
engine log — and turns it into the six-category score the product plan is
written against (reliability 25, speed 15, accuracy 20, convenience 20, reach
10, footprint 10).

Two rules, both from how this repository has been burned before:

  * **Unmeasured is not good.** A category without enough data scores 0 and
    says "unmeasured", instead of borrowing a hopeful number.
  * **The formulas are written down.** Each score is a straight line between a
    stated floor and the plan's bar, so moving a number means changing a
    visible constant, not a judgement.

It only reads. Nothing it computes is written anywhere.

Usage
-----
    python say/tools/scorecard.py             # the last 30 days
    python say/tools/scorecard.py --days 7
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Presses that carry no evidence about reliability: the person changed their
# mind, the machine slept, or the key was tapped without anything said.
NOT_A_TEST = frozenset({'cancelled', 'cancelled_by_system', 'silence', 'too_short'})

# Minimum evidence before a category is scored at all.
MIN_PRESSES = 30
MIN_LONG_CLIPS = 10
MIN_REVIEWED = 40
MIN_HINDI_REVIEWED = 30


@dataclass(frozen=True)
class Category:
    name: str
    weight: int
    points: float
    measured: bool
    evidence: str


def _line(value: float, floor: float, bar: float) -> float:
    """0 at `floor`, 1 at `bar`, clamped. Works whichever way round they are."""
    if bar == floor:
        return 1.0 if value == bar else 0.0
    return max(0.0, min(1.0, (value - floor) / (bar - floor)))


def _p(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * q))]


# ── The six categories. Pure: measurements in, a Category out. ───────────


def reliability(outcomes: Iterable[str]) -> Category:
    """Share of presses with something said that ended `delivered`.

    Floor 90%, bar 99.5% (the plan's bar). `held` counts against: the words
    were kept, but they did not land where the person was looking.
    """
    counts = Counter(outcomes)
    tested = {k: v for k, v in counts.items() if k not in NOT_A_TEST}
    n = sum(tested.values())
    if n < MIN_PRESSES:
        return Category('Reliability', 25, 0.0, False,
                        f'{n} presses with speech recorded; need {MIN_PRESSES}')
    rate = tested.get('delivered', 0) / n
    misses = ', '.join(f'{k} {v}' for k, v in sorted(tested.items()) if k != 'delivered')
    return Category('Reliability', 25, 25 * _line(rate, 0.90, 0.995), True,
                    f'{rate:.1%} of {n} delivered' + (f' (missed: {misses})' if misses else ''))


def speed(long_clip_waits: list[float]) -> Category:
    """p90 wait after release on clips of 20 s or more. Floor 6 s, bar 1.5 s."""
    if len(long_clip_waits) < MIN_LONG_CLIPS:
        return Category('Speed', 15, 0.0, False,
                        f'{len(long_clip_waits)} clips of 20 s+; need {MIN_LONG_CLIPS}')
    p50, p90 = _p(long_clip_waits, 0.5), _p(long_clip_waits, 0.9)
    return Category('Speed', 15, 15 * _line(p90, 6.0, 1.5), True,
                    f'wait after release on 20 s+ clips: median {p50:.1f}s, p90 {p90:.1f}s')


def accuracy(review: Mapping[str, Any]) -> Category:
    """Word error rate on clips the user checked. Floor 15%, bar 3%.

    The reviewed clips are chosen because they look wrong, so this is a
    pessimistic estimate of everyday accuracy — the right direction to err.
    """
    reviewed = int(review.get('reviewed', 0))
    if reviewed < MIN_REVIEWED:
        return Category('Accuracy and trust', 20, 0.0, False,
                        f'{reviewed} clips reviewed; need {MIN_REVIEWED}')
    rate = float(review.get('word_rate', 0.0))
    return Category('Accuracy and trust', 20, 20 * _line(rate, 0.15, 0.03), True,
                    f'{rate:.1%} of words wrong across {reviewed} reviewed clips '
                    f'(selected because they looked doubtful)')


# What the product can do, each worth part of the convenience score. Kept as
# a declared list on purpose: a feature counts when it ships, and the place
# that says so should be one line someone has to change.
CONVENIENCE: tuple[tuple[str, int, str], ...] = (
    ('Hold or toggle with one key', 3, 'shipped'),
    ('Fix a transcript in the app', 2, 'shipped'),
    ('Re-insert the last dictation', 2, 'shipped'),     # Fn+V, installed 2026-10-04
    ('Undo the last dictation', 2, 'shipped'),          # Fn+Z, installed 2026-10-04
    ('Every failure explained', 2, 'shipped'),          # glyph shake + notification
    ('Approve learned vocabulary', 2, 'planned'),
    ('Snippets', 2, 'planned'),                         # built, not yet installed
    ('Voice editing of selected text', 4, 'planned'),
    ('Two-minute onboarding', 1, 'planned'),
)


def convenience(features: Iterable[tuple[str, int, str]] = CONVENIENCE) -> Category:
    rows = list(features)
    have = [(n, w) for n, w, state in rows if state == 'shipped']
    missing = [n for n, _w, state in rows if state != 'shipped']
    points = min(20, sum(w for _n, w in have))
    return Category('Convenience', 20, float(points), True,
                    f'{len(have)} of {len(rows)} shipped; next: {", ".join(missing[:3]) or "none"}')


def reach(hindi_reviewed: int) -> Category:
    """Mac 4, Android 2, Hindi measured 2, Windows/iPhone 2 (deferred)."""
    points = 4 + 2
    parts = ['Mac', 'Android']
    if hindi_reviewed >= MIN_HINDI_REVIEWED:
        points += 2
        parts.append('Hindi measured')
    return Category('Reach', 10, float(points), True,
                    ' + '.join(parts) + f'; Hindi clips reviewed: {hindi_reviewed}; '
                    'Windows and iPhone deferred')


def footprint(idle_rss_mb: float | None) -> Category:
    """No caps or subscription: 4. Idle memory: 6, from 2 GB (floor) to 400 MB (bar)."""
    if idle_rss_mb is None:
        return Category('Footprint', 10, 4.0, False,
                        'no word caps or subscription (4); idle memory unmeasured — '
                        'the engine is not running')
    return Category('Footprint', 10, 4 + 6 * _line(idle_rss_mb, 2048, 400), True,
                    f'no caps (4); engine physical footprint now {idle_rss_mb:,.0f} MB '
                    '(GPU memory included)')


# ── Reading the record ───────────────────────────────────────────────────


def press_outcomes(ledger_path: str, since: float) -> list[str]:
    out: list[str] = []
    try:
        with open(ledger_path, encoding='utf-8') as f:
            for line in f:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get('event') == 'press' and float(row.get('ts', 0)) >= since:
                    out.append(str(row.get('outcome')))
    except OSError:
        pass
    return out


def parse_footprint(text: str) -> float | None:
    """MB from `footprint <pid>` output ("phys_footprint: 3781 MB"). Pure."""
    import re

    m = re.search(r'phys_footprint:\s*([\d.]+)\s*(KB|MB|GB)', text)
    if m is None:
        return None
    value = float(m[1])
    return {'KB': value / 1024, 'MB': value, 'GB': value * 1024}[m[2]]


def engine_footprint_mb() -> float | None:
    """Physical footprint of the running engine, GPU memory included.

    Not `ps` RSS: on Apple Silicon a model loaded through MLX lives in Metal
    buffers that RSS does not count. Measured on 2026-10-03 the same engine
    read 50 MB in `ps` and 3,781 MB in `footprint` — 3.5 GB of it GPU. The pid
    comes from runtime.json, and a pid from a file is not proof of identity,
    so the process's command line is checked first.
    """
    from server.runtime import read_runtime

    info = read_runtime() or {}
    pid = info.get('pid')
    if not pid:
        return None
    try:
        command = subprocess.run(['ps', '-o', 'command=', '-p', str(int(pid))],
                                 capture_output=True, text=True, timeout=5).stdout
        if 'SayEngine.app' not in command and '--no-menubar' not in command:
            return None
        out = subprocess.run(['footprint', str(int(pid))],
                             capture_output=True, text=True, timeout=30).stdout
        return parse_footprint(out)
    except Exception:
        return None


def collect(days: int) -> list[Category]:
    import metrics
    from server.store import get_store

    store = get_store()
    since = time.time() - days * 86400
    cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat(timespec='seconds')
    with store._lock:
        waits = [float(r['inference_seconds']) for r in store._conn.execute(
            'SELECT inference_seconds FROM transcripts '
            'WHERE success = 1 AND audio_seconds >= 20 AND ts_utc >= ?', (cutoff,))]
        hindi = int(store._conn.execute(
            "SELECT COUNT(*) FROM corrections c JOIN transcripts t ON t.id = c.transcript_id "
            "WHERE c.source = 'manual' AND t.language IN ('hi', 'ur')").fetchone()[0])
    return [
        reliability(press_outcomes(metrics.get_ledger()._path, since)),
        speed(waits),
        accuracy(store.review_summary()),
        convenience(),
        reach(hindi),
        footprint(engine_footprint_mb()),
    ]


def render(categories: list[Category], days: int) -> str:
    total = sum(c.points for c in categories)
    lines = [f'  Say scorecard, last {days} days', '']
    for c in categories:
        mark = '' if c.measured else '  (unmeasured)'
        lines.append(f'  {c.name:<20}{c.points:>5.1f} / {c.weight:<3}{mark}')
        lines.append(f'      {c.evidence}')
    lines += ['', f'  {"Total":<20}{total:>5.1f} / 100']
    unmeasured = [c.name for c in categories if not c.measured]
    if unmeasured:
        lines.append(f'  Not yet measured, so not credited: {", ".join(unmeasured)}.')
    return '\n'.join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--days', type=int, default=30)
    args = ap.parse_args()
    print()
    print(render(collect(args.days), args.days))
    print()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
