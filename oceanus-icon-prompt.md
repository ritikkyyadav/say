# Oceanus — App Icon Prompt (Savoir DNA)

Icon-only mark for the macOS dictation app, built on the Savoir brand system.
Concept: **a waveform whose center bar is the Savoir block cursor** — voice + "a datum, set down."
This deliberately fixes the brand's own flagged weakness (a lone teal bar on a tile is generic). The waveform makes it ownable; the solid center cursor keeps it unmistakably Savoir.

---

## ▶ Paste this into ChatGPT

```
Design a minimalist macOS app icon — a single flat, vector-style mark, no text and no letters anywhere.

Canvas: a rounded-square (squircle) tile filled with a cool near-black charcoal ink, hex #14161A — not pure black. Across the tile add a very faint engineering dot-grid texture, barely visible, like drafting paper.

Centered on the tile: a small audio waveform made of five slim vertical bars with perfectly square corners (no rounded caps), evenly spaced, their heights forming a center-weighted bell — short, taller, tallest, taller, short. The bars are a precise petrol-teal, hex #17A0A8. The single center bar is the focal point: slightly wider and fully solid, like a terminal text cursor — a placed datum.

Style: instrument-grade, engineering-exact, declarative. Flat color only, hairline precision. No drop shadow, no outer glow, no gradient mesh, no 3D bevel, no neon, no skeuomorphism. Cool, measured, technical — the feel of an instrument housing, not a consumer app.

Color discipline: only the charcoal ink and the one teal accent. Nothing warm, no cream, no purple-blue "AI" gradients.

Format: square 1:1, crisp edges, app-icon composition with comfortable padding around the mark.
```

---

## Variants (swap one block in the prompt above)

**A · Waveform on an axis** — more instrument-grade. Replace the "Centered on the tile" sentence with:
> Centered on the tile: five slim square-cornered vertical bars sitting on a thin horizontal hairline rule (a measurement axis), heights forming a center-weighted bell, in teal #17A0A8; the center bar is wider and solid like a text cursor.

**B · Pure cursor, minimal** — closest to the original Savoir tile, de-genericized with a baseline tick. Replace the same sentence with:
> Centered on the tile: a single tall vertical block cursor in teal #17A0A8, square corners, solid, resting on a short horizontal hairline tick — like a datum point marked on a drawing.

---

## Spec cheatsheet (for tweaking or handing to a designer)

| Token | Value | Role |
|---|---|---|
| Ink ground | `#14161A` | tile background (cool near-black, never pure black) |
| Signal teal | `#17A0A8` | the waveform / cursor on the dark tile |
| Datum teal | `#0E5E63` | deeper teal if you want the accent on a *light* tile instead |
| Tile radius | 6px (squircle) | only place a 6px radius is allowed in the system |
| Bars | 5 (or 7), square corners | center-weighted bell envelope |
| Center bar | wider + solid | this is the block cursor — the brand mark |

---

## Do NOT (Savoir reject list)

Cream / warm tones · purple-blue AI gradients · glows or neon · drop shadows · rounded bar caps · 3D bevels or glass · microphone clichés · sound-ripple rings · stock illustration · any text or letters in the icon.

---

## Practical notes

- Generate 4–6, pick the cleanest, then trace it to vector (Illustrator / Figma / `vectorizer.ai`) for a crisp `.icns` — DALL·E output is raster and slightly soft at icon sizes.
- The icon is name-agnostic (no text), so it works whether the app reads as **Oceanus** or **Say**.
- For the matching **wordmark** ("Oceanus" + teal block cursor) or a full lockup, ask and I'll generate that prompt too.
