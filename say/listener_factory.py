# listener_factory.py — Pick the right hotkey listener, falling back when
# the preferred one cannot start.
#
# Both FnHotkeyListener (Quartz CGEventTap, Fn-aware) and HotkeyListener
# (pynput, ctrl+space) share the same callback shape. This module hides
# the choice from callers and notifies on fallback.

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from logging_setup import get_logger

_log = get_logger('listener_factory')


Callback = Callable[[], None]


@dataclass(frozen=True)
class ListenerInfo:
    listener: Any
    kind: str        # 'fn' or 'legacy'
    label: str       # user-facing description of the active hotkey
    fell_back: bool  # True if the preferred listener was not used


def make_listener(
    on_start: Callback,
    on_stop: Callback,
    on_cancel: Callback | None = None,
    prefer_legacy: bool = False,
    start_timeout: float = 2.0,
) -> ListenerInfo:
    """Create and start a hotkey listener, with automatic fallback.

    Tries `FnHotkeyListener` (Quartz CGEventTap) first. If that fails to
    start (e.g. CGEventTap creation refused because Accessibility was
    not granted, or `Quartz` is unavailable), falls back to the legacy
    `HotkeyListener` (pynput, ctrl+space).

    Raises RuntimeError if neither listener can be started.
    """
    fn_error: str | None = None

    if not prefer_legacy:
        try:
            from hotkey_fn import FnHotkeyListener
        except Exception as exc:
            fn_error = f'Fn listener import failed: {exc}'
        else:
            fn = FnHotkeyListener(
                on_start_cb=on_start,
                on_stop_cb=on_stop,
                on_cancel_cb=on_cancel,
            )
            try:
                started = fn.start(timeout=start_timeout)
            except Exception as exc:
                fn_error = f'Fn listener exception: {exc}'
                try:
                    fn.stop()
                except Exception:
                    pass
            else:
                if started:
                    return ListenerInfo(
                        listener=fn,
                        kind='fn',
                        label=fn.hotkey_label,
                        fell_back=False,
                    )
                fn_error = (
                    'Fn listener could not create CGEventTap '
                    '(grant Accessibility + Input Monitoring permissions to enable Fn).'
                )
                try:
                    fn.stop()
                except Exception:
                    pass

    if fn_error is not None:
        _log.warning('%s Falling back to pynput hotkey.', fn_error)

    try:
        import config as _config
        from hotkey import HotkeyListener
    except Exception as exc:
        raise RuntimeError(
            f'Both Fn and pynput listeners are unavailable. '
            f'Fn error: {fn_error or "n/a"}. pynput import: {exc}'
        ) from exc

    # The legacy listener parses a token-based hotkey ('ctrl+space'
    # etc.); HOTKEY itself is 'fn', which only the Fn-listener
    # understands. Use HOTKEY_LEGACY explicitly so we don't pick up
    # the wrong default.
    legacy = HotkeyListener(
        on_press_cb=on_start,
        on_release_cb=on_stop,
        on_cancel_cb=on_cancel,
        hotkey=getattr(_config, 'HOTKEY_LEGACY', 'ctrl+space'),
    )
    if not legacy.start():
        raise RuntimeError(
            f'Both Fn and pynput listeners failed to start. '
            f'Fn error: {fn_error or "n/a"}.'
        )

    return ListenerInfo(
        listener=legacy,
        kind='legacy',
        label=legacy.hotkey_label,
        fell_back=not prefer_legacy,
    )
