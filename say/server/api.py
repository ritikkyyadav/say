"""FastAPI app exposed on 127.0.0.1 for the desktop UI.

Endpoints:
  GET  /health                       — liveness + current engine state
  GET  /listener                     — hotkey listener status (kind, label, fell_back, perms)
  GET  /user/profile                 — first-name for the home greeting
  GET  /transcripts                  — reverse-chronological list, paginated
  GET  /stats/summary                — totals, WPM, streak, monthly delta
  GET  /stats/desktop_usage          — per-app usage rollup
  GET  /stats/heatmap                — per-day count grid
  POST /transcripts/{id}/copy        — telemetry stub (no-op)
  GET  /events                       — SSE stream of DictationEvent state

Security model: bind to 127.0.0.1 only, optional bearer token in header
or `?token=` query string. Cross-origin is permitted from `tauri://localhost`
and the dev origin (`http://localhost:1420`).
"""
from __future__ import annotations

import asyncio
import logging
import os
import socket
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from .events import get_bus
from .runtime import clear_runtime, write_runtime
from .store import TranscriptStore, get_store


class ActiveModelRequest(BaseModel):
    """Body for POST /models/active."""

    id: str

_log = logging.getLogger('say.server.api')

# Tauri serves the production UI from a custom protocol; allow it plus the dev origin.
_ALLOWED_ORIGINS = [
    'tauri://localhost',
    'http://tauri.localhost',
    'http://localhost:1420',
    'http://127.0.0.1:1420',
]


def _user_first_name() -> str:
    """Best-effort first-name lookup; falls back to '$USER' or 'there'."""
    if sys.platform == 'darwin':
        try:
            from Foundation import NSFullUserName  # type: ignore

            full = str(NSFullUserName() or '').strip()
            if full:
                return full.split(' ')[0]
        except Exception:
            pass
    name = os.environ.get('USER') or os.environ.get('USERNAME') or ''
    if name:
        return name.split(' ')[0]
    try:
        import getpass

        return getpass.getuser()
    except Exception:
        return 'there'


def _current_token(app: FastAPI) -> str | None:
    return getattr(app.state, 'auth_token', None)


def _check_auth(app: FastAPI, request: Request) -> None:
    token = _current_token(app)
    if not token:
        return
    presented = request.headers.get('Authorization', '')
    if presented.startswith('Bearer '):
        presented = presented.removeprefix('Bearer ').strip()
    else:
        presented = request.query_params.get('token', '') or ''
    if presented != token:
        raise HTTPException(status_code=401, detail='Bad or missing token')


# ── Listener status (set from app.py once make_listener has run) ─────────
_LISTENER_STATE: dict[str, Any] = {
    'kind': None,           # 'fn' | 'legacy' | None
    'label': None,
    'fell_back': False,
    'fn_error': None,       # CGEventTap creation error, if any
    'started': False,
}


def set_listener_state(**kwargs: Any) -> None:
    """Engine calls this once make_listener returns so /listener can
    expose the active mode + any permission-related error to the UI."""
    _LISTENER_STATE.update(kwargs)


def _accessibility_trusted() -> bool:
    """Whether AXIsProcessTrusted reports True for the current process.

    Returns False on import failure so the UI errs on the side of
    showing the permissions banner.
    """
    try:
        import HIServices  # type: ignore[import-not-found]
        return bool(HIServices.AXIsProcessTrusted())
    except Exception:
        try:
            from ApplicationServices import AXIsProcessTrusted  # type: ignore[import-not-found]
            return bool(AXIsProcessTrusted())
        except Exception:
            return False


