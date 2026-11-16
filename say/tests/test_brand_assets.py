"""Tests for the brand assets and the menu-bar icon built from them.

The Say mark is 5:2 — two and a half times as wide as tall — and almost
every bug in this area is something quietly squaring it off or caching a
stale copy of it. These tests pin the properties that have no other alarm.
"""

from __future__ import annotations

import ast
import os

import brand
import menubar_icon
import pytest
from menubar_icon import IconState

# Taken from brand.py rather than restated as a literal. gen_logo.py owns
# the geometry and brand.py mirrors the one number the runtime needs; what
# these tests check is that the *shipped asset* agrees with that number.
MARK_ASPECT = brand.MARK_ASPECT

# gen_logo.py, at the repo root, is the definition every other copy of the
# geometry is supposed to agree with.
_GEN_LOGO = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    'gen_logo.py',
)


def _generated_mark() -> dict:
    """gen_logo.py's module-level constants, read without importing it.

    Importing would drag in Pillow, which the engine venv has no reason to
    carry — and then these tests would skip on precisely the machine that
    builds the app, which is the same as not having them. Parsing the
    module instead needs nothing but the stdlib.

    `MARK_W` / `MARK_H` are computed (sum/max over MARK_COLUMNS) rather than
    literal, so they do not survive literal_eval; the caller derives them
    from MARK_COLUMNS the same way the module does.
    """
    with open(_GEN_LOGO, encoding='utf-8') as f:
        tree = ast.parse(f.read())

    out: dict = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        name = getattr(node.targets[0], 'id', None)
        if not name:
            continue
        try:
            out[name] = ast.literal_eval(node.value)
        except (ValueError, TypeError):
            pass          # computed, not literal — see the docstring
    return out


# ── Asset discovery ──────────────────────────────────────────────

def test_assets_dir_resolves_to_a_real_directory():
    assert os.path.isdir(brand.ASSETS_DIR)


@pytest.mark.parametrize('name', ['logo.png', 'logo@2x.png', 'menubar-template.pdf'])
def test_shipped_asset_is_present(name):
    assert os.path.exists(os.path.join(brand.ASSETS_DIR, name))


def test_assets_dir_falls_back_to_a_path_rather_than_raising(monkeypatch):
    """A missing bundle must degrade to the drawn glyphs, not crash at import."""
    monkeypatch.setenv('SAY_ASSETS_DIR', '/nonexistent/say/assets')
    assert brand._find_assets_dir().endswith('assets')


# ── The mark must not be squared off ─────────────────────────────

def test_menubar_template_keeps_the_marks_aspect():
    """The one that matters.

    rumps hard-codes every status icon to 20x20 points, and the previous
    version of brand.py did the same at 18x18. Either squashes a 5:2 mark
    into an unreadable smear.
    """
    img = brand.menubar_template()
    assert img is not None, 'menu-bar template asset is missing'
    size = img.size()
    assert size.height == pytest.approx(brand.MENUBAR_HEIGHT_PT)
    assert size.width / size.height == pytest.approx(MARK_ASPECT, abs=0.02)


def test_menubar_asset_is_authored_at_its_display_size():
    """The drift the aspect test above cannot see.

    `gen_logo.MENUBAR_PT` decides the size the PDF is written at;
    `brand.MENUBAR_HEIGHT_PT` decides the size it is displayed at, by
    rescaling on load. Let those two disagree and the glyph still has a
    perfect 5:2 aspect — it is just half or double the intended size, which
    every assertion here would happily pass. Comparing the asset's own
    intrinsic height, before `menubar_template()` touches it, is the only
    thing that catches it.
    """
    from AppKit import NSImage

    path = os.path.join(brand.ASSETS_DIR, 'menubar-template.pdf')
    assert os.path.exists(path), 'menu-bar template asset is missing'
    raw = NSImage.alloc().initWithContentsOfFile_(path)
    assert raw is not None
    assert raw.size().height == pytest.approx(brand.MENUBAR_HEIGHT_PT, abs=0.01), (
        'gen_logo.MENUBAR_PT and brand.MENUBAR_HEIGHT_PT have drifted apart; '
        're-run gen_logo.py or fix the constant'
    )


def test_wizard_placeholder_matches_the_generated_mark():
    """The hand copy that already drifted once.

    `wizard._MarkPlaceholder` redraws the mark in NSBezierPath for the case
    where the asset is missing, so it carries its own copy of the geometry.
    It kept the old 5:1 numbers through a re-proportioning of the mark and
    nothing complained, because the path only runs when the asset is gone —
    exactly the situation nobody tests by hand. Pin it to the generator.
    """
    import wizard

    gen = _generated_mark()
    columns = gen['MARK_COLUMNS']
    mark = wizard._MarkPlaceholder
    # Bound to locals rather than compared attribute-first: ruff reads an
    # UPPER_CASE attribute as a constant and calls `mark.GRID_W == ...` a
    # Yoda condition (SIM300).
    grid_w, grid_h = mark.GRID_W, mark.GRID_H
    aspect, lift = mark.ASPECT, mark.OPTICAL_LIFT

    assert tuple(tuple(c) for c in mark.COLUMNS) == tuple(tuple(c) for c in columns)
    assert grid_w == pytest.approx(sum(w for w, _h in columns))
    assert grid_h == pytest.approx(max(h for _w, h in columns))
    assert aspect == pytest.approx(brand.MARK_ASPECT)
    assert lift == pytest.approx(gen['MARK_OPTICAL_LIFT'])


