#!/usr/bin/env python3
"""Generate all Say brand assets from scratch using Pillow.

Logo: dusty-rose square background + cream folded sticky-note glyph, rotated 13°.
Outputs every icon size needed by the macOS app and the Tauri desktop app.
"""

from PIL import Image, ImageDraw
import math
import os

# ── Colour palette ────────────────────────────────────────────────
BG_COLOR   = (185, 122, 138)   # #B97A8A  dusty rose
NOTE_COLOR = (242, 233, 221)   # #F2E9DD  cream
FLAP_COLOR = (218, 208, 196)   # slightly darker cream (back of fold)

ROTATION = -13  # PIL rotates CCW; negative = clockwise visually


# ── Geometry helpers ──────────────────────────────────────────────

def arc_pts(cx, cy, r, a1, a2, n=32):
    """Points along a circular arc from a1° to a2° (screen coords, y-down)."""
    return [
        (cx + r * math.cos(math.radians(a1 + (a2 - a1) * i / n)),
         cy + r * math.sin(math.radians(a1 + (a2 - a1) * i / n)))
        for i in range(n + 1)
    ]


def note_polygon(L, T, R, B, r, fs):
    """Clockwise polygon for rounded-rect sticky note with fold cut at bottom-right.

    L/T/R/B = bounding box.  r = corner radius.  fs = fold size along each edge.
    """
    pts = []
    pts += arc_pts(L + r, T + r, r, 180, 270)   # top-left corner
    pts += arc_pts(R - r, T + r, r, 270, 360)   # top-right corner
    pts.append((R, B - fs))                      # right edge → fold start
    pts.append((R - fs, B))                      # fold diagonal
    pts += arc_pts(L + r, B - r, r, 90, 180)    # bottom-left corner
    return pts


# ── Logo renderer ─────────────────────────────────────────────────

def make_logo(px: int) -> Image.Image:
    """Return a square RGB image of the Say logo at px × px."""
    S = px * 2          # render at 2× for smooth antialiasing
    C = S // 2

    ns  = int(S * 0.565)   # note size  (~56.5 % of canvas)
    r   = int(ns * 0.108)  # corner radius
    fs  = int(ns * 0.238)  # fold size

    L = C - ns // 2
    T = C - ns // 2
    R = L + ns
    B = T + ns

    # Note layer (transparent background so rotation has clean edges)
    note_layer = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(note_layer)

    # 1. Fold flap — sits "behind" the main body, visible through the cut
    flap_pts = [(R, B - fs), (R - fs, B), (R - fs, B - fs)]
    d.polygon(flap_pts, fill=FLAP_COLOR + (255,))

    # 2. Main note body (cut reveals flap below)
    d.polygon(note_polygon(L, T, R, B, r, fs), fill=NOTE_COLOR + (255,))

    # Rotate note layer clockwise 13°
    note_rotated = note_layer.rotate(ROTATION, resample=Image.BICUBIC, center=(C, C))

    # Composite onto background
    bg = Image.new("RGBA", (S, S), BG_COLOR + (255,))
    final = Image.alpha_composite(bg, note_rotated)

    # Downsample to requested size with Lanczos.  Keep RGBA — Tauri's
    # icon validator rejects RGB-only PNGs.
    return final.resize((px, px), Image.LANCZOS)


# ── Menubar template renderer ─────────────────────────────────────

def make_menubar(px: int = 36) -> Image.Image:
    """Pure-black note silhouette on transparent background for macOS menu bar."""
    S = px * 6      # render at 6× for tiny sizes
    C = S // 2

    ns  = int(S * 0.72)
    r   = int(ns * 0.12)
    fs  = int(ns * 0.25)

    L = C - ns // 2
    T = C - ns // 2
    R = L + ns
    B = T + ns

    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d   = ImageDraw.Draw(img)
    d.polygon(note_polygon(L, T, R, B, r, fs), fill=(0, 0, 0, 255))
    return img.resize((px, px), Image.LANCZOS)


# ── Main ──────────────────────────────────────────────────────────

BASE = os.path.dirname(os.path.abspath(__file__))
ASSETS  = os.path.join(BASE, "say", "assets")
ICONS   = os.path.join(BASE, "desktop", "src-tauri", "icons")

tasks = [
    # (generator, size, output_path)
    (make_logo,    512,  os.path.join(ASSETS, "logo.png")),
    (make_logo,   1024,  os.path.join(ASSETS, "logo@2x.png")),
    (make_logo,   1024,  os.path.join(ICONS,  "icon.png")),
    (make_logo,     32,  os.path.join(ICONS,  "32x32.png")),
    (make_logo,    128,  os.path.join(ICONS,  "128x128.png")),
    (make_logo,    256,  os.path.join(ICONS,  "128x128@2x.png")),
    (make_menubar,  36,  os.path.join(ASSETS, "menubar-template.png")),
]

for fn, sz, path in tasks:
    img = fn(sz)
    img.save(path)
    print(f"  ✓  {os.path.relpath(path, BASE)}  ({sz}×{sz})")

print("\nAll assets generated.")
