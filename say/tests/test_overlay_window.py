"""Tests for the overlay's *window*, as opposed to its drawing.

`test_overlay_contrast.py` deliberately stops at the decision table because
"the drawing itself needs a window server". That gap is exactly where the
worst overlay bug so far lived: the panel asked for `NSStatusWindowLevel` and
silently got `NSFloatingWindowLevel`, because `setFloatingPanel_` assigns the
level as a side effect and was being called *after* `setLevel_`. Nothing pure
could have caught it — the decision table was right, the window was wrong.

So these build a real panel and interrogate it. They need a window server, and
skip cleanly where there isn't one rather than failing CI.
"""
from __future__ import annotations

import os

import pytest

pytest.importorskip('AppKit')

import overlay as overlay_mod  # noqa: E402
from AppKit import (  # noqa: E402
    NSApplication,
    NSApplicationActivationPolicyAccessory,
    NSStatusWindowLevel,
)


@pytest.fixture(scope='module')
def panel():
    """A real, built overlay panel — or a skip if this box has no window server."""
    if os.environ.get('SAY_CI'):
        pytest.skip('needs a window server')
    try:
        app = NSApplication.sharedApplication()
        app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
        ov = overlay_mod.make_overlay()
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f'window server unavailable: {exc}')
    if ov is None or getattr(ov, '_panel', None) is None:
        pytest.skip('overlay could not be built here')
    try:
        yield ov._panel
    finally:
        try:
            ov.close()
        except Exception:
            pass


def test_the_glyph_sits_at_status_level(panel):
    """Regression: it spent its life at NSFloatingWindowLevel (3), not 25.

    Floating level clears ordinary windows, so the glyph looked fine in casual
    use and was buried precisely where dictation happens — under full-screen
    apps and other apps' floating panels. `setFloatingPanel_` assigns the level
    itself, so any call to it after `setLevel_` silently wins.
    """
    assert panel.level() == NSStatusWindowLevel, (
        f'overlay window level is {panel.level()}, expected {NSStatusWindowLevel}; '
        'something is assigning the level after setLevel_ (setFloatingPanel_ does)'
    )


def test_the_glyph_can_never_take_focus(panel):
    """Focus is where the transcript is about to be typed.

    A feedback window that can steal it turns feedback into a bug, so this is
    an invariant of the panel, not a preference.
    """
    assert panel.canBecomeKeyWindow() is False
    assert panel.canBecomeMainWindow() is False
    assert panel.ignoresMouseEvents() is True


def test_the_glyph_survives_the_app_losing_focus(panel):
    """It is on screen precisely while another app is frontmost."""
    assert panel.hidesOnDeactivate() is False


# ── Themed painting ─────────────────────────────────────────────────────────
# The multi-colour path builds and swaps real CALayers, which is exactly the
# kind of code the pure contrast tests cannot reach. These are the only tests
# that would notice `_paint` failing to build or the wrong layer being shown.


@pytest.fixture()
def glyph():
    """A freshly built overlay whose config is restored afterwards."""
    if os.environ.get('SAY_CI'):
        pytest.skip('needs a window server')
    import config

    try:
        app = NSApplication.sharedApplication()
        app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
        ov = overlay_mod.DictationOverlay()
        ov._build()
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f'window server unavailable: {exc}')
    if getattr(ov, '_panel', None) is None:
        pytest.skip('overlay could not be built here')

    saved = (
        config.OVERLAY_THEME,
        list(config.OVERLAY_CUSTOM_COLORS),
        getattr(config, 'OVERLAY_PATTERN', 'pulse'),
    )
    try:
        yield ov
    finally:
        config.OVERLAY_THEME = saved[0]
        config.OVERLAY_CUSTOM_COLORS = saved[1]
        config.OVERLAY_PATTERN = saved[2]
        try:
            ov._teardown()
        except Exception:
            pass


def test_there_is_one_column_layer_per_band(glyph):
    assert len(glyph._columns) == glyph._geometry.bands


def test_the_default_theme_paints_nothing(glyph):
    """`ink` must keep using the single-colour shape layer.

    This is the compatibility guarantee: an existing user upgrading into a
    build that has themes sees precisely the glyph they had before.
    """
    import config

    config.OVERLAY_THEME = 'ink'
    config.OVERLAY_CUSTOM_COLORS = []
    glyph._apply_appearance()

    assert glyph._themed is False
    assert glyph._paint.isHidden() is True
    assert glyph._shape.isHidden() is False


def test_a_colour_theme_swaps_which_layer_is_visible(glyph):
    import config

    config.OVERLAY_THEME = 'signal'
    glyph._apply_appearance()

    assert glyph._themed is True
    assert glyph._paint.isHidden() is False
    # Exactly one of the two is ever drawn, or the colours would sit under a
    # solid fill and nothing would appear to have changed.
    assert glyph._shape.isHidden() is True


def _field(glyph):
    """The colour of every column as a comparable string.

    Compare with CGColorEqualToColor, not `str()`: a CGColor's description
    carries its pointer, so two identical colours stringify differently.
    (CGColorGetComponents is not usable — it hands back an unbounded C
    pointer and bus-errors through the bridge.)
    """
    from Quartz import CGColorEqualToColor

    out = []
    for layer in glyph._columns:
        c = layer.backgroundColor()
        idx = next(
            (i for i, p in enumerate(glyph._palette) if CGColorEqualToColor(c, p)),
            -1,
        )
        out.append(str(idx))
    return ''.join(out)


