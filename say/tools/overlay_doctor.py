#!/usr/bin/env python3
"""Ask the window server why the dictation glyph is not on screen.

Why this exists
---------------
The overlay fails silently on purpose — feedback must never be able to stop a
recording — so when it goes missing there is nothing to read. Worse, the code
can be entirely correct and the glyph still invisible, because what matters is
what the *window server* did with the panel, not what the drawing code asked
for. That is not hypothetical: the glyph once asked for `NSStatusWindowLevel`
(25) and spent its whole life at `NSFloatingWindowLevel` (3), because
`setFloatingPanel_` assigns the level as a side effect. It looked fine over a
single window and vanished as soon as another app's floating panel outranked
it — which is why "it works in full screen but not with several windows open"
was the symptom that finally identified it.

So this asks the window server directly: where is the panel, what level did it
actually get, is it on screen, and what is sitting on top of it.

It is a developer tool. It only reads — it never touches the engine.

Usage
-----
    python say/tools/overlay_doctor.py            # one snapshot
    python say/tools/overlay_doctor.py --watch    # live; dictate and watch
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import Quartz  # noqa: E402

RUNTIME = os.path.expanduser('~/Library/Application Support/Say/runtime.json')

# What the glyph should get. Anything lower and ordinary floating panels tie
# with it, and ties are broken by ordering — so it loses at random.
EXPECTED_LEVEL = 25  # NSStatusWindowLevel


def engine_pid() -> int | None:
    try:
        with open(RUNTIME) as fh:
            return int(json.load(fh).get('pid') or 0) or None
    except Exception:
        return None


def windows() -> list[dict]:
    return list(
        Quartz.CGWindowListCopyWindowInfo(
            Quartz.kCGWindowListOptionAll, Quartz.kCGNullWindowID
        )
        or []
    )


def rect(w: dict) -> tuple[float, float, float, float]:
    b = w.get('kCGWindowBounds') or {}
    return (
        float(b.get('X', 0)), float(b.get('Y', 0)),
        float(b.get('Width', 0)), float(b.get('Height', 0)),
    )


def overlaps(a, b) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return not (ax + aw <= bx or bx + bw <= ax or ay + ah <= by or by + bh <= ay)


def find_glyph(wins: list[dict], pid: int | None) -> dict | None:
    """The engine's smallest window — the glyph is 90x19pt plus its margin."""
    owned = [w for w in wins if pid and w.get('kCGWindowOwnerPID') == pid]
    if not owned:
        owned = [w for w in wins if str(w.get('kCGWindowOwnerName', '')) == 'Say']
    if not owned:
        return None
    return min(owned, key=lambda w: rect(w)[2] * rect(w)[3])


def report() -> int:
    pid = engine_pid()
    wins = windows()
    glyph = find_glyph(wins, pid)
    if glyph is None:
        print('No Say window found — is the engine running?')
        return 1

    gl = int(glyph.get('kCGWindowLayer', 0))
    gr = rect(glyph)
    onscreen = bool(glyph.get('kCGWindowIsOnscreen'))

    print(f'  glyph      level {gl}  bounds {gr}  alpha {glyph.get("kCGWindowAlpha")}')
    print(f'  on screen  {onscreen}   (False is correct while idle — it only shows during a gesture)')
    if gl != EXPECTED_LEVEL:
        print(f'  ⚠ LEVEL IS {gl}, EXPECTED {EXPECTED_LEVEL}.')
        print('    At a lower level the glyph ties with other apps\' floating panels and')
        print('    loses at random — the classic "vanishes when several windows are open".')
        print('    Something is assigning the level after setLevel_ (setFloatingPanel_ does).')
    else:
        print(f'  ✓ level is {EXPECTED_LEVEL} (NSStatusWindowLevel)')

    above = [
        w for w in wins
        if w is not glyph
        and int(w.get('kCGWindowLayer', 0)) >= gl
        and w.get('kCGWindowIsOnscreen')
        and overlaps(gr, rect(w))
        and (rect(w)[2] * rect(w)[3]) > 0
    ]
    print()
    if not above:
        print('  Nothing is covering it.')
    else:
        print(f'  {len(above)} window(s) at or above its level overlapping it:')
        for w in sorted(above, key=lambda w: -int(w.get('kCGWindowLayer', 0))):
            print(f'    level {int(w.get("kCGWindowLayer", 0)):>4}  '
                  f'{str(w.get("kCGWindowOwnerName", "?")):<24} {rect(w)}')
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--watch', action='store_true', help='sample continuously')
    ap.add_argument('--interval', type=float, default=0.5)
    args = ap.parse_args()
    if not args.watch:
        print()
        return report()
    try:
        while True:
            print('\033[2J\033[H', end='')
            print(f'  overlay doctor — {time.strftime("%H:%M:%S")}  (Ctrl-C to stop)\n')
            report()
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0


if __name__ == '__main__':
    raise SystemExit(main())
