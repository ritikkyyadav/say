#!/usr/bin/env python3
"""Find out why the correction read-back misses an app, by measuring it.

Why this exists
---------------
The read-back compared 9 of 484 real insertions, and the misses were not
random: 7 of 93 insertions under five words were measured, 0 of 307 at twenty
words or more, and nothing at all in Claude Desktop or Codex. Two different
causes produce that picture and they need different fixes:

  * **Timing.** `text_inject_cgevent` returns once the key events are *posted*.
    The app consumes them afterwards, and `CorrectionWatcher._observe` reads
    the field immediately and gives up if our text is not there yet. Long text
    takes longer to land. The fix is to look again a little later.
  * **An unreadable field.** Some apps expose no text to the Accessibility API
    at all, or return something that is not what was typed. No amount of
    waiting helps, and the honest outcome is `unreadable`.

Reading the code cannot tell these apart for a given app. This types a known
string into whatever field is focused and watches when, and whether, it becomes
readable.

It types into your focused field. Run it, switch to the app you want to test,
click into a text field before the countdown ends, and delete the probe text
afterwards. It reads only the field it just typed into, looks only for its own
string, and prints lengths and timings, never the field's contents.

The terminal you run it from needs Accessibility permission, the same grant
Say itself uses.

Usage
-----
    python say/tools/readback_probe.py                  # 5, 40 and 150 words
    python say/tools/readback_probe.py --words 40       # one size
    python say/tools/readback_probe.py --countdown 8    # longer to switch apps
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Seconds after injection returns at which the field is read.
POLL_AT = (0.0, 0.05, 0.15, 0.4, 1.0, 2.0, 4.0)
DEFAULT_SIZES = (5, 40, 150)

_WORDS = (
    'river', 'window', 'paper', 'orange', 'seven', 'garden', 'copper', 'signal',
    'harbour', 'pencil', 'yellow', 'market', 'thunder', 'velvet', 'compass', 'lantern',
)


def probe_text(words: int, tag: int) -> str:
    """A recognisable sentence of exactly `words` words that no document contains."""
    body = [f'sayprobe{tag}'] + [_WORDS[i % len(_WORDS)] for i in range(max(0, words - 1))]
    return ' '.join(body) + '.'


@dataclass(frozen=True)
class Verdict:
    kind: str  # 'immediate' | 'late' | 'reformatted' | 'absent' | 'unreadable'
    seen_at: float | None  # seconds after injection when our text first appeared
    detail: str


def classify(samples: list[tuple[float, str | None]], text: str) -> Verdict:
    """Turn timed reads of a field into one cause. Pure.

    `samples` is `(seconds_after_injection, field_value_or_None)` in order.
    """
    if not samples or all(value is None for _, value in samples):
        return Verdict('unreadable', None, 'the field never exposed any text')

    for at, value in samples:
        if value is not None and text in value:
            if at == samples[0][0]:
                return Verdict('immediate', at, 'there on the first read')
            return Verdict('late', at, f'not there at first; appeared by {at:.2f}s')

    squashed = ' '.join(text.split())
    for at, value in samples:
        if value is not None and squashed in ' '.join(value.split()):
            return Verdict(
                'reformatted', at,
                'present only after collapsing whitespace: the app re-wraps what is typed',
            )
    return Verdict('absent', None, 'readable, but our text never appeared in it')


ADVICE = {
    'immediate': 'The read-back can arm as it does today.',
    'late': 'Timing. Re-arming a little later would measure this app.',
    'reformatted': 'A whitespace-insensitive match would measure this app.',
    'absent': 'The focused element is not where the text goes. Not measurable as built.',
    'unreadable': 'No text is exposed to Accessibility. Record `unreadable`; do not retry.',
}


def _role(element) -> str:
    try:
        import ApplicationServices as ax

        err, role = ax.AXUIElementCopyAttributeValue(element, ax.kAXRoleAttribute, None)
        return str(role) if err == 0 and role else '?'
    except Exception:
        return '?'


def run_once(words: int, tag: int) -> dict:
    import injector
    import readback
    from server.store import get_frontmost_app

    text = probe_text(words, tag)
    app = get_frontmost_app() or '?'
    element = readback._focused_element()
    role = _role(element) if element is not None else 'none'

    result = injector.inject(text)
    t0 = time.monotonic()
    samples: list[tuple[float, str | None]] = []
    lengths: list[int | None] = []
    for at in POLL_AT:
        wait = at - (time.monotonic() - t0)
        if wait > 0:
            time.sleep(wait)
        el = element if element is not None else readback._focused_element()
        value = readback._read_value(el) if el is not None else None
        samples.append((at, value))
        lengths.append(None if value is None else len(value))

    verdict = classify(samples, text)
    return {
        'app': app, 'role': role, 'words': words, 'chars': len(text),
        'injected': bool(result.success), 'method': result.method,
        'field_chars': lengths, 'verdict': verdict,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--words', default=','.join(str(n) for n in DEFAULT_SIZES),
                    help='comma-separated probe sizes in words')
    ap.add_argument('--countdown', type=float, default=5.0,
                    help='seconds to switch to the app and click a text field')
    args = ap.parse_args()
    sizes = [int(s) for s in args.words.split(',') if s.strip()]

    import readback

    ax = readback._ax()
    if ax is None or not ax.AXIsProcessTrusted():
        print('This terminal does not have Accessibility permission, so nothing can be')
        print('typed or read. Grant it in System Settings > Privacy & Security >')
        print('Accessibility, then run this again.')
        return 2

    print(f'Typing {len(sizes)} probe(s) into the focused field in {args.countdown:.0f}s.')
    print('Switch to the app and click into a text field now.')
    time.sleep(max(0.0, args.countdown))

    rows = []
    for i, words in enumerate(sizes):
        rows.append(run_once(words, tag=int(time.time()) % 100000 + i))
        time.sleep(1.5)

    print()
    print(f'  {"app":<34}{"role":<16}{"words":>6}{"typed":>7}  {"first seen":<11} cause')
    print('  ' + '-' * 92)
    for r in rows:
        v: Verdict = r['verdict']
        seen = '-' if v.seen_at is None else f'{v.seen_at:.2f}s'
        typed = 'yes' if r['injected'] else 'NO'
        print(f'  {r["app"][:33]:<34}{r["role"][:15]:<16}{r["words"]:>6}{typed:>7}  {seen:<11} {v.kind}')
    print()
    for kind in dict.fromkeys(r['verdict'].kind for r in rows):
        print(f'  {kind}: {ADVICE[kind]}')
    if any(not r['injected'] for r in rows):
        print('  A probe that was not typed says nothing about the read-back; check the field.')
    print()
    print('  Delete the probe text from the field. Nothing was stored.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
