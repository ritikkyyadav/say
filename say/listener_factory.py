# listener_factory.py — Pick the right hotkey listener, falling back when
# the preferred one cannot start.
#
# Both FnHotkeyListener (Quartz CGEventTap, Fn-aware) and HotkeyListener
# (pynput, ctrl+space) share the same callback shape. This module hides
# the choice from callers and notifies on fallback.

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from logging_setup import get_logger

_log = get_logger('listener_factory')


# Return values are ignored: the controller's start/stop/cancel return a bool.
Callback = Callable[[], object]

# How often the upgrader re-checks Accessibility, and how far it backs off
# when the grant is present but the tap is still refused (which usually means
# Input Monitoring is the one still missing).
UPGRADE_POLL_SECONDS = 2.0
UPGRADE_MAX_POLL_SECONDS = 30.0


@dataclass(frozen=True)
class ListenerInfo:
    listener: Any
    kind: str        # 'fn' or 'legacy'
    label: str       # user-facing description of the active hotkey
    fell_back: bool  # True if the preferred listener was not used


def _stop_quietly(listener: Any) -> None:
    try:
        listener.stop()
    except Exception:
        _log.debug('Listener stop raised while cleaning up', exc_info=True)


def try_start_fn_listener(
    on_start: Callback,
    on_stop: Callback,
    on_cancel: Callback | None = None,
    on_arm: Callback | None = None,
    on_disarm: Callback | None = None,
    start_timeout: float = 2.0,
    on_reinsert: Callback | None = None,
    on_undo: Callback | None = None,
) -> tuple[ListenerInfo | None, str | None]:
    """Attempt to start the Fn (CGEventTap) listener.

    Returns `(info, error)`. `info` is None when the tap could not be created,
    which is almost always because Accessibility has not been granted to this
    exact binary. Split out of `make_listener` so `ListenerUpgrader` can retry
    just this part later without risking a second fallback listener.
    """
    try:
        from hotkey_fn import FnHotkeyListener
    except Exception as exc:
        return None, f'Fn listener import failed: {exc}'

    fn = FnHotkeyListener(
        on_start_cb=on_start,
        on_stop_cb=on_stop,
        on_cancel_cb=on_cancel,
        on_arm_cb=on_arm,
        on_disarm_cb=on_disarm,
        on_reinsert_cb=on_reinsert,
        on_undo_cb=on_undo,
    )
    try:
        started = fn.start(timeout=start_timeout)
    except Exception as exc:
        _stop_quietly(fn)
        return None, f'Fn listener exception: {exc}'

    if not started:
        _stop_quietly(fn)
        return None, (
            'Fn listener could not create CGEventTap '
            '(grant Accessibility + Input Monitoring permissions to enable Fn).'
        )

    return (
        ListenerInfo(listener=fn, kind='fn', label=fn.hotkey_label, fell_back=False),
        None,
    )


class ListenerUpgrader:
    """Swap a fallen-back legacy listener for the Fn listener, live.

    macOS applies a TCC grant only to processes that started after it, which
    is why "quit and reopen" is the standard advice. But that only describes
    the check an app performs *at startup*: `AXIsProcessTrusted()` flips to
    True inside the already-running process the moment the user toggles the
    switch, and a CGEventTap created from that point on is accepted. So the
    restart is avoidable — and worth avoiding, because the thing users
    actually restart is the window, while the engine outlives it and stays on
    the fallback hotkey with no indication why.

    This does not take that on faith. It polls the cheap non-prompting trust
    check and then tries to build a *real* tap, swapping only if that
    succeeds. If some macOS release does start requiring a restart, the
    watcher simply never fires and we stay exactly where we already were.
    """

    def __init__(
        self,
        start_fn_listener: Callable[[], tuple[ListenerInfo | None, str | None]],
        on_upgraded: Callable[[ListenerInfo], None],
        poll_seconds: float = UPGRADE_POLL_SECONDS,
        max_poll_seconds: float = UPGRADE_MAX_POLL_SECONDS,
    ) -> None:
        self._start_fn_listener = start_fn_listener
        self._on_upgraded = on_upgraded
        self._poll = poll_seconds
        self._max_poll = max_poll_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        # Clear rather than assume: a stop() that lands before start() would
        # otherwise leave the event set, and the new thread would fall out of
        # its first wait() immediately and never watch anything.
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name='listener-upgrade', daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)

    @property
    def running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def _run(self) -> None:
        try:
            from permissions import accessibility_trusted
        except Exception:
            _log.debug('No Accessibility check available; not watching for an upgrade')
            return

        wait = self._poll
        while not self._stop.wait(wait):
            try:
                if not accessibility_trusted():
                    wait = self._poll
                    continue

                info, error = self._start_fn_listener()
                if info is None:
                    # Trusted, but the tap was still refused — Input Monitoring
                    # is usually the one outstanding. Back off rather than
                    # rebuilding a tap every couple of seconds forever.
                    wait = min(wait * 2, self._max_poll)
                    _log.debug('Trusted but the tap was refused (%s); retrying in %.0fs',
                               error, wait)
                    continue

                try:
                    self._on_upgraded(info)
                except Exception:
                    _log.exception('Upgrade callback failed; reverting to the fallback')
                    _stop_quietly(info.listener)
                    return

                _log.info(
                    'Accessibility granted — hotkey upgraded to %s with no restart.',
                    info.label,
                )
                return
            except Exception:
                _log.exception('Listener upgrade attempt failed; staying on the fallback')
                return


def make_listener(
    on_start: Callback,
    on_stop: Callback,
    on_cancel: Callback | None = None,
    prefer_legacy: bool = False,
    start_timeout: float = 2.0,
    on_arm: Callback | None = None,
    on_disarm: Callback | None = None,
    on_reinsert: Callback | None = None,
    on_undo: Callback | None = None,
) -> ListenerInfo:
    """Create and start a hotkey listener, with automatic fallback.

    Tries `FnHotkeyListener` (Quartz CGEventTap) first. If that fails to
    start (e.g. CGEventTap creation refused because Accessibility was
    not granted, or `Quartz` is unavailable), falls back to the legacy
    `HotkeyListener` (pynput, ctrl+space).

    `on_arm` / `on_disarm` are optional and only used by the Fn listener: they
    fire when the trigger key goes down and when the gesture is abandoned, so
    the recorder can pre-open the mic and keep its pre-roll buffer warm during
    the tap-vs-hold window. The legacy pynput listener has no such window.

    Raises RuntimeError if neither listener can be started.
    """
    fn_error: str | None = None

    if not prefer_legacy:
        info, fn_error = try_start_fn_listener(
            on_start=on_start,
            on_stop=on_stop,
            on_cancel=on_cancel,
            on_arm=on_arm,
            on_disarm=on_disarm,
            start_timeout=start_timeout,
            on_reinsert=on_reinsert,
            on_undo=on_undo,
        )
        if info is not None:
            return info

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
