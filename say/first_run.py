"""First-run permissions walkthrough — entry point.

This module is the *router*: it tries the branded onboarding window
in `wizard.py` first, and falls back to a chain of `alert_fn` modals
if the window can't be created (e.g. tests, headless environments,
PyObjC failures). The data describing each permission step lives here
so both paths stay in sync.

Detection of *whether* a permission was granted is intentionally not
attempted — macOS exposes no public API for that, and guessing leads
to false positives. We just deep-link the user to the right Settings
pane and let the next recording fail loudly if anything is missing.
"""

from __future__ import annotations

from collections.abc import Callable

from logging_setup import get_logger
from permissions import open_privacy_settings

_log = get_logger('first_run')


_STEPS = [
    (
        'Microphone',
        'microphone',
        'Say needs Microphone access to record your voice.\n\n'
        'Open Settings → check the box next to your Terminal or Say.app.',
    ),
    (
        'Accessibility',
        'accessibility',
        'Say uses Accessibility to inject transcribed text into the '
        'focused app and to watch the Fn key.\n\n'
        'Open Settings → enable Say (or Terminal/iTerm/Warp).',
    ),
    (
        'Input Monitoring',
        'input-monitoring',
        'Say needs Input Monitoring to detect the global Fn hotkey.\n\n'
        'Open Settings → enable Say (or Terminal/iTerm/Warp).',
    ),
]


def run_wizard(alert_fn: Callable, on_finish: Callable[[], None]) -> None:
    """Show the first-run wizard.

    Tries the branded NSWindow in `wizard.py`. If that import or window
    creation fails, falls back to the legacy alert-chain so onboarding
    still works in tests / headless environments.
    """
    _log.info('Running first-run wizard')

    try:
        from wizard import run_wizard as run_window_wizard
        if run_window_wizard(on_finish):
            return
        _log.warning('Window wizard returned False, falling back to alerts')
    except Exception:
        _log.exception('Window wizard import/launch failed, falling back to alerts')

    _run_alert_chain(alert_fn, on_finish)


# ── Legacy alert-chain fallback ──────────────────────────────────

def _run_alert_chain(alert_fn: Callable, on_finish: Callable[[], None]) -> None:
    """Original alert-driven wizard. Kept for headless and test paths.

    `alert_fn(title, message, ok=..., cancel=...)` returns:
      1 → primary action (Open Settings)
      0 → cancel/secondary
    """
    alert_fn(
        title='Welcome to Say',
        message=(
            'Say transcribes your speech locally — no cloud, no audio '
            'files saved.\n\n'
            'We need three macOS permissions before dictation works:\n'
            '  • Microphone\n  • Accessibility\n  • Input Monitoring\n\n'
            'On the next screens you can open the Settings pane for each.'
        ),
        ok='Continue',
    )

    for name, section, message in _STEPS:
        choice = alert_fn(
            title=f'Permission: {name}',
            message=message,
            ok='Open Settings',
            cancel='Skip',
        )
        if choice == 1:
            open_privacy_settings(section)

    alert_fn(
        title='Say is ready',
        message=(
            'Hold Fn to dictate. Press Fn+Space for hands-free mode.\n\n'
            'If a hotkey does not respond, run "Run Diagnostics" from the '
            'menu bar to see what is missing.'
        ),
        ok='Got it',
    )

    on_finish()
