"""Getting the last dictation back, and taking it back out.

Two promises, and the rule that keeps the second one safe.

**Re-insert** (Fn+V) types the most recent transcript again. It is the answer
to every way a dictation can go missing after Say has it: injection failed and
only the clipboard has it, focus moved and it was held, the field was cleared,
the app crashed. The transcript is already stored before anything is typed, so
nothing new is needed to recover it — only a key that does.

**Undo** (Fn+Z) deletes what Say just typed by sending one Backspace per
character. That is only safe if the text is still exactly where Say left it,
with the cursor right after it. Say cannot see the cursor in most apps (the
read-back proves that daily), so the guard is built from what it *can* know:

  * the same app is still frontmost,
  * the user has not pressed a single key since — the hotkey tap counts key
    presses, never which keys, and skips Say's own injected events,
  * it is within `UNDO_WINDOW_S` of the insertion,
  * every character in the text is one Backspace wide.

Any doubt refuses, with the reason. Deleting the wrong text is worse than not
deleting at all, because the user may not notice until later.

Pure: no AppKit, no store. The controller supplies the facts.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass

# Long enough to notice a wrong dictation and reach for the key; short enough
# that the user has not moved on to something the Backspaces would eat.
UNDO_WINDOW_S = 30.0


@dataclass(frozen=True)
class Delivery:
    """What Say last typed, and where, kept in memory only."""

    transcript_id: int | None
    text: str
    target_app: str | None
    at: float  # time.monotonic() when the text finished landing
    keystrokes: int | None  # the tap's key-press count at that moment; None if unknown


def backspaces_for(text: str) -> int | None:
    """How many Backspaces remove `text`, or None when that is not knowable.

    One per character holds for precomposed Latin, Greek, Cyrillic and most
    punctuation. It does not hold for combining marks (Hindi vowel signs,
    decomposed accents), emoji built from several code points, or variation
    selectors: apps delete those as one cluster or several, and they disagree.
    Refusing is the safe answer for those, not a guess.
    """
    if not text:
        return None
    for ch in text:
        cp = ord(ch)
        if cp > 0xFFFF:  # emoji and other astral characters
            return None
        if unicodedata.combining(ch) or unicodedata.category(ch) in ('Mn', 'Mc', 'Me'):
            return None
        if ch == '‍' or 0xFE00 <= cp <= 0xFE0F:  # ZWJ, variation selectors
            return None
    return len(text)


def undo_refusal(
    last: Delivery | None,
    *,
    now: float,
    frontmost: str | None,
    keystrokes: int | None,
) -> str | None:
    """None when undoing `last` is safe; otherwise the sentence saying why not."""
    if last is None:
        return 'Nothing to undo: Say has not typed anything since it started.'
    if now - last.at > UNDO_WINDOW_S:
        return f'Too late to undo: that was more than {int(UNDO_WINDOW_S)} seconds ago.'
    if last.keystrokes is None or keystrokes is None:
        return "Can't undo here: Say can't tell whether you've typed since."
    if keystrokes != last.keystrokes:
        return "Not undone: you've typed since, so the text may not be where Say left it."
    if last.target_app and frontmost and frontmost != last.target_app:
        return 'Not undone: you switched apps since Say typed it.'
    if backspaces_for(last.text) is None:
        return "Can't undo this one safely: it contains characters apps delete differently."
    return None
