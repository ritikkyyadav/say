// The Say mark: a base with six stepped columns on a 30×12 grid.
//
// The geometry is the same one `gen_logo.py` owns — column widths are
// genuinely uneven (nearly 1.7:1 widest to narrowest), so there is no uniform
// pitch to divide by and the pairs are written out. Keep it 5:2. Anything
// that sets a square size on this turns it into a smear.
//
// The columns animate in two situations, and both are deliberate:
//
//  * `live` — a dictation is running. This is the app's only recording
//    indicator; the mark itself comes alive instead of a pill somewhere
//    saying the word "recording".
//  * `burst` — the mark was touched. A logo that answers when you poke it is
//    the difference between a picture of a waveform and a thing that listens.
//
// Both drive the same loop, so they can never fight over the rects.

import { useEffect, useRef, useState } from 'react';

/** (x, width, height) per column, left to right, on the 30×12 grid. */
const COLUMNS: [number, number, number][] = [
  [0, 5.75, 3.25],
  [5.75, 4.75, 7.0],
  [10.5, 6.75, 12.0],
  [17.25, 4.25, 9.75],
  [21.5, 4.0, 7.0],
  [25.5, 4.5, 3.25],
];

const H = 12;

export function Mark({ live = false, burst = 0 }: { live?: boolean; burst?: number }) {
  const ref = useRef<SVGSVGElement | null>(null);
  const [bursting, setBursting] = useState(false);

  // A burst is a one-shot flourish: bump the counter and it runs for a beat.
  useEffect(() => {
    if (!burst) return;
    setBursting(true);
    const t = setTimeout(() => setBursting(false), 2600);
    return () => clearTimeout(t);
  }, [burst]);

  const animating = live || bursting;

  useEffect(() => {
    const svg = ref.current;
    if (!svg) return;
    const rects = Array.from(svg.querySelectorAll('rect'));

    const rest = () =>
      rects.forEach((r, i) => {
        const h = COLUMNS[i][2];
        r.setAttribute('height', String(h));
        r.setAttribute('y', String(H - h));
      });

    if (!animating || window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) {
      rest();
      return;
    }

    let raf = 0;
    let t = 0;
    const start = performance.now();
    const tick = (now: number) => {
      t += 0.09;
      // A burst settles back to rest instead of stopping dead mid-swing,
      // which would read as the animation breaking rather than finishing.
      const age = (now - start) / 2600;
      const amp = live ? 1 : Math.max(0, 1 - Math.pow(Math.min(1, age), 2));
      rects.forEach((r, i) => {
        const base = COLUMNS[i][2];
        // Each column runs at its own rate so the row reads as a membrane
        // rather than six bars pulsing in lockstep.
        const swing = 0.55 * amp * Math.abs(Math.sin(t * (1 + i * 0.31) + i));
        const h = base * (1 - 0.55 * amp + swing);
        r.setAttribute('height', h.toFixed(2));
        r.setAttribute('y', (H - h).toFixed(2));
      });
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(raf);
      rest();
    };
  }, [animating, live]);

  return (
    <svg ref={ref} viewBox="0 0 30 12" aria-hidden="true">
      {COLUMNS.map(([x, w, h], i) => (
        <rect key={i} x={x} y={H - h} width={w} height={h} rx="0.3" />
      ))}
    </svg>
  );
}
