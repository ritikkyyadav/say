// "What we noticed" — the rotating panel of things the corpus knows about the
// person using it.
//
// The copy arrives fully composed from the engine; this only draws it. Two
// viz kinds cover every insight: a dot grid that makes "one word in 27"
// literal, and a small bar row for anything ranked. Both animate purely off
// the `.slide.on` class, so a slide replays every time it comes round without
// any timer choreography to get out of step.

import { useCallback, useEffect, useMemo, useState } from 'react';
import type { Insight } from '../lib/api';
import { markup } from '../lib/markup';

const DWELL_MS = 7400;

function Dots({ every, rows, caption, label }: { every: number; rows: number; caption: string; label: string }) {
  // One hit per `every`, but shifted a few columns each row — otherwise the
  // marks stack into a vertical stripe at the edge and read as a column
  // rather than as a habit scattered through everything you say.
  const cells = useMemo(() => {
    const cols = Math.max(2, Math.min(40, every));
    const shift = Math.max(1, Math.round(cols / 3.7));
    const offset = Math.floor(cols * 0.34);
    return Array.from({ length: cols * rows }, (_, k) => ({
      hit: k % cols === (offset + Math.floor(k / cols) * shift) % cols,
      cols,
    }));
  }, [every, rows]);

  const cols = cells[0]?.cols ?? 1;

  return (
    <>
      <div className="dots" style={{ gridTemplateColumns: `repeat(${cols}, 1fr)` }}>
        {cells.map((c, k) => (
          <div
            key={k}
            className={c.hit ? 'dot hit' : 'dot'}
            style={{ transitionDelay: `${k * 7}ms` }}
          />
        ))}
      </div>
      <div className="dots-cap">
        <span>{caption}</span>
        <b>{label}</b>
      </div>
    </>
  );
}

function Bars({ rows, unit }: { rows: [string, number][]; unit?: string }) {
  const max = Math.max(...rows.map(([, v]) => v), 1);
  return (
    <div className="mbars">
      {rows.map(([label, value], i) => (
        <div className="mbar" key={`${label}-${i}`}>
          <span className="k" title={label}>{label}</span>
          <span className="t">
            <i
              className="f"
              style={
                {
                  '--w': `${Math.max(2, Math.round((value / max) * 100))}%`,
                  transitionDelay: `${140 + i * 110}ms`,
                } as React.CSSProperties
              }
            />
          </span>
          <span className="n">
            {value.toLocaleString('en-US')}
            {unit ?? ''}
          </span>
        </div>
      ))}
    </div>
  );
}

export function Discovery({ insights }: { insights: Insight[] }) {
  const [cur, setCur] = useState(0);
  const [paused, setPaused] = useState(false);

  const go = useCallback(
    (next: number) => {
      if (insights.length === 0) return;
      setCur(((next % insights.length) + insights.length) % insights.length);
    },
    [insights.length],
  );

  useEffect(() => {
    if (paused || insights.length < 2) return;
    const t = setTimeout(() => go(cur + 1), DWELL_MS);
    return () => clearTimeout(t);
  }, [cur, paused, insights.length, go]);

  if (insights.length === 0) return null;

  return (
    <div
      className="disc"
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
    >
      <div className="disc-top">
        <span className="disc-tag">
          <i />
          What we noticed
        </span>
        <div className="disc-nav">
          <button onClick={() => go(cur - 1)} aria-label="Previous">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
              <path d="M15 6l-6 6 6 6" />
            </svg>
          </button>
          <button onClick={() => go(cur + 1)} aria-label="Next">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
              <path d="M9 6l6 6-6 6" />
            </svg>
          </button>
        </div>
      </div>

      <div className="disc-stage">
        {insights.map((insight, i) => (
          <div key={insight.id} className={i === cur ? 'slide on' : 'slide'}>
            <h3>{markup(insight.headline)}</h3>
            <div className="body">{markup(insight.body)}</div>
            <div className="viz">
              {insight.viz.kind === 'dots' ? (
                <Dots
                  every={insight.viz.every}
                  rows={insight.viz.rows}
                  caption={insight.viz.caption}
                  label={insight.viz.label}
                />
              ) : (
                <Bars rows={insight.viz.rows} unit={insight.viz.unit} />
              )}
            </div>
          </div>
        ))}
      </div>

      <div className="disc-dots">
        {insights.map((insight, i) => (
          <button
            key={insight.id}
            className={i === cur ? 'on' : i < cur ? 'done' : ''}
            onClick={() => go(i)}
            aria-label={`Insight ${i + 1}`}
          >
            <i />
          </button>
        ))}
      </div>
    </div>
  );
}