def test_brand_aspect_matches_the_generated_mark():
    """brand.py mirrors two numbers out of gen_logo.py. Pin both."""
    gen = _generated_mark()
    columns = gen['MARK_COLUMNS']
    aspect = sum(w for w, _h in columns) / max(h for _w, h in columns)
    declared_aspect, declared_height = brand.MARK_ASPECT, brand.MENUBAR_HEIGHT_PT

    assert declared_aspect == pytest.approx(aspect)
    assert declared_height == pytest.approx(gen['MENUBAR_PT'][1])


def test_icon_point_size_is_wide():
    width, height = menubar_icon.icon_point_size()
    assert width > height
    assert width / height == pytest.approx(MARK_ASPECT, abs=0.02)


def test_logo_image_is_square():
    """The tile is square even though the mark inside it is not."""
    logo = brand.logo_image()
    assert logo is not None
    size = logo.size()
    assert size.width == pytest.approx(size.height)


# ── Template flags ───────────────────────────────────────────────

def test_only_idle_is_a_template_image():
    """Colour states must not be templates or the system tints them grey."""
    assert menubar_icon.is_template(IconState.IDLE)
    for state in (IconState.RECORDING, IconState.PROCESSING, IconState.ERROR):
        assert not menubar_icon.is_template(state)


def test_every_non_idle_state_has_a_colour():
    """A state with no colour would render as black and vanish in dark mode."""
    for state in IconState:
        if state is IconState.IDLE:
            assert state not in menubar_icon._STATE_COLOR
        else:
            assert state in menubar_icon._STATE_COLOR


def test_state_colours_are_mid_tone():
    """They sit on both a near-white and a near-black menu bar."""
    for state, rgb in menubar_icon._STATE_COLOR.items():
        luma = 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]
        assert 0.25 < luma < 0.80, f'{state.value} luma {luma:.2f} fails on one bar'


# ── Cache invalidation ───────────────────────────────────────────

def test_fingerprint_changes_when_the_asset_changes(tmp_path, monkeypatch):
    """Cached PNGs live in ~/Library/Caches forever.

    Without a fingerprint in the filename, a user who updates the app keeps
    the previous release's menu-bar glyph: the cache hits on `idle.png` and
    the new mark never renders.
    """
    asset = tmp_path / 'menubar-template.pdf'
    asset.write_bytes(b'first')
    monkeypatch.setattr(brand, 'ASSETS_DIR', str(tmp_path))

    monkeypatch.setattr(menubar_icon, '_fingerprint', None)
    first = menubar_icon._asset_fingerprint()

    asset.write_bytes(b'second')
    monkeypatch.setattr(menubar_icon, '_fingerprint', None)
    second = menubar_icon._asset_fingerprint()

    assert first != second


def test_fingerprint_is_stable_for_unchanged_assets(monkeypatch):
    monkeypatch.setattr(menubar_icon, '_fingerprint', None)
    first = menubar_icon._asset_fingerprint()
    monkeypatch.setattr(menubar_icon, '_fingerprint', None)
    assert menubar_icon._asset_fingerprint() == first


def test_cache_path_carries_the_fingerprint(tmp_path, monkeypatch):
    monkeypatch.setattr(menubar_icon, 'CACHE_DIR', str(tmp_path))
    path = menubar_icon.icon_path_for(IconState.IDLE)
    assert path is not None
    assert menubar_icon._asset_fingerprint() in os.path.basename(path)


# ── Rendering ────────────────────────────────────────────────────

def test_rendered_png_is_high_resolution(tmp_path, monkeypatch):
    """A 1x render of a 14 pt glyph is a blur once the menu bar scales it."""
    monkeypatch.setattr(menubar_icon, 'CACHE_DIR', str(tmp_path))
    path = menubar_icon.icon_path_for(IconState.IDLE)
    assert path is not None

    from AppKit import NSImage

    rep = NSImage.alloc().initWithContentsOfFile_(path).representations()[0]
    width, _height = menubar_icon.icon_point_size()
    assert rep.pixelsWide() >= width * menubar_icon.RENDER_SCALE - 1


def test_recording_and_idle_render_differently(tmp_path, monkeypatch):
    monkeypatch.setattr(menubar_icon, 'CACHE_DIR', str(tmp_path))
    idle = menubar_icon.icon_path_for(IconState.IDLE)
    recording = menubar_icon.icon_path_for(IconState.RECORDING)
    assert idle and recording
    with open(idle, 'rb') as f:
        idle_bytes = f.read()
    with open(recording, 'rb') as f:
        recording_bytes = f.read()
    assert idle_bytes != recording_bytes


def test_stale_cache_entries_are_pruned(tmp_path, monkeypatch):
    monkeypatch.setattr(menubar_icon, 'CACHE_DIR', str(tmp_path))
    stale = tmp_path / 'idle-deadbeef00.png'
    stale.write_bytes(b'old')

    assert menubar_icon.icon_path_for(IconState.IDLE) is not None
    assert not stale.exists()
