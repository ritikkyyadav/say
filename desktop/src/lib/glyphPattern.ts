// How the theme colours are distributed across the glyph's columns.
//
// **This mirrors `say/overlay_theme.py::column_colors` and has to stay exact.**
// The customiser's whole job is showing you what the real overlay will do, so
// a preview that colours differently from the engine is worse than no preview
// — you would tune against a picture of something that does not exist.
//
// The point of everything except `alternate` is that colour comes from the
// *audio*, not from the column index. Index never changes, so an index-based
// pattern is the same stripe in every frame of every dictation; a
// height-based one inherits the coupling the motion engine already applies
// across neighbouring bars, so the colours ripple as one field and never
// land the same way twice.

export type Pattern = 'pulse' | 'spark' | 'flow' | 'drift' | 'alternate';

/** Conjugate of the golden ratio — steps that never fall into a cycle. */
const PHI = 0.6180339887498949;

const PULSE_SCATTER = 0.5; // keeps a flat spectrum from collapsing to one colour
const FLOW_SPEED = 0.34; // rotations per second
const SPARK_SHARE = 0.3; // top fraction of bars that take the accent

const clamp01 = (v: number) => (v < 0 ? 0 : v > 1 ? 1 : v);

export function columnColors(
  pattern: Pattern,
  nColors: number,
  heights: number[],
  t = 0,
): number[] {
  const bands = heights.length;
  if (nColors <= 1 || bands === 0) return new Array(bands).fill(0);

  if (pattern === 'alternate') {
    return heights.map((_, i) => i % nColors);
  }

  if (pattern === 'drift') {
    return heights.map((_, i) => Math.floor(((i * PHI) % 1) * nColors) % nColors);
  }

  if (pattern === 'flow') {
    return heights.map(
      (_, i) => Math.floor(((i / bands + t * FLOW_SPEED) % 1) * nColors) % nColors,
    );
  }

  if (pattern === 'spark') {
    const keep = Math.max(1, Math.round(bands * SPARK_SHARE));
    const hot = new Set(
      heights
        .map((h, i) => [h, i] as const)
        .sort((a, b) => b[0] - a[0])
        .slice(0, keep)
        .map(([, i]) => i),
    );
    return heights.map((_, i) => (hot.has(i) ? nColors - 1 : 0));
  }

  // pulse — loudness, nudged by a fixed scatter.
  return heights.map((h, i) => {
    const mixed = clamp01(h) * (1 - PULSE_SCATTER) + ((i * PHI) % 1) * PULSE_SCATTER;
    return Math.min(nColors - 1, Math.floor(clamp01(mixed) * nColors));
  });
}
