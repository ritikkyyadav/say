"""Brand tokens for Say.

Single source of truth for colors, typography, spacing, and asset paths.

The mark is a frozen frame of the dictation overlay: a continuous base with
six stepped columns standing on it, ink on a cream squircle. It is 5:2 —
two and a half times as wide as it is tall — which is the one thing every
consumer of it has to account for. Surface rules:

- Menu bar: monochrome template glyph that adapts to light/dark menu bars.
- Wizard / Settings windows: light sky background, brown text, pink CTA.

All values exposed as 0-1 RGB tuples and resolved to NSColor on demand,
so callers can use `brand.color(brand.SKY_200)` without thinking about
the AppKit init shape.
"""

from __future__ import annotations

import os
import sys

from AppKit import (
    NSColor,
    NSFont,
    NSFontWeightBold,
    NSFontWeightMedium,
    NSFontWeightRegular,
    NSFontWeightSemibold,
    NSImage,
    NSMakeSize,
)
from logging_setup import get_logger

_log = get_logger('brand')

def _find_assets_dir() -> str:
    """Locate the bundled assets directory.

    Mirrors the lookup in transcriber.py / model_catalog.py: a dev run
    finds it beside this file, a PyInstaller onedir build puts it under
    sys._MEIPASS, and the .app layout can leave it in Contents/Resources.
    Without this the packaged app silently falls back to the runtime-drawn
    glyphs and never shows the real mark.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = []

    env_dir = os.environ.get('SAY_ASSETS_DIR')
    if env_dir:
        candidates.append(env_dir)

    meipass = getattr(sys, '_MEIPASS', None)
    if meipass:
        candidates.append(os.path.join(meipass, 'assets'))

    candidates.append(os.path.join(here, 'assets'))

    cursor = here
    for _ in range(6):
        candidates.append(os.path.join(cursor, 'Resources', 'assets'))
        parent = os.path.dirname(cursor)
        if parent == cursor:
            break
        cursor = parent

    for path in candidates:
        if os.path.isdir(path):
            return path
    return os.path.join(here, 'assets')


ASSETS_DIR = _find_assets_dir()

# ── Sky (background / surface) ───────────────────────────────────
SKY_50  = (0.953, 0.973, 0.992)  # #F3F8FD  faintest wash
SKY_100 = (0.890, 0.937, 0.973)  # #E3EFF8
SKY_200 = (0.800, 0.890, 0.961)  # #CCE3F5  logo background
SKY_300 = (0.682, 0.824, 0.937)  # #AED2EF
SKY_500 = (0.388, 0.651, 0.871)  # #6385DE  ish
SKY_700 = (0.227, 0.475, 0.749)  # #3A79BF

# ── Bear (primary) ───────────────────────────────────────────────
BROWN_300 = (0.745, 0.553, 0.412)  # #BE8D69
BROWN_500 = (0.557, 0.353, 0.227)  # #8E5A3A  bear body
BROWN_700 = (0.431, 0.259, 0.149)  # #6E4226
BROWN_900 = (0.235, 0.137, 0.078)  # #3C2314

# ── Pink (accent / CTA) ──────────────────────────────────────────
PINK_300 = (0.973, 0.831, 0.851)  # #F8D4D9
PINK_500 = (0.961, 0.714, 0.737)  # #F5B6BC  cheeks
PINK_600 = (0.937, 0.557, 0.604)  # #EF8E9A
PINK_700 = (0.831, 0.345, 0.412)  # #D45869

# ── The mark ─────────────────────────────────────────────────────
# The logo's own colour, and the source of truth for anything that draws
# the mark at runtime. The one surface it is deliberately absent from is
# the menu bar: that glyph ships as a *template* image so macOS can tint
# it to match the bar, and a coloured template would have to give that up.
# `gen_logo.MARK` is the same colour on the asset side.
MARK = (0.737, 0.173, 0.380)   # #BC2C61  raspberry — the Say mark

# ── Status (must read on both a light and a dark menu bar, so these
#    are all mid-tone: full-strength ink or white would fail on one) ──
CRAIL  = (0.851, 0.467, 0.341)  # #D97757  recording
AZURE  = (0.416, 0.608, 0.800)  # #6A9BCC  processing
SIGNAL = (0.851, 0.310, 0.353)  # #D94F5A  error

# ── Neutrals ─────────────────────────────────────────────────────
INK   = (0.063, 0.071, 0.094)  # #101218  body text
SLATE = (0.290, 0.310, 0.376)  # #4A4F60  secondary text
MIST  = (0.973, 0.980, 0.992)  # #F8FAFD  card surface
CREAM = (1.000, 0.973, 0.949)  # #FFF8F2  warm card surface
LINE  = (0.851, 0.890, 0.937)  # #D9E3EF  hairlines



# ── NSColor helper ───────────────────────────────────────────────

def color(rgb: tuple[float, float, float], alpha: float = 1.0):
    r, g, b = rgb
    return NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, alpha)


# ── Typography ───────────────────────────────────────────────────

_WEIGHTS = {
    'regular': NSFontWeightRegular,
    'medium': NSFontWeightMedium,
    'semibold': NSFontWeightSemibold,
    'bold': NSFontWeightBold,
}


def font(size: float, weight: str = 'regular') -> NSFont:
    return NSFont.systemFontOfSize_weight_(size, _WEIGHTS[weight])


def mono(size: float) -> NSFont:
    f = NSFont.monospacedSystemFontOfSize_weight_(size, NSFontWeightRegular)
    return f or NSFont.userFixedPitchFontOfSize_(size)


# ── Asset loading ────────────────────────────────────────────────

def logo_image(size: int | None = None) -> NSImage | None:
    """Load the full-color app tile. Returns None if no asset is present.

    Looks for, in order:
        assets/logo@2x.png
        assets/logo.png
        assets/logo.jpg
        assets/logo.jpeg
    """
    for name in ('logo@2x.png', 'logo.png', 'logo.jpg', 'logo.jpeg'):
        path = os.path.join(ASSETS_DIR, name)
        if os.path.exists(path):
            img = NSImage.alloc().initWithContentsOfFile_(path)
            if img is not None:
                if size is not None:
                    img.setSize_(NSMakeSize(size, size))
                return img
    _log.debug('No logo asset found under %s', ASSETS_DIR)
    return None


# The mark's aspect, width : height. gen_logo.py owns the geometry; this is
# the one number the runtime needs from it, and test_brand_assets.py pins it
# against the asset that actually shipped.
MARK_ASPECT = 2.5

# Display height of the menu-bar glyph, in points. Must equal the height in
# gen_logo.MENUBAR_PT. If the two drift the asset is silently rescaled on
# load, so the glyph comes out the wrong size at the right aspect — which no
# aspect assertion can see.
MENUBAR_HEIGHT_PT = 14.0


def menubar_template() -> NSImage | None:
    """Load a monochrome template image for the menu bar. None if absent.

    Scaled by *height*, not squared off at 18x18 like an ordinary status
    glyph: the mark is 5:2, so forcing it square would squash it to a
    smear. Unlike the old 5:1 mark — which would have needed a 90 pt wide
    menu-bar item to reach a normal glyph height, and so ran at half of one
    instead — 5:2 affords the full height for 35 pt of width.
    """
    for name in ('menubar-template.pdf', 'menubar.pdf', 'menubar-template.png'):
        path = os.path.join(ASSETS_DIR, name)
        if os.path.exists(path):
            img = NSImage.alloc().initWithContentsOfFile_(path)
            if img is not None:
                size = img.size()
                if size.height > 0:
                    scale = MENUBAR_HEIGHT_PT / size.height
                    img.setSize_(NSMakeSize(size.width * scale, MENUBAR_HEIGHT_PT))
                img.setTemplate_(True)
                return img
    return None
