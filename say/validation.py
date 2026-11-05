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
    valid_models: set[str] = getattr(cfg, 'VALID_MODELS', set())
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
    valid_inject_methods: set[str] = getattr(cfg, 'VALID_INJECT_METHODS', set())
    if inject_method not in valid_inject_methods:
        errors.append(
            f'INJECT_METHOD must be one of: {", ".join(sorted(valid_inject_methods))}.'
        )

    overlay_contrast = getattr(cfg, 'OVERLAY_CONTRAST', 'auto')
    valid_contrast: set[str] = getattr(cfg, 'VALID_OVERLAY_CONTRAST', set())
    if valid_contrast and overlay_contrast not in valid_contrast:
        errors.append(
            f'OVERLAY_CONTRAST must be one of: {", ".join(sorted(valid_contrast))}.'
        )

    theme = getattr(cfg, 'OVERLAY_THEME', 'ink')
    try:
        import overlay_theme
    except Exception:
        overlay_theme = None  # type: ignore[assignment]
    if overlay_theme is not None:
        if theme not in overlay_theme.VALID_THEME_IDS:
            errors.append(
                'OVERLAY_THEME must be one of: '
                f'{", ".join(sorted(overlay_theme.VALID_THEME_IDS))}.'
            )
        pattern = getattr(cfg, 'OVERLAY_PATTERN', overlay_theme.DEFAULT_PATTERN)
        if pattern not in overlay_theme.VALID_PATTERNS:
            errors.append(
                'OVERLAY_PATTERN must be one of: '
                f'{", ".join(sorted(overlay_theme.VALID_PATTERNS))}.'
            )

        custom = getattr(cfg, 'OVERLAY_CUSTOM_COLORS', []) or []
        if not isinstance(custom, list):
            errors.append('OVERLAY_CUSTOM_COLORS must be a list of hex colours.')
        else:
            for value in custom:
                try:
                    overlay_theme.parse_hex(str(value))
                except overlay_theme.BadColor:
                    errors.append(
                        f'OVERLAY_CUSTOM_COLORS contains {value!r}, which is not '
                        'a hex colour like #D9713F.'
                    )
                    break
            if theme == overlay_theme.CUSTOM_ID and not custom:
                errors.append(
                    "OVERLAY_THEME is 'custom' but OVERLAY_CUSTOM_COLORS is empty."
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

    save_recordings = getattr(cfg, 'SAVE_RECORDINGS', None)
    if not isinstance(save_recordings, bool):
        errors.append('SAVE_RECORDINGS must be True or False.')

    retention_mode = getattr(cfg, 'RECORDINGS_RETENTION_MODE', None)
    valid_modes: set[str] = getattr(cfg, 'VALID_RETENTION_MODES', set())
    if retention_mode not in valid_modes:
        errors.append(
            f'RECORDINGS_RETENTION_MODE must be one of: {", ".join(sorted(valid_modes))}.'
        )

    retention_days = getattr(cfg, 'RECORDINGS_RETENTION_DAYS', None)
    if not isinstance(retention_days, int) or retention_days <= 0:
        errors.append('RECORDINGS_RETENTION_DAYS must be a positive integer.')

    max_bytes = getattr(cfg, 'RECORDINGS_MAX_BYTES', None)
    if not isinstance(max_bytes, int) or max_bytes <= 0:
        errors.append('RECORDINGS_MAX_BYTES must be a positive integer.')

    return errors


def validate_or_raise(cfg: ModuleType = config) -> None:
    errors = validate_config(cfg)
    if errors:
        raise ConfigError('\n'.join(errors))
