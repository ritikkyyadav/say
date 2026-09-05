// A faithful preview of the dictation glyph, drawn in the browser.
//
// It has to be faithful in two specific ways or it is worse than nothing:
//
//  * **One shape, not twelve bars.** The real overlay draws a single closed
//    polygon and paints the colours through it as a clip, so the columns share
//    edges and stand on one continuous base. A preview made of twelve separate
//    rects would show gaps the real thing does not have, and every colour pair
//    would look wrong in a way the user could not fix.
//
//  * **The rim.** On the real glyph a rim in the opposite tone is what keeps a
//    colour legible against an unknown backdrop. Previewing without it would
//    let someone pick white-on-white and only discover the problem in use.
//
// The motion is the sweep the engine shows while it is decoding — the one
// animation that needs no microphone.

import { useEffect, useRef } from 'react';
import { columnColors, Pattern } from '../lib/glyphPattern';

const BANDS = 12;
const COL_W = 7.5;
const BASE_H = 2.5;
const BAR_H = 16.5;
const W = BANDS * COL_W;
const H = BASE_H + BAR_H;
const RIM = 0.75;

const TONE_DARK = '#101218';
const TONE_LIGHT = '#F6F7FA';

function luminance(hex: string): number {
  const h = hex.replace('#', '');
  const full = h.length === 3 ? h.split('').map((c) => c + c).join('') : h;
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(full.slice(i, i + 2), 16) / 255);
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** Mirrors overlay_theme.auto_rim: mean luminance, opposite tone. */
export function autoRim(colors: string[]): string {
  if (colors.length === 0) return TONE_LIGHT;
  const mean = colors.reduce((a, c) => a + luminance(c), 0) / colors.length;
  return mean > 0.5 ? TONE_DARK : TONE_LIGHT;
}

/** The closed polygon for the whole mark, as an SVG path. */
function silhouette(heights: number[]): string {
  const pts: [number, number][] = [[0, H]];
  heights.forEach((v, i) => {
    const top = H - (BASE_H + Math.max(0, Math.min(1, v)) * BAR_H);
    pts.push([i * COL_W, top], [(i + 1) * COL_W, top]);
  });
  pts.push([W, H]);
  return pts.map(([x, y], i) => `${i ? 'L' : 'M'}${x.toFixed(2)} ${y.toFixed(2)}`).join(' ') + ' Z';
}

export function GlyphPreview({
  colors,
  adaptive,
  backdrop,
  pattern = 'pulse',
  height = undefined,
}: {
  /** Distributed across the columns. Empty means the adaptive (system) glyph. */
  colors: string[];
  adaptive: boolean;
  backdrop: 'light' | 'dark' | 'photo';
  pattern?: Pattern;
  /** Rendered width in px; the mark keeps its 5:2-ish proportion. */
  height?: number;
}) {
  const pathRef = useRef<SVGPathElement | null>(null);
  const rimRef = useRef<SVGPathElement | null>(null);
  const colsRef = useRef<(SVGRectElement | null)[]>([]);

  // `inkColors` is read inside the frame loop; a ref keeps the loop from
  // being torn down and restarted every time the palette changes.
  const paletteRef = useRef<string[]>([]);
  const patternRef = useRef<Pattern>(pattern);

  useEffect(() => {
    let raf = 0;
    let t = 0;
    const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;

    const tick = () => {
      t += reduced ? 0 : 0.028;
      // Not a single travelling hump: that is periodic, and a periodic
      // stimulus makes every audio-driven pattern look periodic too, which
      // is the exact thing being compared here. Three detuned components
      // give a spectrum that never repeats — the way speech does not.
      const heights = Array.from({ length: BANDS }, (_, i) => {
        const a = Math.sin(t * 1.7 + i * 0.55);
        const b = Math.sin(t * 0.61 + i * 1.31 + 1.7);
        const c = Math.sin(t * 2.53 - i * 0.29);
        return Math.min(1, Math.max(0.04, 0.42 + 0.3 * a + 0.22 * b + 0.16 * c));
      });

      const d = silhouette(heights);
      pathRef.current?.setAttribute('d', d);
      rimRef.current?.setAttribute('d', d);

      // Same colour rule the engine runs, on the same heights.
      const palette = paletteRef.current;
      if (palette.length) {
        const idx = columnColors(patternRef.current, palette.length, heights, t);
        colsRef.current.forEach((rect, i) => {
          if (rect) rect.setAttribute('fill', palette[idx[i] ?? 0]);
        });
      }

      if (!reduced) raf = requestAnimationFrame(tick);
    };
    tick();
    return () => cancelAnimationFrame(raf);
  }, []);

  // The adaptive theme follows the system; on this preview the backdrop is
  // what stands in for it, so it is shown the way it would actually appear.
  const inkColors = adaptive
    ? [backdrop === 'light' ? TONE_DARK : TONE_LIGHT]
    : colors.length
      ? colors
      : [TONE_DARK];
  const rim = adaptive
    ? backdrop === 'light'
      ? TONE_LIGHT
      : TONE_DARK
    : autoRim(inkColors);

  paletteRef.current = inkColors;
  patternRef.current = pattern;

  const id = `glyphclip-${inkColors.join('')}-${backdrop}-${pattern}`;

  return (
    <svg
      viewBox={`${-RIM * 2} ${-RIM * 2} ${W + RIM * 4} ${H + RIM * 4}`}
      style={{ width: height ?? 270, height: 'auto', display: 'block', overflow: 'visible' }}
      aria-label="Overlay preview"
    >
      <defs>
        <clipPath id={id}>
          <path ref={pathRef} d="" />
        </clipPath>
      </defs>

      {/* Beneath the ink: the same path, fattened, in the opposite tone. */}
      <path
        ref={rimRef}
        d=""
        fill={rim}
        stroke={rim}
        strokeWidth={RIM * 2}
        strokeLinejoin="miter"
      />

      {/* The colours, painted *through* the one shape — never as separate
          rectangles, so the columns share edges exactly as they do on screen. */}
      <g clipPath={`url(#${id})`}>
        {Array.from({ length: BANDS }, (_, i) => (
          <rect
            key={i}
            ref={(el) => {
              colsRef.current[i] = el;
            }}
            x={i * COL_W}
            y={-RIM * 2}
            width={COL_W}
            height={H + RIM * 4}
            fill={inkColors[i % inkColors.length]}
          />
        ))}
      </g>
    </svg>
  );
}
