#!/usr/bin/env python3
"""Is the engine getting more reliable, week by week?

Why this exists
---------------
Every reliability failure this engine has had left the same kind of evidence:
a warning in `say.log` that nobody was counting. The wedged microphone, the
refused key presses, the event tap that went quiet — each was found by reading
the log after someone complained. This counts those lines by week so a
regression shows up as a number going up, and a fix as one going to zero.

It reads the log and nothing else. It never writes.

Usage
-----
    python say/tools/health_report.py              # every week in the log
    python say/tools/health_report.py --weeks 6    # the last six
"""
from __future__ import annotations

import argparse
import glob
import os
import re
import sys
from collections import Counter
from collections.abc import Iterable
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# label -> a substring that identifies the log line. Order is display order.
SIGNALS: dict[str, str] = {
    'mic open retried': 'InputStream open failed',
    'mic wedged': 'did not open within',
    'mic would not arm': 'Could not arm the microphone',
    'hotkey tap refused': 'Failed to create CGEventTap',
    'hotkey tap went quiet': 'Event tap was disabled',
    'key press refused': 'start_recording rejected',
    'no live audio': 'Pre-roll-only clip',
    'streaming re-decoded': 'Streaming result unusable',
    'decode slower than speech': 'fell below real time',
    'decoder loop caught': 'mostly a repetition loop',
    'silence not decoded': 'below the speech floor',
    'overlay clock stalled': 'clock stalled',
}

_DATE = re.compile(r'^(\d{4})-(\d\d)-(\d\d)')


def week_of(day: date) -> date:
    """The Monday of `day`'s week."""
    return day - timedelta(days=day.weekday())


def tally(lines: Iterable[str]) -> dict[str, Counter[date]]:
    """Count each signal per week. Pure: lines in, counts out."""
    counts: dict[str, Counter[date]] = {label: Counter() for label in SIGNALS}
    for line in lines:
        m = _DATE.match(line)
        if m is None:
            continue
        try:
            week = week_of(date(int(m[1]), int(m[2]), int(m[3])))
        except ValueError:
            continue
        for label, needle in SIGNALS.items():
            if needle in line:
                counts[label][week] += 1
    return counts


def render(counts: dict[str, Counter[date]], weeks: int | None = None) -> str:
    seen = sorted({w for c in counts.values() for w in c})
    if weeks:
        seen = seen[-weeks:]
    if not seen:
        return '  No dated lines found in the log.'
    out = ['  ' + ' ' * 27 + ' '.join(f'{w:%m/%d}' for w in seen)]
    for label, by_week in counts.items():
        cells = ' '.join(f'{by_week.get(w) or "·":>5}' for w in seen)
        out.append(f'  {label:<27}{cells}')
    return '\n'.join(out)


def _log_lines(log_dir: str) -> Iterable[str]:
    # Oldest rotation first so the table reads left to right in time.
    paths = sorted(glob.glob(os.path.join(log_dir, 'say.log.*')), reverse=True)
    paths.append(os.path.join(log_dir, 'say.log'))
    for path in paths:
        try:
            with open(path, encoding='utf-8', errors='replace') as f:
                yield from f
        except OSError:
            continue


def main() -> int:
    from logging_setup import LOG_DIR

    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--weeks', type=int, default=0, help='only the most recent N weeks')
    args = ap.parse_args()

    print()
    print('  Engine warnings per week (week starting)')
    print(render(tally(_log_lines(LOG_DIR)), args.weeks or None))
    print()
    print('  A row that reaches zero and stays there is a fix that held.')
    print()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
