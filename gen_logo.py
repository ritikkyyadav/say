#!/usr/bin/env python3
"""Generate every Say brand asset from one vector definition of the mark.

The mark is a *frozen frame of the dictation overlay*: a continuous base
with six stepped columns standing on it, built the same way
`say/waveform.py` builds the live glyph at 60 fps — one closed path, not N
rectangles, so adjacent columns share an edge and the thing reads as a
single object. Press Fn and the app's logo starts moving. (The live glyph
runs 12 uniform bands off the spectrum; the mark is the six-column
portrait of it, which is why the widths here are its own.)

Geometry is measured, not eyeballed. From the 1586x992 reference:

    ink bbox      1376 x 549 px          aspect 2.5064
    column edges  x = 130 392 614.5 918 1108.5 1298.5 1505
    widths        262 222.5 303.5 190.5 190 206.5
    column tops   610 433 206 303 429 606   baseline 753
    heights       144 321 548 451 325 148
    convex corners rounded ~7 px (1.3% of height); concave corners sharp

Normalised onto a 30 x 12 grid, which lands every edge on a clean
quarter-unit and makes the aspect exactly 5:2:

    widths        5.75  4.75  6.75  4.25  4.00  4.50
    vs reference  5.72  4.86  6.62  4.16  4.15  4.51   max drift 0.5% of width
    heights       3.25  7.00 12.00  9.75  7.00  3.25
    vs reference  3.15  7.03 12.00  9.88  7.12  3.24   max drift 1.1% of height

Two properties of this profile are deliberate and preserved. The widths
are genuinely uneven — nearly 1.7:1 between the widest column and the
narrowest, far outside render noise — so columns are stored as
(width, height) pairs and there is no uniform pitch to factor out. And the
flanks are symmetric while the peak is not: columns 1/6 and 2/5 each
measure within 0.75% of their opposite number, close enough to snap to
matching values, which leaves the peak's 12-vs-9.75 lopsidedness as the
only asymmetry — and that is the one you actually see, the thing that
reads as a waveform caught mid-word rather than a symmetric generic bell.

The mass centroid lands at 50.9% of the width, so the mark needs no
horizontal optical correction — only a small vertical one (see
MARK_OPTICAL_LIFT).

This replaced a 5:1 version of the same six-column mark. Anything that
hard-codes the old aspect is a bug: `say/brand.py`, `say/menubar_icon.py`
and `say/tests/test_brand_assets.py` all carry it explicitly.

Usage:
    python3 gen_logo.py              # write all assets
    python3 gen_logo.py --preview    # write a contact sheet, touch nothing
"""

from __future__ import annotations

import math
import os
import shutil
import subprocess
import sys
import tempfile

from PIL import Image, ImageDraw

# ── Palette (desktop/tailwind.config.js is the source of truth) ────
INK        = (20, 20, 19)      # #141413  warm near-black — the mark
BOOK_CLOTH = (250, 249, 245)   # #faf9f5  cream canvas — the tile
EDGE       = (20, 20, 19, 38)  # ink hairline around the tile

# ── The mark, on a 30 x 12 unit grid ───────────────────────────────
# (width, height) per column, left to right. Widths are not uniform — see
# the module docstring — so there is no MARK_COLUMN_W to divide by.
MARK_COLUMNS = (
    (5.75,  3.25),
    (4.75,  7.00),
    (6.75, 12.00),
    (4.25,  9.75),
    (4.00,  7.00),
    (4.50,  3.25),
)
MARK_W = sum(w for w, _h in MARK_COLUMNS)   # 30
MARK_H = max(h for _w, h in MARK_COLUMNS)   # 12
MARK_RADIUS = 0.15                          # 1.3% of height, per the reference

# ── Icon composition ───────────────────────────────────────────────
# macOS Big Sur grid: the squircle is 824 wide in a 1024 canvas.
TILE_RATIO = 824.0 / 1024.0
TILE_CORNER_RATIO = 0.251   # corner radius as a fraction of the tile side
TILE_CORNER_N = 2.30        # corner superellipse exponent (2.0 would be a circle)
TILE_EDGE_RATIO = 0.006     # hairline width as a fraction of the tile side

