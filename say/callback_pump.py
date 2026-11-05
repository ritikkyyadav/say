"""Run hotkey callbacks off the event thread, in the order they were fired.

Both hotkey listeners used to start a fresh daemon thread per callback. That
kept the CGEventTap callback fast — nothing slow may run on the tap thread —
but it threw away ordering: a push-to-talk hold released a few milliseconds
after the tap-vs-hold window resolved fired `on_start` and `on_stop` on two
threads with no guarantee which ran first. When stop won, the controller had
nothing to stop; start then ran, and the recording carried on until the
15-minute cap or the next gesture.

One worker per listener, fed by a queue, gives the callbacks the order a
human produced them in. The only semantic change is that a callback which
blocks (a wedged microphone open, bounded by `audio._OPEN_TIMEOUT`) now delays
the ones behind it — which is exactly the case where running them concurrently
was wrong. Pure Python, no AppKit.
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable

from logging_setup import get_logger

_log = get_logger('callback_pump')

_STOP = object()


class CallbackPump:
    """A single daemon worker that runs zero-argument callbacks in FIFO order.

    Starts lazily on the first `fire()` so a listener that is constructed but
    never started costs nothing. `stop()` is idempotent and safe from any
    thread, including from inside a callback.
    """

    def __init__(self, name: str = 'say-callbacks') -> None:
        self._name = name
        self._queue: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def fire(self, callback: Callable[[], object]) -> None:
        """Queue `callback`. Never blocks and never raises into the caller."""
        self._ensure_worker()
        self._queue.put(callback)

    def stop(self, timeout: float = 2.0) -> None:
        """Let queued callbacks finish, then end the worker."""
        with self._lock:
            thread = self._thread
            self._thread = None
        if thread is None:
            return
        self._queue.put(_STOP)
        if thread is not threading.current_thread():
            thread.join(timeout=timeout)

    @property
    def running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def _ensure_worker(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(
                target=self._run, name=self._name, daemon=True
            )
            self._thread.start()

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is _STOP:
                return
            try:
                item()
            except Exception:
                _log.exception('Hotkey callback raised')
