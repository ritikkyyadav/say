"""User-owned data paths shared by the engine's platform entry points."""
from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path


def application_data_dir(
    *, system: str | None = None, env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    name = sys.platform if system is None else system
    values = os.environ if env is None else env
    base_home = Path.home() if home is None else home
    if name == 'darwin':
        return base_home / 'Library' / 'Application Support' / 'Say'
    if name == 'win32':
        return Path(values.get('APPDATA') or base_home / 'AppData' / 'Roaming') / 'Say'
    configured = values.get('XDG_DATA_HOME', '')
    base = Path(configured) if configured and Path(configured).is_absolute() else base_home / '.local' / 'share'
    return base / 'Say'


def application_log_dir(
    *, system: str | None = None, env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    name = sys.platform if system is None else system
    if name == 'darwin':
        return (Path.home() if home is None else home) / 'Library' / 'Logs' / 'Say'
    return application_data_dir(system=name, env=env, home=home) / 'logs'


def ensure_private_directory(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.chmod(0o700)
