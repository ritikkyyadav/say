"""Authenticated controls for the Linux entry point's in-app recorder."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from .api import _check_auth
from .events import get_bus


class DictationControlRequest(BaseModel):
    action: Literal['start', 'stop', 'cancel']


def register_linux_controls(
    app: FastAPI, controller: Any, control: Callable[[str], bool],
    listener_started: Callable[[], bool] | None = None,
) -> None:
    @app.get('/dictation')
    def dictation_status(request: Request) -> dict[str, Any]:
        _check_auth(app, request)
        snapshot = get_bus().snapshot()
        latest = controller.last_result
        if latest is None and controller.recording_sequence == 0:
            rows = app.state.store.list(limit=1)
            if rows:
                row = rows[0]
                method = row.inject_method
                delivery = ('manual' if method == 'manual' else 'held' if method == 'held'
                            else 'inserted' if row.success else 'failed')
                latest = {'id': row.id, 'text': row.text, 'delivery': delivery}
        return {
            'state': controller.state.value,
            'message': str(snapshot.get('message') or ''),
            'mode': snapshot.get('mode'),
            'can_record': controller.can_start_recording(),
            'shortcut_started': bool(listener_started and listener_started()),
            'latest': latest,
        }

    @app.post('/dictation/control')
    def dictation_control(request: Request, body: DictationControlRequest) -> dict[str, bool]:
        _check_auth(app, request)
        if not control(body.action):
            raise HTTPException(status_code=409, detail='This action is not available right now.')
        return {'accepted': True}
