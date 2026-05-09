"""Polls the system default input device and notifies on change.

Detects events like AirPods unplugged mid-recording, mic permission
revoked, or the user picking a different default mic in System
Settings. Polling avoids the complexity of subscribing to CoreAudio
property listeners through PyObjC.

The watcher fires its callback on a daemon thread; the callback may
freely interact with the audio recorder (e.g. call shutdown()).
"""

from __future__ import annotations

import threading
from collections.abc import Callable

import config
from logging_setup import get_logger

_log = get_logger('device_watcher')


class DeviceWatcher:
    def __init__(self, on_change: Callable[[str | None], None]):
        self._on_change = on_change
        self._last_default: str | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @staticmethod
    def _current_default_input() -> str | None:
        try:
            import sounddevice as sd
            default = sd.default.device
            input_idx = default[0] if isinstance(default, (list, tuple)) else default
            if input_idx is None or (isinstance(input_idx, int) and input_idx < 0):
                return None
            info = sd.query_devices(input_idx)
            name = info.get('name') if isinstance(info, dict) else None
            return name
        except Exception:
            return None

    def start(self) -> None:
        if config.DEVICE_WATCH_INTERVAL_S <= 0:
            return
        if self._thread is not None and self._thread.is_alive():
            return
        self._last_default = self._current_default_input()
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name='DeviceWatcher', daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=3.0)
        self._thread = None

    def _loop(self) -> None:
        interval = max(0.5, float(config.DEVICE_WATCH_INTERVAL_S))
        while not self._stop.wait(interval):
            current = self._current_default_input()
            if current != self._last_default:
                previous = self._last_default
                self._last_default = current
                try:
                    self._on_change(current)
                except Exception:
                    _log.exception('DeviceWatcher callback raised')
                else:
                    _log.info(
                        'Default input device changed: %r -> %r',
                        previous,
                        current,
                    )
