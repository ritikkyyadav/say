"""Brand tokens for Say.

Single source of truth for colors, typography, spacing, and asset paths.
The bear-with-microphone logo (light blue, brown, pink-cheek) sets the
palette. Surface rules:

- Overlay (during dictation): stays dark and out-of-the-way, with a
  pink accent that ties to the bear's cheeks / mic.
- Menu bar: monochrome template glyph that adapts to light/dark menu bars.
- Wizard / Settings windows: light sky background, brown text, pink CTA.

All values exposed as 0-1 RGB tuples and resolved to NSColor on demand,
so callers can use `brand.color(brand.SKY_200)` without thinking about
the AppKit init shape.
"""

from __future__ import annotations

import os

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

ASSETS_DIR = os.path.join(os.path.dirname(__file__), 'assets')

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

# ── Neutrals ─────────────────────────────────────────────────────
INK   = (0.063, 0.071, 0.094)  # #101218  body text
SLATE = (0.290, 0.310, 0.376)  # #4A4F60  secondary text
MIST  = (0.973, 0.980, 0.992)  # #F8FAFD  card surface
CREAM = (1.000, 0.973, 0.949)  # #FFF8F2  warm card surface
LINE  = (0.851, 0.890, 0.937)  # #D9E3EF  hairlines

# ── Overlay (stays dark per design choice) ───────────────────────
OVERLAY_BG     = (0.075, 0.082, 0.106)  # #13151B
OVERLAY_BORDER = (0.231, 0.255, 0.310)  # #3B414F
OVERLAY_TEXT   = (0.831, 0.875, 0.937)  # #D4DFEF
OVERLAY_PINK   = PINK_500
OVERLAY_RED    = (0.851, 0.310, 0.353)  # cancel
OVERLAY_GREEN  = (0.298, 0.749, 0.439)  # finish (kept readable, not changed to pink)


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
    """Load the full-color bear logo. Returns None if no asset is present.

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


def menubar_template() -> NSImage | None:
    """Load a monochrome template image for the menu bar. None if absent."""
    for name in ('menubar-template.pdf', 'menubar.pdf', 'menubar-template.png'):
        path = os.path.join(ASSETS_DIR, name)
        if os.path.exists(path):
            img = NSImage.alloc().initWithContentsOfFile_(path)
            if img is not None:
                img.setTemplate_(True)
                img.setSize_(NSMakeSize(18, 18))
                return img
    return None
