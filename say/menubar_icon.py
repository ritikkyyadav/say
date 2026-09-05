"""Menu-bar icon generation for Say.

The macOS menu bar wants a small monochrome *template* image that the
system tints automatically for light vs. dark menu bars. We support two
sources, in order:

1. `assets/menubar-template.pdf` (or `.png`) — the Say mark, written by
   `gen_logo.py`. This is the path that ships. It is a real vector PDF:
   at 14 pt tall in a 22 pt bar there is little resampling budget to spare.
2. A microphone glyph drawn in NSBezierPath at runtime, so the app still
   looks reasonable if the asset is missing.

Three things here are not the obvious choice, and all three follow from
the mark being 5:2 rather than square — see `icon_point_size`,
`_STATE_COLOR`, and `_render_png`.
"""

from __future__ import annotations

import hashlib
import os
from enum import Enum

import brand
from AppKit import (
    NSBezierPath,
    NSBitmapImageFileTypePNG,
    NSBitmapImageRep,
    NSColor,
    NSCompositingOperationSourceAtop,
    NSCompositingOperationSourceOver,
    NSDeviceRGBColorSpace,
    NSGraphicsContext,
    NSImage,
    NSMakeRect,
    NSMakeSize,
    NSRectFillUsingOperation,
)
from logging_setup import get_logger

_log = get_logger('menubar_icon')

ICON_PT = 18      # the square fallback glyph only
RENDER_SCALE = 3  # cache PNGs at @3x so retina menu bars stay crisp


class IconState(str, Enum):
    IDLE = 'idle'
    RECORDING = 'recording'
    PROCESSING = 'processing'
    ERROR = 'error'


# Non-idle states recolour the whole mark instead of adding a badge dot in
# the corner. A badged image cannot be a template image — the system would
# tint the dot grey along with everything else — so the mark would be drawn
# as literal black and vanish against a dark menu bar. Recolouring the mark
# reads in both appearances and needs no extra width. (At the mark's old
# 7 pt height a badge dot was too small to see anyway; at 14 pt it would be
# legible — but the template-tinting problem rules it out either way.)
_STATE_COLOR = {
    IconState.RECORDING: brand.CRAIL,
    IconState.PROCESSING: brand.AZURE,
    IconState.ERROR: brand.SIGNAL,
}

CACHE_DIR = os.path.expanduser('~/Library/Caches/Say/menubar')

_ASSET_NAMES = ('menubar-template.pdf', 'menubar.pdf', 'menubar-template.png')


# ── Public API ───────────────────────────────────────────────────

def icon_for(state: IconState) -> NSImage:
    """Return an NSImage for `state`, sized in points.

    Always returns a fresh image — callers should cache if needed.
    """
    base = _base_image()
    color = _STATE_COLOR.get(state)
    if color is None:
        return base

    size = base.size()
    rect = NSMakeRect(0, 0, size.width, size.height)
    out = NSImage.alloc().initWithSize_(size)
    out.lockFocus()
    try:
        base.drawAtPoint_fromRect_operation_fraction_(
            (0, 0), rect, NSCompositingOperationSourceOver, 1.0,
        )
        brand.color(color).setFill()
        NSRectFillUsingOperation(rect, NSCompositingOperationSourceAtop)
    finally:
        out.unlockFocus()
    out.setTemplate_(False)
    return out


def icon_point_size() -> tuple[float, float]:
    """Size, in points, at which the menu-bar image must be displayed.

    rumps hard-codes every status icon to 20x20 points, which would squash
    a 5:2 mark into a smear; `app.py` undoes that using this. Measured from
    the loaded asset rather than hard-coded, so the square fallback glyph
    still comes out square.
    """
    size = _base_image().size()
    return (float(size.width), float(size.height))


def icon_path_for(state: IconState) -> str | None:
    """Write the icon for `state` to a cached PNG and return its path.

    rumps' `App.icon` setter takes a filesystem path. We render once per
    state and cache the file. Returns None if writing fails (caller should
    fall back to a title-only menu bar).
    """
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
    except Exception:
        _log.exception('Could not create menubar cache dir %s', CACHE_DIR)
        return None

    stamp = _asset_fingerprint()
    path = os.path.join(CACHE_DIR, f'{state.value}-{stamp}.png')
    if os.path.exists(path):
        return path

    data = _render_png(state)
    if data is None:
        return None
    try:
        tmp = f'{path}.tmp'
        with open(tmp, 'wb') as f:
            f.write(data)
        os.replace(tmp, path)
    except OSError:
        _log.exception('Failed to write menubar icon PNG to %s', path)
        return None

    _prune_stale(stamp)
    return path


def is_template(state: IconState) -> bool:
    """Whether the icon for `state` should be treated as a template image.

    Idle stays pure black-on-transparent so the system tints it for the
    current menu bar. The other states are already coloured, so the system
    must NOT auto-tint or they all turn grey.
    """
    return state == IconState.IDLE


# ── Rendering ────────────────────────────────────────────────────

def _base_image() -> NSImage:
    return brand.menubar_template() or _draw_microphone()


