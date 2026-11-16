"""Tests for how the overlay keeps itself legible against any backdrop.

The system appearance is a poor proxy for what is actually behind the glyph —
macOS in dark mode over a white web page puts near-white ink on white — so the
mark is drawn twice, with a rim in the opposite tone. These pin the decision
table; the drawing itself needs a window server and is not tested here.
"""

from __future__ import annotations

import config
import overlay
import pytest
import validation
from overlay import INK_DARK, INK_LIGHT, contrast_scheme


def ink_of(mode: str, system_is_dark: bool):
    return contrast_scheme(mode, system_is_dark)[0]


def rim_of(mode: str, system_is_dark: bool):
    return contrast_scheme(mode, system_is_dark)[1]


def rimmed(mode: str, system_is_dark: bool) -> bool:
    return contrast_scheme(mode, system_is_dark)[2]


# ── The invariant that makes the technique work ──────────────────

@pytest.mark.parametrize('mode', ['auto', 'system', 'dark', 'light', 'nonsense'])
@pytest.mark.parametrize('system_is_dark', [False, True])
def test_ink_and_rim_are_always_opposites(mode, system_is_dark):
    """One of the two tones always has contrast. That is the whole idea."""
    ink, rim, _ = contrast_scheme(mode, system_is_dark)
    assert ink != rim
    assert {ink, rim} == {INK_LIGHT, INK_DARK}


# ── Mode semantics ───────────────────────────────────────────────

def test_auto_follows_the_system_appearance():
    assert ink_of('auto', system_is_dark=False) == INK_LIGHT
    assert ink_of('auto', system_is_dark=True) == INK_DARK


def test_system_mode_follows_the_appearance_too_but_drops_the_rim():
    """'system' is the pre-rim behaviour, kept for anyone who preferred it."""
    assert ink_of('system', system_is_dark=False) == INK_LIGHT
    assert ink_of('system', system_is_dark=True) == INK_DARK
    assert not rimmed('system', False)
    assert not rimmed('system', True)


@pytest.mark.parametrize('system_is_dark', [False, True])
def test_dark_and_light_name_the_ink_not_the_theme(system_is_dark):
    """Someone choosing 'dark' wants a dark mark.

    That is the opposite of what dark *mode* would hand them, so these must
    ignore the system appearance entirely.
    """
    assert ink_of('dark', system_is_dark) == INK_LIGHT   # INK_LIGHT is the dark ink
    assert ink_of('light', system_is_dark) == INK_DARK   # INK_DARK is the pale ink


@pytest.mark.parametrize('mode', ['auto', 'dark', 'light'])
@pytest.mark.parametrize('system_is_dark', [False, True])
def test_every_mode_but_system_draws_the_rim(mode, system_is_dark):
    assert rimmed(mode, system_is_dark)


def test_an_unknown_mode_falls_back_to_auto():
    """A hand-edited config must not leave the glyph unreadable."""
    for system_is_dark in (False, True):
        assert contrast_scheme('wat', system_is_dark) == contrast_scheme(
            'auto', system_is_dark
        )


@pytest.mark.parametrize('mode', ['AUTO', 'System', 'DaRk'])
def test_mode_is_case_insensitive(mode):
    assert contrast_scheme(mode, False) == contrast_scheme(mode.lower(), False)


def test_empty_mode_does_not_crash():
    assert contrast_scheme('', False) == contrast_scheme('auto', False)


# ── Wiring ───────────────────────────────────────────────────────

def test_rim_is_the_shipped_default():
    assert config.OVERLAY_CONTRAST == 'auto'
    assert rimmed(config.OVERLAY_CONTRAST, False)


def test_every_valid_mode_is_accepted_by_the_scheme():
    for mode in config.VALID_OVERLAY_CONTRAST:
        ink, rim, _ = contrast_scheme(mode, False)
        assert {ink, rim} == {INK_LIGHT, INK_DARK}


def test_validation_rejects_an_unknown_mode(monkeypatch):
    monkeypatch.setattr(config, 'OVERLAY_CONTRAST', 'chartreuse')
    errors = validation.validate_config()
    assert any('OVERLAY_CONTRAST' in e for e in errors)


def test_validation_accepts_the_shipped_default():
    assert not [e for e in validation.validate_config() if 'OVERLAY_CONTRAST' in e]


def test_rim_width_is_positive_and_subtle():
    """Wide enough to see at 19 pt, narrow enough not to become an outline."""
    assert 0.5 <= overlay.RIM_PT <= 2.0
