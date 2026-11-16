// Entrance motion, shared by every card on the insights page.
//
// Everything animates from a single `.in` class landing on a `.rv` wrapper —
// bars grow, heatmap cells fade, dots pop — so the CSS owns the choreography
// and React only decides *when*. That keeps the stagger out of JavaScript and
// means a dropped frame cannot leave half a chart un-drawn.

import { ReactNode, useEffect, useRef, useState } from 'react';

/**
 * Adds `.in` the first time the element is on screen, then stops watching.
 *
 * Anything already within (or just below) the viewport at mount is revealed
 * outright rather than waited for. That is not an optimisation — relying on
 * the observer for above-the-fold content means a page whose cards are all
 * visible from the start can land with only the first one drawn and the rest
 * permanently at `opacity: 0`, which is what happened to the overlay page.
 * A card that fades in a beat early is a non-event; a blank page is not.
 *
 * The observer is then only responsible for what genuinely needs scrolling to,
 * and a missing observer reveals everything immediately.
 */
export function useReveal<T extends HTMLElement = HTMLDivElement>(delayMs = 0) {
  const ref = useRef<T | null>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (delayMs) el.style.transitionDelay = `${delayMs}ms`;

    const show = () => el.classList.add('in');

    if (typeof IntersectionObserver === 'undefined') {
      show();
      return;
    }

    // One frame, so the initial `opacity: 0` is painted and the transition
    // actually runs instead of the element simply appearing.
    const raf = requestAnimationFrame(() => {
      const rect = el.getBoundingClientRect();
      if (rect.top < window.innerHeight * 1.15) {
        show();
      }
    });

    const io = new IntersectionObserver(
      (entries) => {
        if (entries[0]?.isIntersecting) {
          show();
          io.disconnect();
        }
      },
      { threshold: 0.08, rootMargin: '0px 0px -4% 0px' },
    );
    io.observe(el);
    return () => {
      cancelAnimationFrame(raf);
      io.disconnect();
    };
  }, [delayMs]);
  return ref;
}

/** A revealing wrapper. `<Reveal className="card kpi">…</Reveal>` */
export function Reveal({
  children,
  className = '',
  delay = 0,
  ...rest
}: {
  children: ReactNode;
  className?: string;
  delay?: number;
} & React.HTMLAttributes<HTMLDivElement>) {
  const ref = useReveal<HTMLDivElement>(delay);
  return (
    <div ref={ref} className={`rv ${className}`} {...rest}>
      {children}
    </div>
  );
}

const prefersReducedMotion = () =>
  typeof window !== 'undefined' &&
  window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;

/**
 * A number that counts up to its value once, on mount.
 *
 * Mount is the right trigger here rather than scroll: these live in the
 * above-the-fold KPI row and only exist once the query has resolved, so the
 * component appearing *is* the moment the number becomes known.
 */
export function CountUp({
  value,
  format = (n: number) => Math.round(n).toLocaleString('en-US'),
  ms = 1150,
}: {
  value: number;
  format?: (n: number) => string;
  ms?: number;
}) {
  const [shown, setShown] = useState(() => (prefersReducedMotion() ? value : 0));

  useEffect(() => {
    if (prefersReducedMotion()) {
      setShown(value);
      return;
    }
    let raf = 0;
    const t0 = performance.now();
    const tick = (now: number) => {
      const p = Math.min(1, (now - t0) / ms);
      // easeOutQuart: fast enough to feel instant, slow enough to read.
      setShown(value * (1 - Math.pow(1 - p, 4)));
      if (p < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [value, ms]);

  return <>{format(shown)}</>;
}