def _render_png(state: IconState) -> bytes | None:
    """Rasterise the glyph for `state` at RENDER_SCALE and encode as PNG.

    Deliberately not `lockFocus` + `TIFFRepresentation`: that rasterises at
    whatever backing scale the current display happens to have, so the
    cached file would come out 1x on one Mac and 2x on another, and the
    1x one is a blur once the menu bar scales it back up. An explicit
    bitmap rep pins the resolution.
    """
    base = _base_image()
    size = base.size()
    pw = max(1, int(round(size.width * RENDER_SCALE)))
    ph = max(1, int(round(size.height * RENDER_SCALE)))

    rep = (
        NSBitmapImageRep.alloc()
        .initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
            None, pw, ph, 8, 4, True, False, NSDeviceRGBColorSpace, 0, 0,
        )
    )
    if rep is None:
        _log.warning('Could not allocate a %dx%d bitmap for the menu-bar icon', pw, ph)
        return None
    ctx = NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep)
    if ctx is None:
        _log.warning('Could not make a graphics context for the menu-bar icon')
        return None

    NSGraphicsContext.saveGraphicsState()
    try:
        NSGraphicsContext.setCurrentContext_(ctx)
        rect = NSMakeRect(0, 0, pw, ph)
        # NSZeroRect as the source rect means "the whole image".
        base.drawInRect_fromRect_operation_fraction_(
            rect, NSMakeRect(0, 0, 0, 0), NSCompositingOperationSourceOver, 1.0,
        )
        color = _STATE_COLOR.get(state)
        if color is not None:
            brand.color(color).setFill()
            NSRectFillUsingOperation(rect, NSCompositingOperationSourceAtop)
        ctx.flushGraphics()
    finally:
        NSGraphicsContext.restoreGraphicsState()

    data = rep.representationUsingType_properties_(NSBitmapImageFileTypePNG, {})
    return bytes(data) if data is not None else None


# ── Cache invalidation ───────────────────────────────────────────

_fingerprint: str | None = None


def _asset_fingerprint() -> str:
    """Short hash of the source asset, used in the cache filename.

    Rendered PNGs live in ~/Library/Caches indefinitely. Without this, a
    user who updates the app keeps the previous release's menu-bar glyph
    forever — the cache would hit on `idle.png` and never re-render.
    """
    global _fingerprint
    if _fingerprint is not None:
        return _fingerprint

    h = hashlib.sha256()
    for name in _ASSET_NAMES:
        path = os.path.join(brand.ASSETS_DIR, name)
        if os.path.exists(path):
            try:
                with open(path, 'rb') as f:
                    h.update(f.read())
            except OSError:
                h.update(name.encode())
            break
    else:
        h.update(b'fallback-microphone')
    h.update(f'{brand.MENUBAR_HEIGHT_PT}:{RENDER_SCALE}'.encode())

    _fingerprint = h.hexdigest()[:10]
    return _fingerprint


def _prune_stale(stamp: str) -> None:
    """Drop cached PNGs from earlier versions of the glyph."""
    try:
        for name in os.listdir(CACHE_DIR):
            if name.endswith('.png') and f'-{stamp}.png' not in name:
                os.remove(os.path.join(CACHE_DIR, name))
    except OSError:
        _log.debug('Could not prune %s', CACHE_DIR, exc_info=True)


# ── Microphone fallback glyph ────────────────────────────────────

def _draw_microphone() -> NSImage:
    """A simple capsule mic + stand, pure black, template-ready."""
    img = NSImage.alloc().initWithSize_(NSMakeSize(ICON_PT, ICON_PT))
    img.lockFocus()
    try:
        NSColor.blackColor().setFill()
        NSColor.blackColor().setStroke()

        # Mic capsule: rounded rect, vertically centered-up
        capsule = NSMakeRect(6.5, 6.5, 5.0, 9.0)
        path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            capsule, 2.5, 2.5
        )
        path.fill()

        # U-shaped cradle under the capsule
        cradle = NSBezierPath.bezierPath()
        cradle.setLineWidth_(1.4)
        cradle.setLineCapStyle_(1)  # round cap
        cradle.moveToPoint_((4.0, 7.0))
        cradle.curveToPoint_controlPoint1_controlPoint2_(
            (14.0, 7.0), (4.0, 1.5), (14.0, 1.5)
        )
        cradle.stroke()

        # Stand stem
        stand = NSBezierPath.bezierPath()
        stand.setLineWidth_(1.4)
        stand.setLineCapStyle_(1)
        stand.moveToPoint_((9.0, 4.5))
        stand.lineToPoint_((9.0, 1.5))
        stand.stroke()

        # Base
        base_path = NSBezierPath.bezierPath()
        base_path.setLineWidth_(1.6)
        base_path.setLineCapStyle_(1)
        base_path.moveToPoint_((6.0, 1.5))
        base_path.lineToPoint_((12.0, 1.5))
        base_path.stroke()
    finally:
        img.unlockFocus()
    img.setTemplate_(True)
    return img