# The mark's width as a fraction of the tile side, and how far to lift it
# above true centre. The silhouette is bottom-heavy (mass centroid sits at
# 36.7% of its own height), so bounding-box centring reads as "sunk" — but
# moving the centroid all the way onto centre (lift 0.133) over-corrects and
# opens a visible void beneath the base. Half of it is what looks level.
MARK_WIDTH_RATIO = 0.78
MARK_OPTICAL_LIFT = 0.066   # x mark height — half the full centroid correction

# Supersample factor. The reduction back down uses Image.BOX, never
# LANCZOS: at an exact 8x integer ratio BOX is plain area-averaging, which
# is ideal antialiasing for synthetic vector art. LANCZOS has negative
# lobes, and on the mark's long horizontal edges those ring — the base
# slab ends up with a white seam separating it from the columns above.
SS = 8

# Icons at or below this size get the pixel-snapped mark (see
# snapped_mark_rects); above it, the smooth supersampled path.
SNAP_MAX_PX = 128

# Menu-bar glyph, in points. At 5:2 the mark can finally take an ordinary
# status-glyph height instead of hiding at half of one: 14 pt sits in the
# normal 12-18 pt range, and the 35 pt width it implies is the exact
# footprint the old 5:1 mark already occupied. `brand.MENUBAR_HEIGHT_PT`
# must agree with the height here or the asset gets rescaled on load.
MENUBAR_PT = (35, 14)


# ── Geometry helpers ───────────────────────────────────────────────

def _signed_area(pts: list[tuple[float, float]]) -> float:
    n = len(pts)
    return 0.5 * sum(
        pts[i][0] * pts[(i + 1) % n][1] - pts[(i + 1) % n][0] * pts[i][1]
        for i in range(n)
    )


def round_convex_corners(
    pts: list[tuple[float, float]], radius: float, samples: int = 10
) -> list[tuple[float, float]]:
    """Round only the convex corners of a closed polygon.

    The reference rounds every outer turn with a small arc and leaves
    every inner (step) turn perfectly sharp — measured directly off the
    pixels. Rounding both would soften the steps into a blob at icon
    sizes; rounding neither would alias badly on the outline.
    """
    pts = [p for i, p in enumerate(pts) if p != pts[i - 1]]
    n = len(pts)
    if n < 3 or radius <= 0:
        return pts
    ccw = _signed_area(pts) > 0

    out: list[tuple[float, float]] = []
    for i in range(n):
        prev, cur, nxt = pts[i - 1], pts[i], pts[(i + 1) % n]
        v1 = (cur[0] - prev[0], cur[1] - prev[1])
        v2 = (nxt[0] - cur[0], nxt[1] - cur[1])
        l1 = math.hypot(*v1)
        l2 = math.hypot(*v2)
        if l1 == 0 or l2 == 0:
            out.append(cur)
            continue
        cross = v1[0] * v2[1] - v1[1] * v2[0]
        convex = (cross > 0) if ccw else (cross < 0)
        if not convex or abs(cross) < 1e-9:
            out.append(cur)          # concave or collinear: leave it sharp
            continue

        r = min(radius, l1 / 2.0, l2 / 2.0)
        u1 = (v1[0] / l1, v1[1] / l1)
        u2 = (v2[0] / l2, v2[1] / l2)
        a = (cur[0] - u1[0] * r, cur[1] - u1[1] * r)
        b = (cur[0] + u2[0] * r, cur[1] + u2[1] * r)
        # Every corner in this mark is a right angle, for which the arc
        # centre is exactly a + b - cur.
        cx, cy = a[0] + b[0] - cur[0], a[1] + b[1] - cur[1]
        a0 = math.atan2(a[1] - cy, a[0] - cx)
        a1 = math.atan2(b[1] - cy, b[0] - cx)
        sweep = (a1 - a0 + math.pi) % (2 * math.pi) - math.pi  # shortest way
        for s in range(samples + 1):
            t = a0 + sweep * s / samples
            out.append((cx + r * math.cos(t), cy + r * math.sin(t)))
    return out


def mark_polygon(radius: float = MARK_RADIUS) -> list[tuple[float, float]]:
    """The mark as a closed y-up polygon on the 30 x 12 grid, corners rounded."""
    pts: list[tuple[float, float]] = [(0.0, 0.0)]
    x = 0.0
    for w, h in MARK_COLUMNS:
        pts.append((x, h))
        pts.append((x + w, h))
        x += w
    pts.append((MARK_W, 0.0))
    return round_convex_corners(pts, radius)


