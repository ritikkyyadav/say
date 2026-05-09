# Logo Prompt — Claude-style Minimalist Circular Avatar

## Master prompt (paste into Midjourney, DALL-E 3, Imagen, Flux, or Ideogram)

> Minimalist circular profile avatar, single abstract geometric glyph centered on a flat solid color background. The glyph is a soft cream/off-white organic shape — an origami-style folded paper form, or a single curved petal, or an abstract eight-pointed asterisk-like burst — rendered as one continuous matte silhouette with no outlines, no gradients, no shadows, no text. Background is a muted desaturated earth tone — dusty rose, terracotta, sand, sage, or warm clay. Flat 2D vector style, balanced negative space, generous padding around the glyph (the glyph occupies ~55% of the circle). Modern AI-startup branding aesthetic, Anthropic-inspired, calm, premium, hand-crafted feel. Square 1024×1024, centered composition, perfectly circular crop. No photorealism, no 3D, no metallic effects, no people, no letters, no logos.

## Style anchors (append to the prompt for the model you're using)

**Midjourney v6:**
```
[master prompt above] --style raw --stylize 150 --ar 1:1 --v 6
```

**DALL-E 3 / GPT image:**
End the prompt with: `Style: flat vector, 2D, single continuous shape, matte finish, premium minimal branding.`

**Flux / Ideogram:**
Add: `flat illustration, vector art, single shape silhouette, brand mark, app icon`

## Color palette to rotate through (run the prompt 5–8 times, swap one color per run)

- Background `#B97A8A` + glyph `#F2E9DD` (dusty rose — matches your screenshot)
- Background `#C97B5B` + glyph `#F5EBDD` (terracotta)
- Background `#7A8B6F` + glyph `#F2EDE0` (sage)
- Background `#8A6F5C` + glyph `#F0E4D4` (clay)
- Background `#5C6F7A` + glyph `#EDE6DC` (slate blue)
- Background `#A89070` + glyph `#F4ECDD` (warm sand)
- Background `#6B5B73` + glyph `#EFE7DA` (muted plum)

## Glyph variants to try (pick one per generation)

1. Folded paper / origami corner — single fold creating two triangular planes
2. Abstract eight-point star burst (the Claude/Anthropic mark family)
3. A single curved leaf or petal
4. A soft rounded square with one corner peeled up
5. Two overlapping organic blobs forming a yin-yang-like balance
6. A single thick brushstroke arc or comma shape

Generate the same prompt with each glyph variant and pick the strongest output. The "folded paper corner" variant is closest to the icon in your screenshot.

## If you want it as code instead (SVG)

Hand this to Claude Code or any LLM:

> Generate a 512×512 SVG profile icon. A perfect circle filled `#B97A8A`. Centered inside, a single off-white `#F2E9DD` shape representing a folded paper corner: a rounded square rotated 12° with one corner folded inward, the fold visible as a thin shadow line at 5% opacity. The shape occupies 55% of the circle. No text, no other elements. Output only the SVG, no explanation.

## Reject and regenerate if the output has

- Any text or letterforms
- Drop shadows or 3D bevels
- More than one shape inside the circle
- Photographic textures
- Glow, sparkle, or particle effects
- A gradient on the background

These are the most common failures. Most generators will produce a usable result within 3–5 attempts.

## Final crop

Whatever model you use, run the result through a 1:1 circular mask in Figma, Photopea, or ImageMagick. Many generators put the "circle" inside a white square. You want the file itself to be circular (PNG with transparency) so it sits cleanly on any background as an avatar.

ImageMagick one-liner:
```bash
magick input.png \( +clone -threshold 101% -fill white -draw "circle 512,512 512,0" \) -channel-fx '| gray=>alpha' output.png
```
