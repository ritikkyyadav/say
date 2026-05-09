# notifications.py — macOS Native Notifications
# ============================================================
# Say
# Shows macOS notifications when transcription completes.
# ============================================================

import subprocess

from config import SHOW_NOTIFICATIONS


def notify(title: str, text: str):
    """Display a macOS notification via osascript.

    Args:
        title: Notification title (e.g., 'Say')
        text:  Notification body text (truncated to 80 chars)
    """
    if not SHOW_NOTIFICATIONS:
        return

    # Truncate long text for notification display
    display_text = text[:80] + ('…' if len(text) > 80 else '')

    # Escape for AppleScript
    display_text = display_text.replace('\\', '\\\\').replace('"', '\\"')
    title = title.replace('\\', '\\\\').replace('"', '\\"')

    script = f'display notification "{display_text}" with title "{title}"'

    try:
        subprocess.run(
            ['osascript', '-e', script],
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
