# validation.py - Startup configuration checks.

from __future__ import annotations

from types import ModuleType

import config
from hotkey import parse_hotkey


class ConfigError(ValueError):
    """Raised when Say configuration is invalid."""


def validate_config(cfg: ModuleType = config) -> list[str]:
    """Return a list of human-readable configuration errors."""
    errors: list[str] = []

    model = getattr(cfg, 'MODEL', None)
    valid_models = getattr(cfg, 'VALID_MODELS', set())
    if not isinstance(model, str) or not model.strip():
        errors.append('MODEL must be a non-empty string.')
    elif valid_models and model not in valid_models:
        errors.append(f'MODEL is not in VALID_MODELS: {model}')

    hotkey = getattr(cfg, 'HOTKEY', '')
    if hotkey != 'fn':
        try:
            parse_hotkey(hotkey)
        except ValueError as exc:
            errors.append(str(exc))

    inject_method = getattr(cfg, 'INJECT_METHOD', None)
    valid_inject_methods = getattr(cfg, 'VALID_INJECT_METHODS', set())
    if inject_method not in valid_inject_methods:
        errors.append(
            f'INJECT_METHOD must be one of: {", ".join(sorted(valid_inject_methods))}.'
        )

    sample_rate = getattr(cfg, 'SAMPLE_RATE', None)
    if not isinstance(sample_rate, int) or sample_rate <= 0:
        errors.append('SAMPLE_RATE must be a positive integer.')

    max_recording = getattr(cfg, 'MAX_RECORDING_SECONDS', None)
    if not isinstance(max_recording, (int, float)) or max_recording <= 0:
        errors.append('MAX_RECORDING_SECONDS must be a positive number.')

    min_audio = getattr(cfg, 'MIN_AUDIO_SECONDS', None)
    if not isinstance(min_audio, (int, float)) or min_audio < 0:
        errors.append('MIN_AUDIO_SECONDS must be zero or greater.')
    elif isinstance(max_recording, (int, float)) and min_audio >= max_recording:
        errors.append('MIN_AUDIO_SECONDS must be less than MAX_RECORDING_SECONDS.')

    language = getattr(cfg, 'LANGUAGE', None)
    if language is not None and (
        not isinstance(language, str) or not language.strip()
    ):
        errors.append('LANGUAGE must be a language code string or None.')

    notifications = getattr(cfg, 'SHOW_NOTIFICATIONS', None)
    if not isinstance(notifications, bool):
        errors.append('SHOW_NOTIFICATIONS must be True or False.')

    return errors


def validate_or_raise(cfg: ModuleType = config) -> None:
    errors = validate_config(cfg)
    if errors:
        raise ConfigError('\n'.join(errors))
