"""In-process pub/sub for DictationEvent → SSE.

The controller emits state changes via its `status_callback`. The HTTP
API wraps each connected SSE client in a queue and fans out events to
all of them. Clients that fall behind are dropped after the queue fills,
so a stalled UI cannot back-pressure the dictation engine.
"""
from __future__ import annotations

import asyncio
import threading
from collections.abc import Iterator
from typing import Any


class EventBus:
    def __init__(self, *, queue_size: int = 64):
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queues: list[asyncio.Queue[dict[str, Any]]] = []
        self._queue_size = queue_size
        self._last_state: dict[str, Any] = {
            'state': 'idle',
            'message': '',
            'mode': None,
        }

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        with self._lock:
            self._loop = loop

    def publish_threadsafe(self, payload: dict[str, Any]) -> None:
        """Publish from any thread. Drops events to slow consumers."""
        with self._lock:
            self._last_state = {**payload}
            loop = self._loop
            queues = list(self._queues)
        if loop is None:
            return
        for q in queues:
            try:
                loop.call_soon_threadsafe(self._safe_put, q, payload)
            except RuntimeError:
                # Loop closed mid-publish — ignore.
                pass

    @staticmethod
    def _safe_put(q: asyncio.Queue[dict[str, Any]], payload: dict[str, Any]) -> None:
        if q.full():
            try:
                q.get_nowait()
            except asyncio.QueueEmpty:
                pass
        q.put_nowait(payload)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._last_state)

    async def subscribe(self) -> 'EventBus._Subscription':
        loop = asyncio.get_running_loop()
        with self._lock:
            self._loop = loop
            q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=self._queue_size)
            self._queues.append(q)
            initial = dict(self._last_state)
        await q.put(initial)
        return EventBus._Subscription(self, q)

    def _unsubscribe(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        with self._lock:
            try:
                self._queues.remove(q)
            except ValueError:
                pass

    class _Subscription:
        def __init__(self, bus: 'EventBus', q: asyncio.Queue[dict[str, Any]]):
            self._bus = bus
            self._q = q

        async def __aenter__(self) -> 'EventBus._Subscription':
            return self

        async def __aexit__(self, *_exc) -> None:
            self._bus._unsubscribe(self._q)

        async def __aiter__(self) -> 'EventBus._Subscription':
            return self

        async def __anext__(self) -> dict[str, Any]:
            return await self._q.get()


_bus: EventBus | None = None
_bus_lock = threading.Lock()


def get_bus() -> EventBus:
    global _bus
    with _bus_lock:
        if _bus is None:
            _bus = EventBus()
        return _bus
