"""FastAPI app exposed on 127.0.0.1 for the desktop UI.

Endpoints:
  GET  /health                       — liveness + current engine state
  GET  /listener                     — hotkey listener status (kind, label, fell_back, perms)
  GET  /user/profile                 — first-name for the home greeting
  GET  /transcripts                  — reverse-chronological list, paginated
  GET  /stats/summary                — totals, WPM, streak, monthly delta, correction rate
  GET  /stats/desktop_usage          — per-app usage rollup
  GET  /stats/heatmap                — per-day count grid
  POST /transcripts/{id}/copy        — telemetry stub (no-op)
  GET  /corrections                  — correction ledger + measured correction rate
  GET  /recordings                   — saved audio receipts (newest first)
  GET  /recordings/config            — save toggle + retention policy + totals
  POST /recordings/config            — update save toggle / retention (prunes)
  POST /recordings/export            — bundle all clips + manifest into a local ZIP
  GET  /recordings/{id}/audio        — WAV bytes for one clip
  DELETE /recordings/{id}            — delete one clip
  DELETE /recordings                 — delete all clips
  GET  /events                       — SSE stream of DictationEvent state

Security model: bind to 127.0.0.1 only, optional bearer token in header
or `?token=` query string. Cross-origin is permitted from `tauri://localhost`
and the dev origin (`http://localhost:1420`).
"""
from __future__ import annotations

import asyncio
import logging
import os
import secrets
import socket
import sys
import threading
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


class ProfileRequest(BaseModel):
    """Body for POST /user/profile — both fields optional."""

    first_name: str | None = None
    onboarded: bool | None = None


class LanguageRequest(BaseModel):
    """Body for POST /language. `code: null` means detect automatically.

    `focus` is what the person dictates in (see languages.FOCUS_GROUPS).
    Sending a focus without a code applies the focus's default code.
    """

    code: str | None = None
    focus: str | None = None


class OverlayConfigRequest(BaseModel):
    """Body for POST /overlay — every field optional (partial update)."""

    theme: str | None = None
    colors: list[str] | None = None
    pattern: str | None = None
    show_overlay: bool | None = None
    contrast: str | None = None
    preview: bool | None = None


class RecordingsConfigRequest(BaseModel):
    """Body for POST /recordings/config — all fields optional (partial update)."""

    save_recordings: bool | None = None
    retention_mode: str | None = None
    retention_days: int | None = None
    max_bytes: int | None = None

_log = logging.getLogger('say.server.api')

# Tauri serves the production UI from a custom protocol; allow it plus the dev origin.
_ALLOWED_ORIGINS = [
    'tauri://localhost',
    'http://tauri.localhost',
    'http://localhost:1420',
    'http://127.0.0.1:1420',
]


# A display name, not a field with a schema. Capped so a pathological value in
# a hand-edited settings file cannot blow out the layout it is rendered into.
_MAX_NAME_CHARS = 48


def _user_first_name() -> str:
    """Best-effort first-name lookup; falls back to '$USER' or 'there'."""
    if sys.platform == 'darwin':
        try:
            from Foundation import NSFullUserName

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
    # Constant-time: a plain `!=` leaks the token prefix through timing. The
    # attacker has to be a local process already, so this is defence in depth
    # rather than the load-bearing control — but it costs one import.
    if not secrets.compare_digest(presented, token):
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
        import HIServices
        return bool(HIServices.AXIsProcessTrusted())
    except Exception:
        try:
            from ApplicationServices import AXIsProcessTrusted
            return bool(AXIsProcessTrusted())
        except Exception:
            return False


