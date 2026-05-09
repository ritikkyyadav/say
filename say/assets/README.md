# Say — Asset Directory

Drop brand assets here. The app loads them at runtime; nothing else
needs to change when you swap a file. All paths are relative to
`say/assets/`.

## Required for full-fidelity branding

| File | Purpose | Recommended size |
| --- | --- | --- |
| `logo.png` (or `logo@2x.png`) | Full-color bear-with-microphone logo. Used in the first-run wizard, Settings header, and the About dialog. | 512 × 512 PNG, transparent or sky-blue background |
| `menubar-template.pdf` (or `menubar-template.png`) | Monochrome glyph for the macOS menu bar. The system tints it to match light/dark menu bars. | 18 × 18 PDF, pure black on transparent |

Higher-resolution `@2x` and `@3x` variants are loaded automatically if
present (e.g. `logo@2x.png`).

## Fallbacks when an asset is missing

- `logo.png` missing → wizard / Settings show a generated bear-face
  glyph (three brown circles, drawn at runtime). Looks fine; not
  pixel-perfect with the real mark.
- `menubar-template.pdf` missing → the menu-bar icon falls back to the
  emoji set in `config.MENU_BAR_ICON`. Functional, less branded.

## Generating the menubar template from a full-color logo

```bash
# In assets/, with the source logo as logo.png:
sips -z 36 36 logo.png --out _logo36.png            # downscale to @2x
# Then in any vector editor: trace the silhouette, fill pure black,
# export as menubar-template.pdf at 18×18 points.
```

Pure-black template images are required — macOS handles the inversion
for dark menu bars. Multi-color icons in the menu bar look out of place
and break in dark mode.
