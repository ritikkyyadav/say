"""Shared fixtures for native packaged-binary smoke tests, never real user audio."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ('Hello. This is a local voice dictation test. The quick brown fox jumps over the lazy dog. '
             'Everything stays on this computer.')


def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
    return subprocess.run(argv, check=True, **kwargs)


def speech_fixture(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    source = folder / 'speech-original.wav'
    target = folder / 'speech.wav'
    run(['espeak-ng', '-v', 'en-us', '-s', '145', '-w', str(source), REFERENCE])
    run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i', str(source),
         '-ar', '16000', '-ac', '1', '-c:a', 'pcm_s16le', str(target)])
    return target


def isolated_env(folder: Path, *, offline: bool = True) -> dict[str, str]:
    env = dict(os.environ)
    for key in ('PYTHONPATH', 'PYTHONHOME', 'SAY_ENGINE_BIN', 'SAY_BUNDLED_MODELS_DIR',
                'HF_TOKEN', 'HUGGING_FACE_HUB_TOKEN', 'OPENAI_API_KEY'):
        env.pop(key, None)
    for key, name in (('HOME', 'home'), ('XDG_DATA_HOME', 'data'),
                      ('XDG_CONFIG_HOME', 'config'), ('XDG_CACHE_HOME', 'cache'),
                      ('XDG_STATE_HOME', 'state'), ('XDG_RUNTIME_DIR', 'runtime')):
        path = folder / name
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.chmod(0o700)
        env[key] = str(path)
    env['SAY_MOONSHINE_CACHE'] = str(folder / 'empty-moonshine-cache')
    env['SAY_WHISPER_CPP_CACHE'] = str(folder / 'cpp-cache')
    env['HF_HUB_CACHE'] = str(folder / 'empty-hf-cache')
    env['SAY_HOTKEY'] = '<ctrl>+<space>'
    if offline:
        for key in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy'):
            env[key] = 'http://127.0.0.1:9'
        env['NO_PROXY'] = env['no_proxy'] = '127.0.0.1,localhost'
        env['HF_HUB_OFFLINE'] = '1'
    settings = Path(env['XDG_DATA_HOME']) / 'Say'
    settings.mkdir(mode=0o700, exist_ok=True)
    config = settings / 'settings.json'
    config.write_text(json.dumps({'SHOW_NOTIFICATIONS': False, 'SAVE_RECORDINGS': False}))
    config.chmod(0o600)
    return env


def wait_for(predicate, timeout: float = 45, description: str = 'condition') -> Any:
    deadline = time.monotonic() + timeout
    error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            result = predicate()
            if result:
                return result
        except (OSError, ValueError) as exc:
            error = exc
        time.sleep(0.1)
    raise AssertionError(f'Timed out waiting for {description}: {error}')


def runtime_info(env: dict[str, str]) -> dict:
    path = Path(env['XDG_DATA_HOME']) / 'Say' / 'runtime.json'
    data = json.loads(path.read_text())
    assert path.stat().st_mode & 0o077 == 0, 'Runtime credentials are not private.'
    assert data['host'] == '127.0.0.1' and 0 < int(data['port']) < 65536
    return data


def api(info: dict, path: str, payload: dict | None = None) -> Any:
    request = urllib.request.Request(
        f'http://127.0.0.1:{info["port"]}{path}',
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={'Authorization': f'Bearer {info["token"]}', 'Content-Type': 'application/json'},
    )
    with urllib.request.urlopen(request, timeout=4) as response:
        return json.load(response)


def word_error_rate(text: str) -> float:
    sys.path.insert(0, str(ROOT / 'say'))
    from corrections import word_errors

    errors, words = word_errors(REFERENCE, text)
    return errors / max(1, words)