def create_app(*, store: TranscriptStore | None = None, auth_token: str | None = None) -> FastAPI:
    """Build the FastAPI app. Useful in tests with an injected store."""

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        get_bus().bind_loop(asyncio.get_running_loop())
        yield

    app = FastAPI(
        title='Say',
        version='0.1.0',
        lifespan=lifespan,
        # FastAPI mounts /docs, /redoc and /openapi.json by default, all
        # unauthenticated. Nothing here chose that. Two reasons to turn them
        # off rather than shrug: an unauthenticated machine-readable index of
        # every route is free reconnaissance for anything already running as
        # this user, and Swagger UI fetches its own JavaScript from a CDN —
        # so merely opening /docs would make this app perform the one thing
        # it promises never to do.
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
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
        import config

        chosen = str(getattr(config, 'USER_NAME', '') or '').strip()
        return {
            # What you asked to be called wins over what the OS thinks your
            # account is named. Neither leaves this machine.
            'first_name': chosen or _user_first_name(),
            'onboarded': bool(getattr(config, 'ONBOARDED', False)),
        }

    @app.post('/user/profile')
    async def set_user_profile(request: Request, body: ProfileRequest) -> dict[str, Any]:
        _check_auth(app, request)
        import settings as settings_mod

        if body.first_name is not None:
            # Trimmed and length-capped before it is written: this string is
            # rendered straight into the greeting, and a settings file is a
            # place a person can edit by hand.
            name = ' '.join(body.first_name.split())[:_MAX_NAME_CHARS]
            settings_mod.set_value('USER_NAME', name)

        if body.onboarded is not None:
            settings_mod.set_value('ONBOARDED', bool(body.onboarded))

        return await user_profile(request)

    # ── Language ─────────────────────────────────────────────────────────

    def _language_payload() -> dict[str, Any]:
        import languages
        import model_manager

        code = languages.current()
        focus = languages.current_focus()
        return {
            'code': code,
            'auto': code is None,
            'name': languages.name_for(code),
            'options': languages.options_for(focus),
            'focus': focus,
            'groups': languages.groups(),
            'recommended_model': languages.recommended_model(focus),
            # Honest about the bundled model: Moonshine is English-only and
            # takes no language argument, so the choice is inert there.
            'applies_to_active_model': languages.applies_to(
                model_manager.get_manager().active_model()
            ),
        }

    @app.get('/language')
    async def get_language(request: Request) -> dict[str, Any]:
        _check_auth(app, request)
        return _language_payload()

    @app.post('/language')
    async def set_language(request: Request, body: LanguageRequest) -> dict[str, Any]:
        """Persist the decoder language; `null` = automatic detection.

        Until this existed `LANGUAGE` was 'en' with no way to change it from
        the desktop app, so the multilingual models the Models page offered
        could not actually transcribe anything but English.
        """
        _check_auth(app, request)
        import languages
        import settings as settings_mod

        # Validate everything before writing anything: a bad focus must not
        # leave a half-applied change behind.
        sent = body.model_fields_set
        try:
            focus = languages.normalise_focus(body.focus) if 'focus' in sent else None
            code = languages.normalise(body.code) if 'code' in sent else None
        except languages.BadLanguage as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        if focus is not None:
            settings_mod.set_value('LANGUAGE_FOCUS', focus)
            if 'code' not in sent:
                # A new focus without an explicit language takes the focus's
                # default: English is fixed, everything else detects.
                code = languages.default_code_for(focus)
                settings_mod.set_value('LANGUAGE', code)
        if 'code' in sent:
            settings_mod.set_value('LANGUAGE', code)
        return _language_payload()

    @app.get('/transcripts')
    async def list_transcripts(
        request: Request,
        limit: int = Query(200, ge=1, le=1000),
        before: str | None = Query(None),
    ) -> list[dict[str, Any]]:
        _check_auth(app, request)
        rows = app.state.store.list(limit=limit, before=before)
        store = app.state.store
        out: list[dict[str, Any]] = []
        for row in rows:
            item = row.to_dict()
            # The transcripts feed carries its own playback, so it needs to
            # know which rows actually have a clip behind them. Saving audio is
            # opt-in, so for most people this is False everywhere.
            #
            # The path being *recorded* is not enough: retention sweeps and
            # manual deletes remove the file and leave the column set, and a
            # play button that 404s is worse than no play button.
            try:
                path = store.audio_path_for(row.id)
                item['has_audio'] = bool(path) and os.path.exists(path)
            except Exception:
                item['has_audio'] = False
            out.append(item)
        return out

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

    @app.get('/stats/vocabulary')
    def stats_vocabulary(
        request: Request,
        limit: int = Query(2000, ge=1, le=20000),
    ) -> dict[str, Any]:
        """Which words this person actually uses, and what that says about them.

        **Deliberately `def`, not `async def`.** This is the only genuinely
        CPU-bound endpoint in the app — measured, it is 220 ms over a real
        344-dictation corpus and 12.7 s at the limit this route allows. On the
        event loop that would not merely be slow, it would hold up *every*
        other request for the duration: `/health`, the SSE stream the UI reads
        its recording state from, everything. A sync endpoint is handed to
        Starlette's threadpool instead, so a heavy analysis costs one worker
        and nothing else notices.

        Recomputed rather than stored, because the extraction rules keep
        improving and a cached report would freeze today's rules over
        yesterday's transcripts — but memoised on the corpus it was computed
        from, so re-opening Home does not pay for it twice.
        """
        _check_auth(app, request)
        import vocabulary

        rows = app.state.store.list(limit=limit)
        # Cheap identity for "the same corpus": how many rows and the newest
        # id. Any new dictation changes one of them.
        key = (limit, len(rows), rows[0].id if rows else 0)
        cached = getattr(app.state, 'vocab_cache', None)
        if cached is not None and cached[0] == key:
            return cached[1]

        try:
            report = vocabulary.analyse(r.to_dict() for r in rows).to_dict()
        except Exception:
            _log.exception('Vocabulary analysis failed')
            raise HTTPException(status_code=500, detail='Could not analyse vocabulary')

        app.state.vocab_cache = (key, report)
        return report

    # ── Overlay appearance ───────────────────────────────────────────────

    @app.get('/overlay')
    async def get_overlay(request: Request) -> dict[str, Any]:
        _check_auth(app, request)
        import config
        import overlay_theme

        return {
            'theme': getattr(config, 'OVERLAY_THEME', 'ink'),
            'colors': list(getattr(config, 'OVERLAY_CUSTOM_COLORS', []) or []),
            'pattern': getattr(config, 'OVERLAY_PATTERN', overlay_theme.DEFAULT_PATTERN),
            'patterns': [dict(p) for p in overlay_theme.PATTERN_INFO],
            'show_overlay': bool(getattr(config, 'SHOW_OVERLAY', True)),
            'contrast': getattr(config, 'OVERLAY_CONTRAST', 'auto'),
            'max_colors': overlay_theme.MAX_COLORS,
            'presets': [overlay_theme.to_dict(t) for t in overlay_theme.catalog()],
        }

    @app.post('/overlay')
    async def set_overlay(request: Request, body: OverlayConfigRequest) -> dict[str, Any]:
        _check_auth(app, request)
        import config
        import overlay_theme
        import settings as settings_mod

        if body.colors is not None:
            try:
                colors = overlay_theme.clean_colors(body.colors)
            except overlay_theme.BadColor as exc:
                raise HTTPException(status_code=400, detail=str(exc))
            settings_mod.set_value('OVERLAY_CUSTOM_COLORS', colors)

        if body.theme is not None:
            theme = body.theme.strip().lower()
            if theme not in overlay_theme.VALID_THEME_IDS:
                raise HTTPException(status_code=400, detail=f'Unknown overlay theme: {theme}')
            stored = list(getattr(config, 'OVERLAY_CUSTOM_COLORS', []) or [])
            if theme == overlay_theme.CUSTOM_ID and not stored:
                raise HTTPException(
                    status_code=400,
                    detail='Pick at least one colour before switching to a custom overlay.',
                )
            settings_mod.set_value('OVERLAY_THEME', theme)

        if body.pattern is not None:
            pattern = body.pattern.strip().lower()
            if pattern not in overlay_theme.VALID_PATTERNS:
                raise HTTPException(
                    status_code=400, detail=f'Unknown overlay pattern: {pattern}'
                )
            settings_mod.set_value('OVERLAY_PATTERN', pattern)

        if body.contrast is not None:
            valid: set[str] = getattr(config, 'VALID_OVERLAY_CONTRAST', set())
            if valid and body.contrast not in valid:
                raise HTTPException(
                    status_code=400,
                    detail=f'contrast must be one of: {", ".join(sorted(valid))}',
                )
            settings_mod.set_value('OVERLAY_CONTRAST', body.contrast)

        if body.show_overlay is not None:
            settings_mod.set_value('SHOW_OVERLAY', bool(body.show_overlay))

        # Repaint the running glyph. Best-effort by design: the settings are
        # already saved, and an overlay that cannot be reached (no run loop, a
        # headless test) must not turn a successful save into a 500.
        overlay = overlay_theme.live_overlay()
        if overlay is not None:
            try:
                overlay.set_theme()
                if body.preview and getattr(config, 'SHOW_OVERLAY', True):
                    overlay.preview()
            except Exception:
                _log.debug('Could not repaint the live overlay', exc_info=True)

        return await get_overlay(request)

    @app.post('/overlay/preview')
    async def preview_overlay(request: Request) -> dict[str, Any]:
        """Flash the real glyph on screen so a colour can be judged in place."""
        _check_auth(app, request)
        import overlay_theme

        overlay = overlay_theme.live_overlay()
        if overlay is None:
            return {'shown': False, 'reason': 'no overlay in this process'}
        try:
            overlay.set_theme()
            overlay.preview()
            return {'shown': True}
        except Exception:
            _log.debug('Overlay preview failed', exc_info=True)
            return {'shown': False, 'reason': 'overlay unavailable'}

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

    # ── Recordings (audio receipts) ──────────────────────────────────────

    @app.get('/corrections')
    async def list_corrections(
        request: Request,
        limit: int = Query(200, ge=1, le=2000),
    ) -> dict[str, Any]:
        """Corrections plus coverage/outcomes by app, including unscored edits.

        Historical observations have unknown classification; a low-coverage
        correction rate must never be mistaken for transcription accuracy.
        """
        _check_auth(app, request)
        store = app.state.store
        return {
            'summary': store.correction_summary(),
            'corrections': store.corrections(limit=limit),
        }

    @app.get('/recordings')
    async def list_recordings(
        request: Request,
        limit: int = Query(500, ge=1, le=5000),
    ) -> list[dict[str, Any]]:
        _check_auth(app, request)
        import recordings

        return recordings.list_recordings(app.state.store, limit=limit)

    @app.get('/recordings/config')
    async def get_recordings_config(request: Request) -> dict[str, Any]:
        _check_auth(app, request)
        import recordings

        return recordings.recordings_config(app.state.store)

    @app.post('/recordings/config')
    async def set_recordings_config(
        request: Request, body: RecordingsConfigRequest
    ) -> dict[str, Any]:
        _check_auth(app, request)
        import recordings

        try:
            return recordings.apply_recordings_config(
                app.state.store,
                save_recordings=body.save_recordings,
                retention_mode=body.retention_mode,
                retention_days=body.retention_days,
                max_bytes=body.max_bytes,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.post('/recordings/export')
    async def export_recordings(request: Request) -> dict[str, Any]:
        _check_auth(app, request)
        import recordings

        return recordings.export_zip(app.state.store)

    @app.get('/recordings/{recording_id}/audio')
    async def recording_audio(recording_id: int, request: Request) -> Response:
        _check_auth(app, request)
        import recordings

        data = recordings.read_bytes(app.state.store, recording_id)
        if data is None:
            raise HTTPException(status_code=404, detail='No recording for that id')
        return Response(
            content=data,
            media_type='audio/wav',
            headers={'Cache-Control': 'no-store'},
        )

    @app.delete('/recordings/{recording_id}')
    async def delete_recording(recording_id: int, request: Request) -> dict[str, Any]:
        _check_auth(app, request)
        import recordings

        deleted = recordings.delete_recording(app.state.store, recording_id)
        return {'deleted': deleted}

    @app.delete('/recordings')
    async def clear_recordings(request: Request) -> dict[str, Any]:
        _check_auth(app, request)
        import recordings

        return {'deleted': recordings.delete_all(app.state.store)}

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
                    except TimeoutError:
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


def serve_blocking(
    *,
    host: str = '127.0.0.1',
    port: int | None = None,
    log_level: str = 'warning',
    owner_pid: int | None = None,
) -> None:
    """Boot the API and block. Returns when uvicorn shuts down.

    Writes `runtime.json` so the desktop shell can find the port — and, when
    this engine was launched as a sidecar, which shell it belongs to.
    """
    import uvicorn

    chosen_port = port if port is not None else _pick_free_port()
    info = write_runtime(chosen_port, owner_pid=owner_pid)
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


def serve_in_thread(**kwargs) -> threading.Thread:
    """Run the server on a background thread. Returns the thread."""
    t = threading.Thread(target=serve_blocking, kwargs=kwargs, name='say-api', daemon=True)
    t.start()
    return t
