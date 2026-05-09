"""Menu-bar icon generation for Say.

The macOS menu bar wants a small monochrome *template* image that the
system tints automatically for light vs. dark menu bars. We support two
sources, in order:

1. `assets/menubar-template.pdf` (or `.png`) — designer-authored, used
   verbatim. This is the path we want shipped with the app.
2. A fallback microphone glyph drawn in NSBezierPath at runtime, so the
   app still looks reasonable before any asset is dropped in `assets/`.
   The mascot bear identity lives in the wizard and Settings windows;
   at 18pt the menu bar can't hold a recognizable bear face anyway.

A small status badge (recording dot, processing rings) is composited in
the corner so all four states are visually distinct without changing
the menu-bar title.
"""

from __future__ import annotations

import os
from enum import Enum

from AppKit import (
    NSBezierPath,
    NSBitmapImageFileTypePNG,
    NSBitmapImageRep,
    NSColor,
    NSCompositingOperationSourceOver,
    NSImage,
    NSMakeRect,
    NSMakeSize,
)
from logging_setup import get_logger

import brand

_log = get_logger('menubar_icon')

ICON_PT = 18


class IconState(str, Enum):
    IDLE = 'idle'
    RECORDING = 'recording'
    PROCESSING = 'processing'
    ERROR = 'error'


def icon_for(state: IconState) -> NSImage:
    """Return an NSImage suitable for `rumps.App.icon`.

    Always returns a fresh image — callers should cache if needed.
    """
    base = brand.menubar_template() or _draw_microphone()
    return _composite_badge(base, state)


CACHE_DIR = os.path.expanduser('~/Library/Caches/Say/menubar')


def icon_path_for(state: IconState) -> str | None:
    """Write the icon for `state` to a cached PNG and return its path.

    rumps' `App.icon` setter takes a filesystem path. We render once per
    state and cache the file — subsequent calls just return the same
    path. Returns None if writing fails (caller should fall back to a
    title-only menu bar).
    """
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
    except Exception:
        _log.exception('Could not create menubar cache dir %s', CACHE_DIR)
        return None

    path = os.path.join(CACHE_DIR, f'{state.value}.png')
    if os.path.exists(path):
        return path

    img = icon_for(state)
    if not _write_png(img, path):
        return None
    return path


def is_template(state: IconState) -> bool:
    """Whether the icon for `state` should be treated as a template image.

    Idle has no badge so it stays pure black-on-transparent and the
    system tints it. Other states paint a colored dot, so the system
    must NOT auto-tint or the dot turns grey.
    """
    return state == IconState.IDLE


# ── PNG writer (TIFF -> bitmap rep -> PNG) ───────────────────────

def _write_png(image: NSImage, path: str) -> bool:
    try:
        tiff = image.TIFFRepresentation()
        if tiff is None:
            return False
        rep = NSBitmapImageRep.imageRepWithData_(tiff)
        if rep is None:
            return False
        png_data = rep.representationUsingType_properties_(
            NSBitmapImageFileTypePNG, {},
        )
        if png_data is None:
            return False
        return bool(png_data.writeToFile_atomically_(path, True))
    except Exception:
        _log.exception('Failed to write menubar icon PNG to %s', path)
        return False


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


# ── State badge composite ────────────────────────────────────────

def _composite_badge(base: NSImage, state: IconState) -> NSImage:
    """Overlay a small status dot in the upper-right of the base image.

    Idle leaves the base image untouched. Coloured states render a
    coloured dot, which means the result is no longer a pure template
    image — we set template=False so the menu bar doesn't try to tint
    the dot grey.
    """
    if state == IconState.IDLE:
        return base

    size = base.size()
    out = NSImage.alloc().initWithSize_(NSMakeSize(size.width, size.height))
    out.lockFocus()
    try:
        base.drawAtPoint_fromRect_operation_fraction_(
            (0, 0), NSMakeRect(0, 0, size.width, size.height),
            NSCompositingOperationSourceOver, 1.0,
        )

        dot_d = 7.0
        dot_x = size.width - dot_d - 0.5
        dot_y = size.height - dot_d - 0.5
        rect = NSMakeRect(dot_x, dot_y, dot_d, dot_d)

        if state == IconState.RECORDING:
            brand.color(brand.PINK_700).setFill()
        elif state == IconState.PROCESSING:
            brand.color(brand.SKY_500).setFill()
        else:  # ERROR
            brand.color((0.851, 0.310, 0.353)).setFill()

        NSBezierPath.bezierPathWithOvalInRect_(rect).fill()

        if state == IconState.PROCESSING:
            for r_off in (1.5, 3.0):
                ring = NSMakeRect(
                    dot_x - r_off,
                    dot_y - r_off,
                    dot_d + 2 * r_off,
                    dot_d + 2 * r_off,
                )
                path = NSBezierPath.bezierPathWithOvalInRect_(ring)
                path.setLineWidth_(0.6)
                brand.color(brand.SKY_500, alpha=0.35 / r_off).setStroke()
                path.stroke()
    finally:
        out.unlockFocus()
    out.setTemplate_(False)
    return out