def snapped_mark_rects(width_px: int, height_px: int
                       ) -> list[tuple[int, int, int, int]]:
    """The mark as whole-pixel column rects, for 16 and 32 px icons.

    Below ~32 px the mark is only four or five pixels tall, and a
    supersampled fill turns the steps into a grey smear. Snapping every
    edge to the destination pixel grid — and giving each column a floor of
    one pixel so the base never vanishes — is what keeps it a waveform
    instead of a dash. Hand-tuning the small sizes is standard practice in
    a real icon set; this just does it arithmetically.
    """
    n = len(MARK_COLUMNS)
    edges, acc = [0], 0.0
    for w, _h in MARK_COLUMNS:
        acc += w
        edges.append(round(acc / MARK_W * width_px))

    # Uneven widths plus integer rounding can round the narrowest column
    # away entirely at 16 px. Push each edge forward far enough to leave one
    # pixel, then pull the tail back so the last edge still lands exactly on
    # the right border. Needs width_px >= n, which render_icon guarantees.
    for i in range(1, n + 1):
        edges[i] = max(edges[i], edges[i - 1] + 1)
    edges[n] = width_px
    for i in range(n - 1, 0, -1):
        edges[i] = min(edges[i], edges[i + 1] - 1)

    rects = []
    for i, (_w, h) in enumerate(MARK_COLUMNS):
        hp = max(1, round(h / MARK_H * height_px))
        rects.append((edges[i], height_px - hp, edges[i + 1], height_px))
    return rects


def squircle(cx: float, cy: float, half: float, samples: int = 180
             ) -> list[tuple[float, float]]:
    """The macOS app-icon tile: a rounded rect with squircled corners.

    Measured off Notes.app's own icon rather than assumed. A whole-shape
    superellipse — the usual shortcut — fits the real silhouette to only
    5.2 px rms on a 206 px tile and visibly bulges the flat sides. A
    rounded rect whose corners follow |x/r|^n + |y/r|^n = 1 with
    r = 25.1% of the side and n = 2.30 fits to 0.34 px.
    """
    r = 2 * half * TILE_CORNER_RATIO
    n = TILE_CORNER_N
    left, top = cx - half, cy - half
    right, bottom = cx + half, cy + half

    def arc(ox: float, oy: float, sx: int, sy: int) -> list[tuple[float, float]]:
        # Quarter arc around corner centre (ox, oy); (sx, sy) points outward.
        out = []
        for i in range(samples + 1):
            t = (math.pi / 2) * i / samples
            out.append((ox + sx * r * math.cos(t) ** (2.0 / n),
                        oy + sy * r * math.sin(t) ** (2.0 / n)))
        return out

    # Clockwise from the left end of the top edge. The straight sides are
    # implicit — consecutive arc endpoints are joined by the polygon fill.
    return (
        arc(left + r, top + r, -1, -1)             # top-left
        + arc(right - r, top + r, +1, -1)[::-1]    # top-right
        + arc(right - r, bottom - r, +1, +1)       # bottom-right
        + arc(left + r, bottom - r, -1, +1)[::-1]  # bottom-left
    )


def _place_mark(width: float, left: float, baseline: float) -> list[tuple[float, float]]:
    """Scale the unit mark to `width` and place it in y-down image space."""
    s = width / MARK_W
    return [(left + x * s, baseline - y * s) for x, y in mark_polygon()]


# ── Renderers ──────────────────────────────────────────────────────

def _flatten(rgba: tuple[int, int, int, int],
             over: tuple[int, int, int]) -> tuple[int, int, int, int]:
    """Composite `rgba` over an opaque `over` colour, keeping it opaque."""
    a = rgba[3] / 255.0
    return (*(int(round(rgba[i] * a + over[i] * (1 - a))) for i in range(3)), 255)


