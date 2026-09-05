#!/usr/bin/env python3
"""Show what the user changed after Say typed, and the rate it implies.

Why this exists
---------------
Every layer between the microphone and the document — vocabulary repair,
formatting, per-app profiles, any future semantic tier — is a bet about what the
user meant. `correction rate after insertion` is the only honest score for those
bets, and this is the fastest way to read it.

It is also the diagnostic for the capture loop itself. A correction that was
never observed and one that was observed and found clean look identical in the
summary, so this prints the ledger, not just the number: if you fixed a word and
nothing appears here, the read-back did not fire, and `--why` explains the
handful of reasons that can happen.

It is a developer tool. It only ever reads — never mutates the store, the
recordings, or settings.

Usage
-----
    python say/tools/corrections_report.py            # rate + recent corrections
    python say/tools/corrections_report.py --terms    # what the vocabulary could learn
    python say/tools/corrections_report.py --days 7
    python say/tools/corrections_report.py --why      # why nothing was captured
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import corrections  # noqa: E402
from server.store import TranscriptStore, default_db_path  # noqa: E402


def _bar(frac: float, width: int = 28) -> str:
    filled = int(round(max(0.0, min(1.0, frac)) * width))
    return '█' * filled + '·' * (width - filled)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--days', type=int, default=30, help='window for the rate (default 30)')
    ap.add_argument('--limit', type=int, default=25, help='corrections to list (default 25)')
    ap.add_argument('--terms', action='store_true', help='aggregate learnable term pairs')
    ap.add_argument('--why', action='store_true', help='explain an empty ledger')
    args = ap.parse_args()

    db = default_db_path()
    if not db.exists():
        print(f'No database at {db} — dictate at least once first.')
        return 1
    store = TranscriptStore(db)

    s = store.correction_summary(days=args.days)
    clean = 1.0 - s['touch_rate'] if s['insertions'] else 0.0

    print()
    print(f'  Last {args.days} days · {db}')
    print()
    if not s['insertions']:
        if s['inserted_total']:
            print(f'  {s["inserted_total"]} insertions in this window, none measured yet.')
            print('  Nothing has been scored: the read-back either has not run for these')
            print('  or could not read the field. Dictate again and edit the result.')
        else:
            print('  No insertions in this window yet.')
    else:
        print(f'  Left untouched   {_bar(clean)}  {clean * 100:5.1f}%')
        print(f'  {s["corrected"]} of {s["insertions"]} measured insertions were edited '
              f'afterwards  (measured {s["insertions"]}/{s["inserted_total"]} inserted)')
        print(f'  {s["edited_words"]} of {s["words"]} words in those edits changed '
              f'({s["word_rate"] * 100:.1f}%)')
    print()

    rows = store.corrections(limit=args.limit)
    if rows:
        print(f'  Recent corrections ({len(rows)})')
        print()
        for r in rows:
            app = (r['target_app'] or '—').split('.')[-1]
            print(f'    [{r["ts_utc"][:16]}] {app} · {r["source"]} · '
                  f'{r["edit_distance"]}/{r["words"]} words')
            d = corrections.diff(r['inserted'], r['corrected'])
            for e in d.edits:
                if e.kind == 'sub':
                    print(f'        {e.before!r}  →  {e.after!r}')
                elif e.kind == 'del':
                    print(f'        {e.before!r}  →  (deleted)')
                else:
                    print(f'        (inserted)  →  {e.after!r}')
        print()

    if args.terms and rows:
        tally: dict[tuple[str, str], int] = {}
        for r in rows:
            for pair in corrections.term_pairs(corrections.diff(r['inserted'], r['corrected'])):
                tally[pair] = tally.get(pair, 0) + 1
        if tally:
            print('  Learnable term pairs (candidates for DICTIONARY_REPLACEMENTS)')
            print()
            for (before, after), n in sorted(tally.items(), key=lambda kv: -kv[1]):
                print(f'    {n:3}x  {before!r}  →  {after!r}')
            print()

    if args.why and not rows:
        import config

        print('  Nothing captured. In order of likelihood:')
        print()
        print(f'    1. LEARN_FROM_CORRECTIONS is '
              f'{getattr(config, "LEARN_FROM_CORRECTIONS", True)}')
        print(f'    2. The read-back waits {getattr(config, "CORRECTION_READBACK_DELAY_S", 8.0)}s '
              'after insertion — fix the text and leave the field open that long.')
        print('    3. Accessibility must be granted to the *engine* (Say Engine), not just')
        print('       the window. Check /listener → accessibility_trusted.')
        print('    4. The app must expose its text via AX. Native fields do; some')
        print('       Electron/canvas editors report nothing, and are skipped by design.')
        print('    5. Only edits *inside* the inserted span count. Typing on afterwards is')
        print('       continuation, not correction, and is deliberately not recorded.')
        print('    6. Changing more than half the span reads as a rewrite, not a fix.')
        print()

    store.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
