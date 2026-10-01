"""Minimal local provenance for new dictations; never stores prompt or dictionary text."""
from __future__ import annotations

import hashlib
import json
import os
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

import config
import model_catalog

# Bump the schema if the snapshot fields change. Source hashes identify the
# effective Python implementation when the source files ship with the engine.
_SCHEMA_VERSION = 1
_KNOBS = (
    'MODEL', 'LANGUAGE', 'LANGUAGE_FOCUS', 'INITIAL_PROMPT', 'DICTIONARY',
    'DICTIONARY_REPLACEMENTS', 'AUTO_CORRECT', 'AUTO_CAPITALISE',
    'AUTO_TERMINAL_PUNCTUATION', 'STREAMING_TRANSCRIPTION',
    'STYLE_SEED_ENABLED', 'CONDITION_ON_PREVIOUS_TEXT',
)


def _frozen_build_id() -> str | None:
    """Identify a packaged build by its executable, which every build replaces.

    The shipped engine carries no `.py` sources to hash, and without this every
    dictation it records has no code version at all — which is the build people
    actually run. Size and mtime are not a content hash, but they change on
    every build and never within one, and that is all provenance needs here.
    """
    if not getattr(sys, 'frozen', False):
        return None
    try:
        st = os.stat(sys.executable)
    except OSError:
        return None
    return hashlib.sha256(f'frozen:{st.st_size}:{int(st.st_mtime)}'.encode()).hexdigest()


@lru_cache(maxsize=1)
def _code_version() -> str | None:
    root = Path(__file__).parent
    digest = hashlib.sha256()
    for name in ('postprocess.py', 'transcriber.py', 'streaming.py'):
        try:
            digest.update((root / name).read_bytes())
        except OSError:
            return _frozen_build_id()
    return digest.hexdigest()


def snapshot() -> dict[str, Any]:
    """Fingerprint effective settings + code without persisting their values."""
    model = config.MODEL
    info = model_catalog.get(model)
    backend = info.backend if info is not None else None
    version = _code_version()
    knobs = {key: getattr(config, key, None) for key in _KNOBS}
    payload = json.dumps(
        {'schema': _SCHEMA_VERSION, 'settings': knobs, 'backend': backend,
         'code_version': version},
        sort_keys=True, ensure_ascii=False, default=str,
    )
    return {
        'pipeline_hash': hashlib.sha256(payload.encode('utf-8')).hexdigest(),
        'pipeline_version': version,
        'model_backend': backend,
        'language': getattr(config, 'LANGUAGE', None),
    }
