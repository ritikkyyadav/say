"""Native CGEvent-based Unicode text injector.

Posts synthetic keyboard events with the unicode string attached, which
the focused application receives like real key presses. Replaces the
old AppleScript `keystroke` path which was slow, ASCII-only, fragile on
long input, and routed every character through System Events.

Requires Accessibility permission for the running app.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

# Empirically-safe chunk size for CGEventKeyboardSetUnicodeString, in UTF-16
# code units — the unit the API counts in. Posting more than ~20 in a single
# event is unreliable across apps (some clip the tail). Chunking is essentially
# free, so we stay conservative.
#
# Units, not characters. Python indexes strings by code point, and a code
# point outside the Basic Multilingual Plane (every emoji, some CJK) is TWO
# UTF-16 units. The first version sliced `text[i:i+20]` and passed `len(chunk)`
# as the length, so a chunk holding N such characters was 20+N units long and
# the API was told 20: its last N units were dropped, and a pair could be torn
# in half. Verified by round trip — "hello world 😀 test!" came back without
# its "!", and five emoji in a row produced an undecodable half-surrogate.
_CHUNK_LEN = 20

# Inter-chunk delay so receiving apps can process events. Browsers and
# Electron apps need this — without it they drop characters under load.
_CHUNK_DELAY_S = 0.002

# Stamped into `kCGEventSourceUserData` on every event we post, so the Fn
# CGEventTap in `hotkey_fn` can recognise our own synthetic keystrokes and
# ignore them. Without it the tap sees each injected chunk as the user typing:
# a keystroke arriving while the trigger key is held is treated as an ordinary
# Fn shortcut and abandons the gesture, so starting a new dictation while the
# previous one is still being typed out would silently cancel it.
INJECTED_EVENT_MARKER = 0x5A5900


@dataclass(frozen=True)
class InjectResult:
    success: bool
    error: str | None = None


def _utf16_units(ch: str) -> int:
    return 2 if ord(ch) >= 0x10000 else 1


def utf16_len(text: str) -> int:
    """Length of `text` in UTF-16 code units — what Quartz counts."""
    return sum(_utf16_units(ch) for ch in text)


def chunks(text: str, limit: int = _CHUNK_LEN) -> list[str]:
    """Split `text` into pieces of at most `limit` UTF-16 units.

    A character is never split: a surrogate pair either fits in the current
    chunk or starts the next one, so every chunk is a valid string on its own
    and the length handed to Quartz is exactly the chunk's unit count.
    """
    out: list[str] = []
    current: list[str] = []
    units = 0
    for ch in text:
        n = _utf16_units(ch)
        if current and units + n > limit:
            out.append(''.join(current))
            current, units = [], 0
        current.append(ch)
        units += n
    if current:
        out.append(''.join(current))
    return out


def _mark(Quartz, event) -> None:
    """Tag an event as ours. Best-effort: injection matters more than the tag."""
    try:
        Quartz.CGEventSetIntegerValueField(
            event, Quartz.kCGEventSourceUserData, INJECTED_EVENT_MARKER
        )
    except Exception:
        pass


def is_injected_event(Quartz, event) -> bool:
    """True if `event` is one this module posted."""
    try:
        return (
            Quartz.CGEventGetIntegerValueField(
                event, Quartz.kCGEventSourceUserData
            )
            == INJECTED_EVENT_MARKER
        )
    except Exception:
        return False


def inject_text_cgevent(text: str) -> InjectResult:
    """Inject `text` as synthesized keyboard events with a Unicode payload."""
    if not text:
        return InjectResult(False, 'no text')

    try:
        import Quartz
    except Exception as exc:
        return InjectResult(False, f'Quartz unavailable: {exc}')

    try:
        for chunk in chunks(text):
            units = utf16_len(chunk)
            evt_down = Quartz.CGEventCreateKeyboardEvent(None, 0, True)
            if evt_down is None:
                return InjectResult(False, 'CGEventCreateKeyboardEvent returned None')
            Quartz.CGEventKeyboardSetUnicodeString(evt_down, units, chunk)
            _mark(Quartz, evt_down)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, evt_down)

            evt_up = Quartz.CGEventCreateKeyboardEvent(None, 0, False)
            if evt_up is None:
                return InjectResult(False, 'CGEventCreateKeyboardEvent returned None')
            Quartz.CGEventKeyboardSetUnicodeString(evt_up, units, chunk)
            _mark(Quartz, evt_up)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, evt_up)

            time.sleep(_CHUNK_DELAY_S)
    except Exception as exc:
        return InjectResult(False, f'cgevent post failed: {exc}')

    return InjectResult(True, None)


_KEYCODE_DELETE = 51  # kVK_Delete, the Backspace key


def delete_backward(count: int) -> InjectResult:
    """Press Backspace `count` times. Used only by undo (see recall.py).

    The modifier flags are cleared on every event: an event created with no
    source inherits whatever is held, and Fn + Delete is *forward* delete,
    which would eat the text after the cursor instead of Say's.
    """
    if count <= 0:
        return InjectResult(False, 'nothing to delete')
    try:
        import Quartz
    except Exception as exc:
        return InjectResult(False, f'Quartz unavailable: {exc}')
    try:
        for _ in range(count):
            for down in (True, False):
                evt = Quartz.CGEventCreateKeyboardEvent(None, _KEYCODE_DELETE, down)
                if evt is None:
                    return InjectResult(False, 'CGEventCreateKeyboardEvent returned None')
                Quartz.CGEventSetFlags(evt, 0)
                _mark(Quartz, evt)
                Quartz.CGEventPost(Quartz.kCGHIDEventTap, evt)
            time.sleep(_CHUNK_DELAY_S)
    except Exception as exc:
        return InjectResult(False, f'cgevent post failed: {exc}')
    return InjectResult(True, None)