def render_icon(px: int) -> Image.Image:
    """Square app icon: cream squircle, hairline edge, ink mark."""
    S = px * SS
    crisp = px <= SNAP_MAX_PX
    img = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    half = S * TILE_RATIO / 2.0
    c = S / 2.0
    # Two filled squircles, not a stroked one: a stroked polyline mitres
    # badly on a 720-point path and leaves a soft halo instead of an edge.
    edge = max(1.0, S * TILE_RATIO * TILE_EDGE_RATIO)
    d.polygon(squircle(c, c, half), fill=_flatten(EDGE, BOOK_CLOTH))
    d.polygon(squircle(c, c, half - edge), fill=BOOK_CLOTH + (255,))

    if not crisp:
        mark_w = 2 * half * MARK_WIDTH_RATIO
        mark_h = mark_w * MARK_H / MARK_W
        baseline = c + mark_h / 2.0 - mark_h * MARK_OPTICAL_LIFT
        d.polygon(_place_mark(mark_w, c - mark_w / 2.0, baseline), fill=INK + (255,))

    out = img.resize((px, px), Image.BOX)
    if not crisp:
        return out

    # The snapped mark is drawn at 1:1 on the already-downsampled tile.
    # Downsampling it would defeat the point: Lanczos has negative lobes,
    # so an 8x reduction of a hard edge rings, and the ringing shows up as
    # grey bands straight across a 16 px icon.
    w_px = max(len(MARK_COLUMNS), round(px * TILE_RATIO * MARK_WIDTH_RATIO))
    h_px = max(2, round(w_px * MARK_H / MARK_W))
    left = round((px - w_px) / 2.0)
    top = round((px - h_px) / 2.0 - h_px * MARK_OPTICAL_LIFT)
    od = ImageDraw.Draw(out)
    for x0, y0, x1, y1 in snapped_mark_rects(w_px, h_px):
        od.rectangle([left + x0, top + y0, left + x1 - 1, top + y1 - 1],
                     fill=INK + (255,))
    return out


def render_mark(width_px: int, pad_ratio: float = 0.0,
                color: tuple[int, int, int] = INK) -> Image.Image:
    """The bare mark on transparency, at its true 5:2 aspect."""
    W = width_px * SS
    pad = W * pad_ratio
    inner = W - 2 * pad
    H = int(round(inner * MARK_H / MARK_W + 2 * pad))
    img = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(img).polygon(
        _place_mark(inner, pad, H - pad), fill=color + (255,)
    )
    return img.resize((width_px, max(1, int(round(H / SS)))), Image.BOX)


def render_ios(px: int) -> Image.Image:
    """Full-bleed square icon: iOS masks the corners itself, so we must not."""
    S = px * SS
    img = Image.new('RGB', (S, S), BOOK_CLOTH)
    mark_w = S * MARK_WIDTH_RATIO * TILE_RATIO
    mark_h = mark_w * MARK_H / MARK_W
    baseline = S / 2.0 + mark_h / 2.0 - mark_h * MARK_OPTICAL_LIFT
    ImageDraw.Draw(img).polygon(
        _place_mark(mark_w, (S - mark_w) / 2.0, baseline), fill=INK
    )
    return img.resize((px, px), Image.BOX)


def render_adaptive_foreground(px: int) -> Image.Image:
    """Android adaptive-icon foreground: mark inside the 66/108 safe zone."""
    S = px * SS
    img = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    mark_w = S * (66.0 / 108.0) * MARK_WIDTH_RATIO
    mark_h = mark_w * MARK_H / MARK_W
    baseline = S / 2.0 + mark_h / 2.0 - mark_h * MARK_OPTICAL_LIFT
    ImageDraw.Draw(img).polygon(
        _place_mark(mark_w, (S - mark_w) / 2.0, baseline), fill=INK + (255,)
    )
    return img.resize((px, px), Image.BOX)


def render_round(px: int) -> Image.Image:
    """Android `ic_launcher_round`: the tile clipped to a circle."""
    S = px * SS
    img = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([0, 0, S - 1, S - 1], fill=BOOK_CLOTH + (255,))
    mark_w = S * MARK_WIDTH_RATIO * TILE_RATIO
    mark_h = mark_w * MARK_H / MARK_W
    baseline = S / 2.0 + mark_h / 2.0 - mark_h * MARK_OPTICAL_LIFT
    d.polygon(_place_mark(mark_w, (S - mark_w) / 2.0, baseline), fill=INK + (255,))
    return img.resize((px, px), Image.BOX)


# ── Output ─────────────────────────────────────────────────────────

BASE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(BASE, 'say', 'assets')
ICONS = os.path.join(BASE, 'desktop', 'src-tauri', 'icons')
PUBLIC = os.path.join(BASE, 'desktop', 'public')