def create_app(*, store: TranscriptStore | None = None, auth_token: str | None = None) -> FastAPI:
    """Build the FastAPI app. Useful in tests with an injected store."""

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        get_bus().bind_loop(asyncio.get_running_loop())
        yield

    app = FastAPI(title='Say', version='0.1.0', lifespan=lifespan)
    app.state.store = store if store is not None else get_store()
    app.state.auth_token = auth_token

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_ALLOWED_ORIGINS,
        allow_credentials=False,
        allow_methods=['GET', 'POST', 'DELETE'],
        allow_headers=['Authorization', 'Content-Type'],
    )

    # Loopback-only enforcement is handled by uvicorn binding to 127.0.0.1
    # in serve_blocking(). We do not add a middleware-level check because
    # ASGI surfaces a "client host" that does not always reflect the real
    # socket (e.g. Starlette's TestClient reports "testclient").

    @app.get('/health')
    async def health(request: Request) -> dict[str, Any]:
        # /health intentionally does not require auth — the shell uses it
        # to discover whether the sidecar is alive before sending any
        # other request.
        snapshot = get_bus().snapshot()
        return {'ok': True, 'state': snapshot.get('state', 'idle')}

    @app.get('/listener')
    async def listener_state(request: Request) -> dict[str, Any]:
        # Same auth-free policy as /health — the UI polls this on every
        # window focus to decide whether to show the permissions banner.
        return {
            **_LISTENER_STATE,
            'accessibility_trusted': _accessibility_trusted(),
        }

    @app.get('/user/profile')
    async def user_profile(request: Request) -> dict[str, Any]:
        _check_auth(app, request)
        return {'first_name': _user_first_name()}

    @app.get('/transcripts')
    async def list_transcripts(
        request: Request,
        limit: int = Query(200, ge=1, le=1000),
        before: str | None = Query(None),
    ) -> list[dict[str, Any]]:
        _check_auth(app, request)
        rows = app.state.store.list(limit=limit, before=before)
        return [r.to_dict() for r in rows]

    @app.get('/stats/summary')
    async def stats_summary(request: Request) -> dict[str, Any]:
        _check_auth(app, request)
        return app.state.store.summary()

    @app.get('/stats/desktop_usage')
    async def stats_desktop_usage(
        request: Request,
        days: int = Query(30, ge=1, le=365),
    ) -> list[dict[str, Any]]:
        _check_auth(app, request)
        return app.state.store.desktop_usage(days=days)

    @app.get('/stats/heatmap')
    async def stats_heatmap(
        request: Request,
        days: int = Query(180, ge=7, le=365),
    ) -> list[dict[str, Any]]:
        _check_auth(app, request)
        return app.state.store.heatmap(days=days)

    @app.post('/transcripts/{transcript_id}/copy', status_code=status.HTTP_204_NO_CONTENT)
    async def copy_transcript(transcript_id: int, request: Request) -> Response:
        _check_auth(app, request)
        return Response(status_code=204)

    # ── Model management ─────────────────────────────────────────────────

    @app.get('/models')
    async def list_models(request: Request) -> dict[str, Any]:
        _check_auth(app, request)
        import model_manager

        mgr = model_manager.get_manager()
        return {
            'active': mgr.active_model(),
            'cache_bytes': mgr.total_disk_bytes(),
            'models': mgr.list_models(),
        }

    @app.post('/models/active')
    async def set_active_model(request: Request, body: ActiveModelRequest) -> dict[str, Any]:
        _check_auth(app, request)
        import model_manager

        try:
            active = model_manager.get_manager().set_active(body.id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {'active': active}

    @app.post('/models/{model_id:path}/download')
    async def download_model(model_id: str, request: Request) -> dict[str, Any]:
        _check_auth(app, request)
        import model_manager

        try:
            return model_manager.get_manager().start_download(model_id)
        except model_manager.InsufficientStorage as exc:
            raise HTTPException(status_code=507, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc))

    @app.post('/models/{model_id:path}/cancel')
    async def cancel_download(model_id: str, request: Request) -> dict[str, Any]:
        _check_auth(app, request)
        import model_manager

        cancelled = model_manager.get_manager().cancel_download(model_id)
        return {'cancelled': cancelled}

    @app.delete('/models/{model_id:path}')
    async def delete_model(model_id: str, request: Request) -> dict[str, Any]:
        _check_auth(app, request)
        import model_manager

        try:
            model_manager.get_manager().delete(model_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return {}

    @app.get('/events')
    async def events_stream(request: Request):
        _check_auth(app, request)
        bus = get_bus()

        async def gen() -> AsyncIterator[dict[str, Any]]:
            sub = await bus.subscribe()
            async with sub:
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        payload = await asyncio.wait_for(sub.__anext__(), timeout=15.0)
                        yield {'event': 'state', 'data': _json(payload)}
                    except asyncio.TimeoutError:
                        # Heartbeat keeps proxies / fetch readers alive.
                        yield {'event': 'ping', 'data': '{}'}

        return EventSourceResponse(gen())

    return app


def _json(payload: dict[str, Any]) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False)


# ── uvicorn launcher ───────────────────────────────────────────────────────


def _pick_free_port() -> int:
    """Reserve an ephemeral port and release it for uvicorn to bind."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return int(s.getsockname()[1])


def serve_blocking(*, host: str = '127.0.0.1', port: int | None = None, log_level: str = 'warning') -> None:
    """Boot the API and block. Returns when uvicorn shuts down.

    Writes `runtime.json` so the desktop shell can find the port.
    """
    import uvicorn

    chosen_port = port if port is not None else _pick_free_port()
    info = write_runtime(chosen_port)
    app = create_app(auth_token=info['token'])

    config = uvicorn.Config(
        app,
        host=host,
        port=chosen_port,
        log_level=log_level,
        loop='asyncio',
        access_log=False,
    )
    server = uvicorn.Server(config)
    try:
        server.run()
    finally:
        clear_runtime()


def serve_in_thread(**kwargs) -> 'threading.Thread':
    """Run the server on a background thread. Returns the thread."""
    import threading

    t = threading.Thread(target=serve_blocking, kwargs=kwargs, name='say-api', daemon=True)
    t.start()
    return t
