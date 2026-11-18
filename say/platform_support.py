"""Platform capabilities, without importing a desktop toolkit."""
from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from typing import Any


def linux_session(env: Mapping[str, str] | None = None) -> str:
    values = os.environ if env is None else env
    if values.get('WAYLAND_DISPLAY') or values.get('XDG_SESSION_TYPE', '').lower() == 'wayland':
        return 'wayland'
    return 'x11' if values.get('DISPLAY') else 'headless'


def supported_backends(system: str | None = None) -> tuple[str, ...]:
    name = sys.platform if system is None else system
    if name.startswith('linux'):
        return ('moonshine', 'whisper-cpp')
    if name == 'darwin':
        return ('moonshine', 'whisper-mlx')
    return ('moonshine',)


def supports_backend(backend: str, system: str | None = None) -> bool:
    return backend in supported_backends(system)


def get_platform_info(
    *, system: str | None = None, env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    name = sys.platform if system is None else system
    values = os.environ if env is None else env
    if name.startswith('linux'):
        session = linux_session(values)
        automatic = session == 'x11'
        hotkey = values.get('SAY_HOTKEY') or '<ctrl>+<space>'
        label = '+'.join(part.strip().strip('<>').capitalize() for part in hotkey.split('+'))
        return {
            'platform': 'linux',
            'session': session,
            'automatic_insertion': automatic,
            'global_hotkey': automatic,
            'hotkey_label': label if automatic else None,
            'overlay': automatic,
            'manual_paste': not automatic,
            'supported_backends': list(supported_backends(name)),
        }
    mac = name == 'darwin'
    return {
        'platform': 'macos' if mac else 'windows' if name == 'win32' else 'other',
        'session': 'native',
        'automatic_insertion': mac,
        'global_hotkey': mac,
        'hotkey_label': 'Fn' if mac else None,
        'overlay': mac,
        'manual_paste': not mac,
        'supported_backends': list(supported_backends(name)),
    }