# macOS .icns needs both members of each @1x/@2x pair.
ICNS_SIZES = [
    ('icon_16x16.png', 16), ('icon_16x16@2x.png', 32),
    ('icon_32x32.png', 32), ('icon_32x32@2x.png', 64),
    ('icon_128x128.png', 128), ('icon_128x128@2x.png', 256),
    ('icon_256x256.png', 256), ('icon_256x256@2x.png', 512),
    ('icon_512x512.png', 512), ('icon_512x512@2x.png', 1024),
]


def write_icns(path: str) -> bool:
    """Build a real multi-resolution .icns via iconutil. False if unavailable."""
    if shutil.which('iconutil') is None:
        print('  !  iconutil not found — skipping', os.path.relpath(path, BASE))
        return False
    with tempfile.TemporaryDirectory() as tmp:
        iconset = os.path.join(tmp, 'icon.iconset')
        os.makedirs(iconset)
        for name, size in ICNS_SIZES:
            render_icon(size).save(os.path.join(iconset, name))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        subprocess.run(['iconutil', '-c', 'icns', iconset, '-o', path], check=True)
    return True


def write_menubar_pdf(path: str) -> None:
    """Write the mark as a real vector PDF for the menu bar.

    A PNG template has to pick a scale factor and is then resampled by
    AppKit for every other one; at 7 pt tall there is no resampling budget
    to spare. PDF is what `brand.menubar_template()` looks for first, and a
    single filled path is small enough to emit by hand — PDF user space is
    already y-up, so the mark polygon goes in unflipped.
    """
    w, h = MENUBAR_PT
    scale = w / MARK_W
    ops = ['0 0 0 rg']
    for i, (x, y) in enumerate(mark_polygon()):
        ops.append(f'{x * scale:.4f} {y * scale:.4f} {"m" if i == 0 else "l"}')
    ops.append('h f')
    stream = ('\n'.join(ops) + '\n').encode('ascii')

    objects = [
        b'<</Type/Catalog/Pages 2 0 R>>',
        b'<</Type/Pages/Kids[3 0 R]/Count 1>>',
        (f'<</Type/Page/Parent 2 0 R/MediaBox[0 0 {w} {h}]'
         f'/Contents 4 0 R/Resources<<>>>>').encode('ascii'),
        b'<</Length ' + str(len(stream)).encode('ascii') + b'>>stream\n'
        + stream + b'endstream',
    ]

    out = bytearray(b'%PDF-1.4\n')
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f'{i} 0 obj'.encode('ascii') + body + b'endobj\n'
    xref = len(out)
    out += f'xref\n0 {len(objects) + 1}\n'.encode('ascii')
    out += b'0000000000 65535 f \n'
    for off in offsets:
        out += f'{off:010d} 00000 n \n'.encode('ascii')
    out += (f'trailer<</Size {len(objects) + 1}/Root 1 0 R>>\n'
            f'startxref\n{xref}\n%%EOF\n').encode('ascii')
    with open(path, 'wb') as f:
        f.write(bytes(out))


def write_ico(path: str) -> None:
    sizes = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    render_icon(256).save(path, format='ICO', sizes=sizes)


