"""Runtime handshake file written by the engine, read by the desktop shell.

When the engine boots in --server mode it picks an ephemeral loopback
port, starts uvicorn against it, and writes the chosen port + a short
auth token to `runtime.json` next to the SQLite store. The Tauri shell
reads the file before opening the browser-side window so it can route
fetches to the right port and present the token on requests.
"""
from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import Any

from .store import _app_data_dir


def runtime_path() -> Path:
    return _app_data_dir() / 'runtime.json'


def write_runtime(
    port: int,
    *,
    token: str | None = None,
    pid: int | None = None,
    owner_pid: int | None = None,
) -> dict[str, Any]:
    """Publish the engine's endpoint, and who it belongs to.

    `owner_pid` is the desktop shell that launched this engine as a sidecar.
    It is what lets the shell distinguish *its* engine from one a developer
    started by hand: an engine with no owner is nobody's to kill, while one
    whose owner is dead is an orphan that should never have survived.
    """
    payload = {
        'host': '127.0.0.1',
        'port': int(port),
        'token': token or secrets.token_urlsafe(24),
        'pid': pid if pid is not None else os.getpid(),
    }
    if owner_pid:
        payload['owner_pid'] = int(owner_pid)
    p = runtime_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=2), encoding='utf-8')
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    return payload


def read_runtime() -> dict[str, Any] | None:
    p = runtime_path()
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding='utf-8'))
    except Exception:
        return None


def clear_runtime() -> None:
    p = runtime_path()
    try:
        p.unlink()
    except FileNotFoundError:
        pass
