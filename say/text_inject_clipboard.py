"""NSPasteboard-based clipboard fallback for text injection.

Copies the text to the system pasteboard, posts Cmd+V via CGEvent,
and best-effort restores the previous clipboard content.

Replaces the old pyperclip + osascript path: removes the racy fixed
sleeps where possible and avoids spawning an osascript subprocess.

Privacy note: clipboard managers (Raycast, Maccy, Paste, Alfred, etc.)
will capture the dictated text into their history regardless of the
restore — this is unavoidable when going through the pasteboard.
Prefer the CGEvent path whenever Accessibility is granted.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

# How long to wait between writing the pasteboard and posting Cmd+V.
# Short — just enough for the pasteboard to propagate to listeners.
_PRE_PASTE_SETTLE_S = 0.03

# How long to wait after Cmd+V before restoring the previous clipboard
# contents. The receiving app reads the pasteboard synchronously on most
# platforms, but some Electron-based apps queue paste events on their
# own loop. 200 ms is the empirical sweet spot.
_POST_PASTE_SETTLE_S = 0.20

# Virtual keycode for the V key on US-style layouts. The unicode payload
# is irrelevant here because we rely on the modifier flag (Cmd) to make
# the receiving app interpret this as a paste shortcut, not a literal V.
_V_KEYCODE = 9


@dataclass(frozen=True)
class InjectResult:
    success: bool
    error: str | None = None


def inject_text_clipboard(text: str) -> InjectResult:
    """Paste `text` via NSPasteboard + Cmd+V, then restore previous content."""
    if not text:
        return InjectResult(False, 'no text')

    try:
        import Quartz
        from AppKit import NSPasteboard, NSPasteboardTypeString
    except Exception as exc:
        return InjectResult(False, f'AppKit/Quartz unavailable: {exc}')

    pb = NSPasteboard.generalPasteboard()
    prev = pb.stringForType_(NSPasteboardTypeString)

    pb.clearContents()
    if not pb.setString_forType_(text, NSPasteboardTypeString):
        return InjectResult(False, 'pasteboard write failed')

    time.sleep(_PRE_PASTE_SETTLE_S)

    try:
        flags = Quartz.kCGEventFlagMaskCommand
        evt_down = Quartz.CGEventCreateKeyboardEvent(None, _V_KEYCODE, True)
        if evt_down is None:
            return InjectResult(False, 'CGEventCreateKeyboardEvent returned None')
        Quartz.CGEventSetFlags(evt_down, flags)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, evt_down)

        evt_up = Quartz.CGEventCreateKeyboardEvent(None, _V_KEYCODE, False)
        if evt_up is None:
            return InjectResult(False, 'CGEventCreateKeyboardEvent returned None')
        Quartz.CGEventSetFlags(evt_up, flags)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, evt_up)
    except Exception as exc:
        return InjectResult(False, f'cmd+v post failed: {exc}')

    time.sleep(_POST_PASTE_SETTLE_S)

    if prev is not None:
        try:
            pb.clearContents()
            pb.setString_forType_(prev, NSPasteboardTypeString)
        except Exception:
            pass

    return InjectResult(True, None)
