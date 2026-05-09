# permissions.py - macOS environment and permission helpers.

from __future__ import annotations

import importlib.util
import platform
import subprocess
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class EnvironmentCheck:
    name: str
    ok: bool
    message: str


_PRIVACY_URLS = {
    'microphone': 'x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone',
    'accessibility': 'x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility',
    'input-monitoring': 'x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent',
}


def open_privacy_settings(section: str = 'accessibility') -> bool:
    """Open a macOS Privacy & Security pane."""
    url = _PRIVACY_URLS.get(section)
    if not url:
        return False
    try:
        subprocess.run(['open', url], check=False)
        return True
    except Exception:
        return False


def _has_module(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None


def check_environment() -> list[EnvironmentCheck]:
    """Return non-invasive startup checks for the local machine."""
    checks: list[EnvironmentCheck] = []

    macos = platform.mac_ver()[0]
    checks.append(
        EnvironmentCheck(
            'macOS',
            bool(macos),
            f'macOS {macos}' if macos else 'This app is intended for macOS.',
        )
    )

    version_ok = sys.version_info >= (3, 11)
    checks.append(
        EnvironmentCheck(
            'Python',
            version_ok,
            f'Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}',
        )
    )

    machine = platform.machine()
    checks.append(
        EnvironmentCheck(
            'CPU',
            machine in {'arm64', 'aarch64'},
            f'{machine or "unknown"} (Apple Silicon recommended)',
        )
    )

    for module_name in (
        'mlx_whisper',
        'sounddevice',
        'numpy',
        'pynput',
        'pyperclip',
        'rumps',
        'Quartz',
    ):
        checks.append(
            EnvironmentCheck(
                module_name,
                _has_module(module_name),
                'installed' if _has_module(module_name) else 'missing',
            )
        )

    return checks


def microphone_summary() -> str:
    """Return a short summary of available audio devices."""
    try:
        import sounddevice as sd

        return str(sd.query_devices())
    except Exception as exc:
        return f'Unable to query microphone devices: {exc}'


def accessibility_probe() -> EnvironmentCheck:
    """Probe whether System Events is callable through AppleScript."""
    try:
        result = subprocess.run(
            ['osascript', '-e', 'tell application "System Events" to get name'],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception as exc:
        return EnvironmentCheck('Accessibility', False, str(exc))

    if result.returncode == 0:
        return EnvironmentCheck('Accessibility', True, 'System Events is reachable.')
    return EnvironmentCheck(
        'Accessibility',
        False,
        result.stderr.strip() or 'Accessibility probe failed.',
    )