def main() -> None:
    preview = '--preview' in sys.argv

    tasks: list[tuple[str, int, str]] = [
        # ── say (the Python engine app) ──
        ('icon', 512, os.path.join(ASSETS, 'logo.png')),
        ('icon', 1024, os.path.join(ASSETS, 'logo@2x.png')),
        # ── Tauri desktop app ──
        # 512, not 1024: tauri-bundler derives icns density from the file
        # name, so a 1024 px `icon.png` (no @2x suffix) is density 1 — and
        # 1024@1x has no IconType. 1024 only exists as 512@2x.
        ('icon', 512, os.path.join(ICONS, 'icon.png')),
        ('icon', 32, os.path.join(ICONS, '32x32.png')),
        ('icon', 64, os.path.join(ICONS, '64x64.png')),
        ('icon', 128, os.path.join(ICONS, '128x128.png')),
        ('icon', 256, os.path.join(ICONS, '128x128@2x.png')),
        # ── web (favicon + sidebar) ──
        ('icon', 1024, os.path.join(PUBLIC, 'logo.png')),
        ('icon', 512, os.path.join(PUBLIC, 'logo-512.png')),
        # ── Windows tiles ──
        ('icon', 30, os.path.join(ICONS, 'Square30x30Logo.png')),
        ('icon', 44, os.path.join(ICONS, 'Square44x44Logo.png')),
        ('icon', 71, os.path.join(ICONS, 'Square71x71Logo.png')),
        ('icon', 89, os.path.join(ICONS, 'Square89x89Logo.png')),
        ('icon', 107, os.path.join(ICONS, 'Square107x107Logo.png')),
        ('icon', 142, os.path.join(ICONS, 'Square142x142Logo.png')),
        ('icon', 150, os.path.join(ICONS, 'Square150x150Logo.png')),
        ('icon', 284, os.path.join(ICONS, 'Square284x284Logo.png')),
        ('icon', 310, os.path.join(ICONS, 'Square310x310Logo.png')),
        ('icon', 50, os.path.join(ICONS, 'StoreLogo.png')),
    ]

    # iOS app icons are full-bleed and opaque — the system draws the mask.
    ios = os.path.join(ICONS, 'ios')
    if os.path.isdir(ios):
        for name in sorted(os.listdir(ios)):
            if name.endswith('.png'):
                with Image.open(os.path.join(ios, name)) as im:
                    tasks.append(('ios', im.size[0], os.path.join(ios, name)))

    # Android: legacy square + round launchers, and the adaptive foreground.
    android = os.path.join(ICONS, 'android')
    if os.path.isdir(android):
        for root, _dirs, files in os.walk(android):
            for name in sorted(files):
                if not name.endswith('.png'):
                    continue
                path = os.path.join(root, name)
                with Image.open(path) as im:
                    size = im.size[0]
                kind = ('adaptive' if 'foreground' in name
                        else 'round' if 'round' in name else 'icon')
                tasks.append((kind, size, path))

    render = {
        'icon': render_icon,
        'ios': render_ios,
        'round': render_round,
        'adaptive': render_adaptive_foreground,
    }

    if preview:
        out = os.path.join(BASE, 'logo-preview.png')
        _contact_sheet().save(out)
        print(f'  ✓  {os.path.relpath(out, BASE)}  (preview only — nothing else written)')
        return

    for kind, size, path in tasks:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        render[kind](size).save(path)
        print(f'  ✓  {os.path.relpath(path, BASE)}  ({size}x{size})')

    # Menu-bar template: pure black on transparent. The PDF is what the app
    # actually loads; the PNG stays as a fallback if the PDF goes missing.
    pdf = os.path.join(ASSETS, 'menubar-template.pdf')
    write_menubar_pdf(pdf)
    print(f'  ✓  {os.path.relpath(pdf, BASE)}  ({MENUBAR_PT[0]}x{MENUBAR_PT[1]} pt, vector)')
    png = os.path.join(ASSETS, 'menubar-template.png')
    render_mark(MENUBAR_PT[0] * 3, color=(0, 0, 0)).save(png)
    print(f'  ✓  {os.path.relpath(png, BASE)}  ({MENUBAR_PT[0]}x{MENUBAR_PT[1]} pt @3x)')

    for icns in (os.path.join(ASSETS, 'Say.icns'), os.path.join(ICONS, 'icon.icns')):
        if write_icns(icns):
            print(f'  ✓  {os.path.relpath(icns, BASE)}  (10 representations)')

    ico = os.path.join(ICONS, 'icon.ico')
    write_ico(ico)
    print(f'  ✓  {os.path.relpath(ico, BASE)}  (6 representations)')

    print('\nAll assets generated.')


def _contact_sheet() -> Image.Image:
    sizes = [512, 256, 128, 64, 32, 16]
    pad = 24
    width = pad + sum(s + pad for s in sizes)
    sheet = Image.new('RGB', (width, (512 + pad * 2) * 2), (255, 255, 255))
    for row, bg in enumerate(((235, 233, 226), (38, 38, 36))):
        band = Image.new('RGB', (width, 512 + pad * 2), bg)
        x = pad
        for s in sizes:
            ic = render_icon(s)
            band.paste(ic, (x, pad + (512 - s) // 2), ic)
            x += s + pad
        sheet.paste(band, (0, row * (512 + pad * 2)))
    return sheet


if __name__ == '__main__':
    main()
