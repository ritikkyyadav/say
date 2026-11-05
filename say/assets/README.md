# Say — Asset Directory

Everything here is **generated**, not hand-drawn. Run `gen_logo.py` at the
repo root to rebuild the whole set from one vector definition of the mark:

```bash
python3 gen_logo.py            # rewrites this directory and desktop/ icons
python3 gen_logo.py --preview  # writes logo-preview.png, touches nothing else
```

`gen_logo.py` needs Pillow, which the `say` venv does not carry — run it
with a Python that has it (e.g. Homebrew's `/opt/homebrew/bin/python3`).

## The mark

A continuous base with six stepped columns standing on it: a frozen frame
of the dictation overlay, which draws the same silhouette live at 60 fps
from `waveform.py`. Ink `#141413` on a book-cloth `#faf9f5` squircle.

The one property every consumer has to respect is that **the mark is 5:1**
— far wider than it is tall. Squaring it off squashes it into a smear.
`brand.menubar_template()` scales it by height for that reason, and
`app.py` has to undo the 20×20 that rumps forces on every status icon.

## Files

| File | Purpose | Size |
| --- | --- | --- |
| `logo.png` / `logo@2x.png` | Full-colour tile: cream squircle, hairline edge, ink mark. Used by the first-run wizard, the Settings header, and the desktop sidebar. | 512 / 1024 px |
| `Say.icns` | The `.app` bundle icon, wired up in `Say.spec`. Ten representations, 16 → 1024 px. | — |
| `menubar-template.pdf` | Monochrome menu-bar glyph. **Vector** — at 7 pt tall there is no resampling budget to spare. Loaded in preference to the PNG. | 35 × 7 pt |
| `menubar-template.png` | Raster fallback if the PDF goes missing. | 105 × 21 px (@3x) |

`Say.spec` ships this whole directory into the bundle. Before that was
true the packaged app had no logo at all — `brand.py` found nothing and
every surface silently fell back to its runtime-drawn placeholder.

## Fallbacks when an asset is missing

- `logo.png` missing → the wizard draws `_MarkPlaceholder`, the same
  six-column silhouette in NSBezierPath. Correct shape, no tile.
- `menubar-template.*` missing → the menu bar falls back to a drawn
  microphone glyph. Functional, off-brand.

## Small sizes

Icons at or below 128 px are drawn on the destination pixel grid rather
than supersampled (`snapped_mark_rects`), with a one-pixel floor per column
so the base never disappears. At 16 px the mark is only two pixels tall; a
supersampled fill turns it into a grey smear. The reduction filter is
`Image.BOX`, never `LANCZOS` — Lanczos rings on the mark's long horizontal
edges and leaves a white seam between the base and the columns above it.