def test_the_stripe_pattern_still_draws_a_stripe(glyph):
    """`alternate` is kept so the old look is still reachable."""
    import config

    config.OVERLAY_THEME = 'crimson'
    config.OVERLAY_PATTERN = 'alternate'
    glyph._apply_appearance()
    glyph._apply([0.5] * glyph._geometry.bands, 1.0)

    assert _field(glyph) == '01' * (glyph._geometry.bands // 2)


def test_the_stripe_is_the_same_in_every_frame(glyph):
    """Which is the whole problem with it, and why it is not the default."""
    import config

    config.OVERLAY_THEME = 'crimson'
    config.OVERLAY_PATTERN = 'alternate'
    glyph._apply_appearance()

    glyph._apply([0.05] * glyph._geometry.bands, 1.0)
    quiet = _field(glyph)
    glyph._apply([0.95] * glyph._geometry.bands, 1.0)
    loud = _field(glyph)
    assert quiet == loud


def test_pulse_recolours_as_the_audio_changes(glyph):
    """The point of the whole pattern layer.

    Colour comes from the spectrum, so a different spectrum must repaint the
    row — on the real layers, not just in the pure function.
    """
    import math

    import config

    config.OVERLAY_THEME = 'crimson'
    config.OVERLAY_PATTERN = 'pulse'
    glyph._apply_appearance()

    seen = set()
    for k in range(6):
        heights = [abs(math.sin(k * 0.9 + i * 0.7)) for i in range(glyph._geometry.bands)]
        glyph._apply(heights, 1.0)
        seen.add(_field(glyph))

    assert len(seen) > 3, f'colour field barely moved: {seen}'


def test_every_column_always_holds_a_palette_colour(glyph):
    """No column may end up with a colour that is not in the theme."""
    import config

    config.OVERLAY_THEME = 'bloom'
    config.OVERLAY_PATTERN = 'pulse'
    glyph._apply_appearance()
    glyph._apply([0.3, 0.9] * (glyph._geometry.bands // 2), 1.0)

    assert '-1' not in _field(glyph)


def test_a_themed_glyph_is_always_rimmed(glyph):
    """A two-tone mark has a colour that vanishes against *some* backdrop, so
    the rim is not optional here."""
    import config

    config.OVERLAY_THEME = 'mono'
    glyph._apply_appearance()

    assert glyph._rim.isHidden() is False
    assert glyph._rim.lineWidth() > 0


def test_switching_back_to_ink_restores_the_plain_glyph(glyph):
    import config

    config.OVERLAY_THEME = 'ember'
    glyph._apply_appearance()
    assert glyph._themed is True

    config.OVERLAY_THEME = 'ink'
    glyph._apply_appearance()
    assert glyph._themed is False
    assert glyph._paint.isHidden() is True
    assert glyph._shape.isHidden() is False


def test_a_frame_sets_the_mask_path_too(glyph):
    """The mask carries the *same* silhouette the shape does — that is what
    keeps a multi-colour glyph one path instead of twelve rectangles."""
    import config

    config.OVERLAY_THEME = 'bloom'
    glyph._apply_appearance()
    glyph._apply(glyph._motion.heights, 0.5)

    assert glyph._mask.path() is not None
    assert glyph._shape.path() is not None


def test_a_corrupt_custom_theme_falls_back_rather_than_raising(glyph):
    """A bad settings file must never cost someone their glyph."""
    import config

    config.OVERLAY_THEME = 'custom'
    config.OVERLAY_CUSTOM_COLORS = ['not-a-colour']
    glyph._apply_appearance()          # must not raise

    assert glyph._themed is False
    assert glyph._shape.isHidden() is False


# ── Self-healing frame clock ─────────────────────────────────────────────────
# AppKit-vended display links suspend the moment their window leaves every
# display (measured: zero frames off-screen or ordered out, resuming on return).
# A glyph caught mid-fade by that stays flagged visible forever, and every later
# show is skipped because the overlay believes the mark is already up. These
# pin the recovery: a visible glyph whose clock has gone quiet is rebuilt.


def _show_listening(glyph):
    from waveform import BarMotion

    glyph._show(BarMotion.LISTEN, True)


def test_a_glyph_whose_clock_stalled_is_rebuilt_on_the_next_show(glyph):
    import time

    _show_listening(glyph)
    assert glyph._visible is True
    first = glyph._link
    assert first is not None

    # No frame for longer than the stale threshold: the display went away.
    glyph._last_frame_at = time.perf_counter() - (overlay_mod.CLOCK_STALE_S + 1.0)
    _show_listening(glyph)

    assert glyph._visible is True
    assert glyph._link is not None and glyph._link is not first
    assert glyph._panel.isVisible() is True


def test_hiding_a_stalled_glyph_finishes_immediately(glyph):
    """A fade needs frames; with none coming, waiting would never end."""
    import time

    _show_listening(glyph)
    glyph._last_frame_at = time.perf_counter() - (overlay_mod.CLOCK_STALE_S + 1.0)

    glyph._hide()

    assert glyph._visible is False
    assert glyph._link is None and glyph._timer is None
    assert glyph._panel.isVisible() is False


def test_a_live_clock_is_left_alone(glyph):
    """The guard must never fire on a healthy glyph: rebuilding one that is
    animating would reset its motion mid-dictation."""
    import time

    _show_listening(glyph)
    first = glyph._link
    glyph._last_frame_at = time.perf_counter()

    _show_listening(glyph)

    assert glyph._link is first
    assert glyph._visible is True


def test_recovery_keeps_the_recording_flag(glyph):
    """`listen()` sets `_recording` just before it shows; clearing it during a
    recovery would let a stale disarm hide a live recording's mark."""
    import time

    _show_listening(glyph)
    glyph._mark_recording(True)
    glyph._last_frame_at = time.perf_counter() - (overlay_mod.CLOCK_STALE_S + 1.0)

    from waveform import BarMotion

    glyph._show(BarMotion.LISTEN, False)

    assert glyph._recording is True
    assert glyph._visible is True
