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


def snapshot_pasteboard(pb) -> list[dict[str, object]] | None:
    """Every item on the pasteboard with every type it carries.

    The first version saved only the plain-text type, so a clipboard holding
    an image, a file or rich text was replaced by the transcript and never
    restored. Returns None when the pasteboard cannot be read, in which case
    the caller falls back to the string-only path.
    """
    try:
        items = pb.pasteboardItems() or []
        out: list[dict[str, object]] = []
        for item in items:
            entry: dict[str, object] = {}
            for kind in item.types() or []:
                data = item.dataForType_(kind)
                if data is not None:
                    entry[str(kind)] = data
            if entry:
                out.append(entry)
        return out
    except Exception:
        return None


def restore_pasteboard(pb, snapshot, AppKit) -> bool:
    """Put a `snapshot_pasteboard()` result back. Best-effort."""
    try:
        pb.clearContents()
        objects = []
        for entry in snapshot:
            item = AppKit.NSPasteboardItem.alloc().init()
            for kind, data in entry.items():
                item.setData_forType_(data, kind)
            objects.append(item)
        if not objects:
            return True
        return bool(pb.writeObjects_(objects))
    except Exception:
        return False


def inject_text_clipboard(text: str) -> InjectResult:
    """Paste `text` via NSPasteboard + Cmd+V, then restore previous content."""
    if not text:
        return InjectResult(False, 'no text')

    try:
        import AppKit
        import Quartz
        from AppKit import NSPasteboard, NSPasteboardTypeString
    except Exception as exc:
        return InjectResult(False, f'AppKit/Quartz unavailable: {exc}')

    pb = NSPasteboard.generalPasteboard()
    # Everything that is on the clipboard, not just its text: an image or a
    # file the user copied a moment ago must survive the paste.
    snapshot = snapshot_pasteboard(pb)
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

    restored = bool(snapshot) and restore_pasteboard(pb, snapshot, AppKit)
    if not restored and prev is not None:
        # The string-only path: what the snapshot could not do, or all there was.
        try:
            pb.clearContents()
            pb.setString_forType_(prev, NSPasteboardTypeString)
        except Exception:
            pass

    return InjectResult(True, None)
