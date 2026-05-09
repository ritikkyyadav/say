"""Text injection for the currently focused macOS application.

Two underlying methods, both unicode-correct:
  - 'accessibility': CGEvent direct injection — fast, lossless, no
    clipboard pollution. Requires Accessibility permission.
  - 'clipboard':     NSPasteboard write + Cmd+V — works in apps that
    block synthetic CGEvents, but clipboard managers will capture the
    dictated text into their history.

The 'auto' method tries accessibility first, then clipboard.

Phase 2 replaced the previous AppleScript-keystroke path entirely.
"""

from __future__ import annotations

from dataclasses import dataclass

import config
from text_inject_cgevent import inject_text_cgevent
from text_inject_clipboard import inject_text_clipboard


@dataclass(frozen=True)
class InjectionResult:
    success: bool
    method: str
    error: str | None = None


def escape_applescript_text(text: str) -> str:
    """Escape `text` for an AppleScript double-quoted string.

    The runtime injector no longer uses AppleScript, but other tooling
    (notifications.py) does, and tests still depend on this helper.
    """
    return text.replace('\\', '\\\\').replace('"', '\\"')


def inject(text: str) -> InjectionResult:
    """Inject transcribed text into the currently focused application."""
    if not text:
        return InjectionResult(False, 'none', 'No text to inject.')

    method = config.INJECT_METHOD

    if method == 'accessibility':
        result = inject_text_cgevent(text)
        return InjectionResult(result.success, 'accessibility', result.error)

    if method == 'clipboard':
        result = inject_text_clipboard(text)
        return InjectionResult(result.success, 'clipboard', result.error)

    cge = inject_text_cgevent(text)
    if cge.success:
        return InjectionResult(True, 'accessibility', None)

    cb = inject_text_clipboard(text)
    if cb.success:
        return InjectionResult(True, 'clipboard', None)

    return InjectionResult(
        False,
        'auto',
        f'CGEvent failed: {cge.error}; clipboard failed: {cb.error}',
    )


def inject_text(text: str) -> bool:
    """Backward-compatible wrapper for older entrypoints."""
    return inject(text).success
