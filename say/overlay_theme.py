"""Colour themes for the dictation glyph.

The overlay has always been one tone that follows the system appearance. This
module lets it be several: a theme is a small list of colours cycled across the
twelve columns, so the mark can read as alternating slashes — red, ink, red,
ink — instead of one solid silhouette.

**It does not make the glyph twelve layers.** `waveform.silhouette()` still
returns a single closed polygon and `overlay.py` still draws exactly that one
path; the colours are painted *through* it as a clip mask. Adjacent columns
therefore still share an edge and can never disagree about a sub-pixel
boundary, which is the invariant that stops the mark reading as a row of
separate meters. See the note in `overlay.py::_build`.

Pure: no AppKit, no I/O. `overlay.py` turns the RGB tuples into CGColors and
`server/api.py` serialises the catalogue; both of those are the only places
that need anything Apple-specific.

Two rules worth keeping:

* **The default theme carries no colours at all.** `colors=()` means "do what
  you have always done" — ink follows the system appearance, rim is its
  opposite, `OVERLAY_CONTRAST` decides the policy. A fresh install and every
  existing user therefore see precisely the glyph they saw before, and the
  contrast tests keep testing the thing they were written for.

* **The rim is computed, never chosen.** The rim exists so that one of the two
  tones always has contrast against an unknown backdrop, which is a legibility
  guarantee, not a taste. Asking someone to pick it is asking them to break it,
  so a multi-colour theme derives its rim from the mean luminance of its own
  colours and takes the opposite tone.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

RGB = tuple[float, float, float]

# The two rim tones, matching overlay.INK_LIGHT / INK_DARK. Duplicated here
# rather than imported because this module must stay free of AppKit, and
# overlay.py imports *this* one.
TONE_DARK: RGB = (0.063, 0.071, 0.094)
TONE_LIGHT: RGB = (0.965, 0.969, 0.980)

MAX_COLORS = 6          # more than six and the cycle stops reading as a pattern
_HEX_RE = re.compile(r'^#?([0-9a-fA-F]{6}|[0-9a-fA-F]{3})$')


class BadColor(ValueError):
    """A colour string that is not a hex triple."""


@dataclass(frozen=True)
class OverlayTheme:
    id: str
    name: str
    colors: tuple[str, ...]     # cycled across the columns; () = follow the system
    blurb: str

    @property
    def adaptive(self) -> bool:
        return not self.colors


# The catalogue. `ink` must stay first and must stay empty — see the module
# docstring. The rest are pairs on purpose: alternating two colours is what
# makes the row read as slashes rather than as a gradient.
PRESETS: tuple[OverlayTheme, ...] = (
    OverlayTheme('ink', 'Ink', (), 'Follows your system appearance. The original.'),
    OverlayTheme('ember', 'Ember', ('#D9713F', '#17150F'),
                 'Say orange against near-black.'),
    OverlayTheme('signal', 'Signal', ('#CA152C', '#FFECEE'),
                 'Crimson on blush. The loudest one.'),
    OverlayTheme('crimson', 'Crimson', ('#CA152C', '#2C2C2E'),
                 'Crimson against charcoal.'),
    OverlayTheme('mono', 'Mono', ('#17150F', '#FDFCF8'),
                 'Black and white, alternating.'),
    OverlayTheme('forest', 'Forest', ('#3F7D52', '#17150F'),
                 'Deep green against near-black.'),
    OverlayTheme('tide', 'Tide', ('#2E6F9E', '#FDFCF8'),
                 'Blue and off-white.'),
    OverlayTheme('bloom', 'Bloom', ('#D9713F', '#E9B44C', '#17150F'),
                 'Three colours on a rotation.'),
    OverlayTheme('dusk', 'Dusk', ('#6E4B8E', '#E9B44C'),
                 'Violet and amber.'),
)

# ── How the colours are distributed across the columns ─────────────────────
#
# The first version of this only ever did `i % n`: column 0 red, column 1 ink,
# column 2 red… which reads as a stripe — a fixed barber-pole that happens to
# wobble. The colours were a property of *position*, and position never
# changes, so the pattern was the same in every frame of every dictation.
#
# The interesting patterns take their colour from the **audio** instead. Every
# column reads the same spectrum in the same frame, and `BarMotion` already
# couples neighbouring bars into one membrane, so a colour derived from height
# inherits that coordination for free: runs of colour form, travel and break
# apart together. It is tightly synchronised because it is all one signal, and
# unpredictable because speech is.

ALTERNATE = 'alternate'
PULSE = 'pulse'
FLOW = 'flow'
SPARK = 'spark'
DRIFT = 'drift'

DEFAULT_PATTERN = PULSE
VALID_PATTERNS: tuple[str, ...] = (PULSE, SPARK, FLOW, DRIFT, ALTERNATE)

PATTERN_INFO: tuple[dict[str, str], ...] = (
    {'id': PULSE, 'name': 'Pulse',
     'blurb': 'Colour follows loudness. Never lands the same way twice.'},
    {'id': SPARK, 'name': 'Spark',
     'blurb': 'Only the peaks catch the second colour.'},
    {'id': FLOW, 'name': 'Flow',
     'blurb': 'A colour wave travelling the row.'},
    {'id': DRIFT, 'name': 'Drift',
     'blurb': 'A fixed scatter that never lines up.'},
    {'id': ALTERNATE, 'name': 'Stripe',
     'blurb': 'Every other column. Regular and calm.'},
)

# The conjugate of the golden ratio. Stepping by it lands anywhere but in a
# repeating cycle, which is what makes DRIFT look scattered while staying
# perfectly even — the standard low-discrepancy trick.
_PHI = 0.6180339887498949

# PULSE mixes each bar's own loudness with a fixed per-column offset. Pure
# loudness (scatter 0) collapses the whole row to one colour whenever the
# spectrum is flat, which looks like the theme broke. Measured on a flat
# spectrum, 0.35 still collapsed everywhere below 0.35 and above 0.65 — i.e.
# most of the usable range. At 0.5 the only levels that go uniform are true
# silence and true saturation, which *should* be uniform: at that point the
# colour is honestly reporting that every band is the same.
PULSE_SCATTER = 0.5

FLOW_SPEED = 0.34          # rotations per second
SPARK_SHARE = 0.3          # top fraction of bars that take the accent


def column_colors(
    pattern: str,
    n_colors: int,
    heights: Sequence[float],
    t: float = 0.0,
) -> list[int]:
    """Which colour each column takes this frame, as indices into the list.

    Pure and frame-local: the same inputs always give the same output, which
    is what makes the patterns testable at all. `heights` are the 0–1 bar
    heights the motion engine just produced; `t` is a monotonic clock in
    seconds, used only by FLOW.
    """
    bands = len(heights)
    if n_colors <= 1 or bands == 0:
        return [0] * bands
    pattern = (pattern or DEFAULT_PATTERN).strip().lower()

    if pattern == ALTERNATE:
        return [i % n_colors for i in range(bands)]

    if pattern == DRIFT:
        return [int((i * _PHI % 1.0) * n_colors) % n_colors for i in range(bands)]

    if pattern == FLOW:
        return [
            int((((i / bands) + t * FLOW_SPEED) % 1.0) * n_colors) % n_colors
            for i in range(bands)
        ]

    if pattern == SPARK:
        # The loudest few bars take the last colour, everything else the first.
        # Rank rather than threshold, so it works at any speaking volume.
        keep = max(1, int(round(bands * SPARK_SHARE)))
        ranked = sorted(range(bands), key=lambda i: -heights[i])[:keep]
        hot = set(ranked)
        return [(n_colors - 1) if i in hot else 0 for i in range(bands)]

    # PULSE (the default): loudness, nudged by a fixed scatter.
    out: list[int] = []
    for i, h in enumerate(heights):
        level = clamp01(h)
        offset = (i * _PHI) % 1.0
        mixed = level * (1.0 - PULSE_SCATTER) + offset * PULSE_SCATTER
        out.append(min(n_colors - 1, int(clamp01(mixed) * n_colors)))
    return out


def clamp01(value: float) -> float:
    return 0.0 if value < 0.0 else (1.0 if value > 1.0 else float(value))


CUSTOM_ID = 'custom'
PRESET_IDS: frozenset[str] = frozenset(t.id for t in PRESETS)
VALID_THEME_IDS: frozenset[str] = PRESET_IDS | {CUSTOM_ID}


def catalog() -> tuple[OverlayTheme, ...]:
    return PRESETS


def get(theme_id: str) -> OverlayTheme | None:
    for theme in PRESETS:
        if theme.id == theme_id:
            return theme
    return None


def parse_hex(value: str) -> RGB:
    """'#D9713F' -> (0.851, 0.443, 0.247). Raises BadColor on anything else."""
    match = _HEX_RE.match((value or '').strip())
    if match is None:
        raise BadColor(f'{value!r} is not a hex colour like #D9713F')
    digits = match.group(1)
    if len(digits) == 3:
        digits = ''.join(c * 2 for c in digits)
    return tuple(int(digits[i:i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore[return-value]


def normalise_hex(value: str) -> str:
    """Canonical '#RRGGBB', so the same colour never persists two ways."""
    r, g, b = parse_hex(value)
    return f'#{round(r * 255):02X}{round(g * 255):02X}{round(b * 255):02X}'


def clean_colors(values: Iterable[Any]) -> list[str]:
    """Validate and canonicalise a user-supplied colour list.

    Raises BadColor on the first bad entry rather than silently dropping it —
    a colour that vanishes without explanation reads as the app ignoring you.
    """
    out: list[str] = []
    for value in list(values)[:MAX_COLORS]:
        out.append(normalise_hex(str(value)))
    return out


def _luminance(rgb: RGB) -> float:
    """Relative luminance, sRGB weights. Good enough to pick a rim tone."""
    r, g, b = rgb
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def auto_rim(colors: Sequence[RGB]) -> RGB:
    """The tone that keeps a multi-colour glyph legible on an unknown backdrop.

    Mean luminance, opposite tone. A red-and-white mark averages bright, so it
    gets a dark rim; black-and-red averages dark and gets a light one.
    """
    if not colors:
        return TONE_LIGHT
    mean = sum(_luminance(c) for c in colors) / len(colors)
    return TONE_DARK if mean > 0.5 else TONE_LIGHT


def resolve(theme_id: str, custom: Sequence[str] | None = None) -> tuple[list[RGB], RGB] | None:
    """`(column colours, rim)` for a theme, or None when it is adaptive.

    None is the signal to `overlay.py` that it should keep doing what it always
    did — follow the system appearance and `OVERLAY_CONTRAST`. Unknown ids and
    an empty custom list both fall back to adaptive rather than raising: a bad
    settings file must never be able to leave someone without a glyph.
    """
    theme_id = (theme_id or 'ink').strip().lower()
    if theme_id == CUSTOM_ID:
        raw = list(custom or [])
    else:
        theme = get(theme_id)
        if theme is None or theme.adaptive:
            return None
        raw = list(theme.colors)

    colors: list[RGB] = []
    for value in raw[:MAX_COLORS]:
        try:
            colors.append(parse_hex(str(value)))
        except BadColor:
            continue
    if not colors:
        return None
    return colors, auto_rim(colors)


def to_dict(theme: OverlayTheme) -> dict[str, Any]:
    return {
        'id': theme.id,
        'name': theme.name,
        'colors': list(theme.colors),
        'blurb': theme.blurb,
        'adaptive': theme.adaptive,
    }


# ── The live overlay ────────────────────────────────────────────────────────
# The HTTP layer needs to reach the running glyph so a colour lands while the
# customiser is open rather than on the next launch. The engine registers it at
# startup, the same way `model_manager` holds the live controller. Kept here,
# in a module with no AppKit import, so `server/api.py` never has to pull
# AppKit in just to change a colour (and so the tests do not either).

_live: Any = None


def register_overlay(overlay: Any) -> None:
    global _live
    _live = overlay


def live_overlay() -> Any:
    return _live
