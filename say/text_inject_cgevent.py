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

# Empirically-safe chunk size for CGEventKeyboardSetUnicodeString.
# Posting more than ~20 UTF-16 code units in a single event is
# unreliable across apps (some clip the tail). Chunking is essentially
# free, so we stay conservative.
_CHUNK_LEN = 20

# Inter-chunk delay so receiving apps can process events. Browsers and
# Electron apps need this — without it they drop characters under load.
_CHUNK_DELAY_S = 0.002


@dataclass(frozen=True)
class InjectResult:
    success: bool
    error: str | None = None


def inject_text_cgevent(text: str) -> InjectResult:
    """Inject `text` as synthesized keyboard events with a Unicode payload."""
    if not text:
        return InjectResult(False, 'no text')

    try:
        import Quartz
    except Exception as exc:
        return InjectResult(False, f'Quartz unavailable: {exc}')

    chunks = [text[i:i + _CHUNK_LEN] for i in range(0, len(text), _CHUNK_LEN)]

    try:
        for chunk in chunks:
            evt_down = Quartz.CGEventCreateKeyboardEvent(None, 0, True)
            if evt_down is None:
                return InjectResult(False, 'CGEventCreateKeyboardEvent returned None')
            Quartz.CGEventKeyboardSetUnicodeString(evt_down, len(chunk), chunk)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, evt_down)

            evt_up = Quartz.CGEventCreateKeyboardEvent(None, 0, False)
            if evt_up is None:
                return InjectResult(False, 'CGEventCreateKeyboardEvent returned None')
            Quartz.CGEventKeyboardSetUnicodeString(evt_up, len(chunk), chunk)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, evt_up)

            time.sleep(_CHUNK_DELAY_S)
    except Exception as exc:
        return InjectResult(False, f'cgevent post failed: {exc}')

    return InjectResult(True, None)
