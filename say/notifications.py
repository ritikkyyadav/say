# notifications.py — macOS Native Notifications
# ============================================================
# Say
# Shows macOS notifications when transcription completes.
# ============================================================

import subprocess

import config


def notify(title: str, text: str):
    """Display a macOS notification via osascript.

    Args:
        title: Notification title (e.g., 'Say')
        text:  Notification body text (truncated to 80 chars)
    """
    # Read at call time, not import time. `from config import
    # SHOW_NOTIFICATIONS` copied the value once, so switching notifications
    # off in Preferences did nothing until the engine was restarted — settings
    # mirror onto the `config` module, and nothing here re-read it.
    if not getattr(config, 'SHOW_NOTIFICATIONS', True):
        return

    # Collapse whitespace before truncating. An AppleScript string literal
    # cannot contain a raw newline, so the old string-building version turned
    # every multi-line transcript into a syntax error and silently showed no
    # notification at all — invisible, because osascript's failure is a
    # non-zero exit rather than an exception.
    display_text = ' '.join(text.split())
    if len(display_text) > 80:
        display_text = display_text[:80] + '…'

    # The text is passed as an *argument*, never interpolated into the script.
    # Hand-escaping quotes and backslashes was correct as written, but it is
    # one refactor away from not being — and this is a transcript, i.e. the
    # most attacker-influenced string in the whole app if anyone ever dictates
    # something they were sent. argv has no quoting to get wrong.
    try:
        subprocess.run(
            [
                'osascript',
                '-e', 'on run argv',
                '-e', 'display notification (item 1 of argv) with title (item 2 of argv)',
                '-e', 'end run',
                display_text,
                title,
            ],
            capture_output=True,
            timeout=5,
        )
    except Exception:
        pass  # Notifications are non-critical — fail silently


def notify_recording_start():
    """Notify that recording has started."""
    notify('Say', '🎙 Recording…')


def notify_transcription_complete(text: str):
    """Notify that transcription is complete with the result."""
    notify('Say', f'✓ {text}')


def notify_error(message: str):
    """Notify about an error."""
    notify('Say', f'⚠ {message}')
