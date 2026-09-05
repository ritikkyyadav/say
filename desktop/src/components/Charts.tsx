// The small charts on the Home page. All of them animate from the `.in` class
// their `<Reveal>` wrapper adds, so nothing here needs a timer.

import type { CSSProperties } from 'react';
import type { DesktopUsageRow, HeatmapCell, WordStat } from '../lib/api';
import { appLabel } from '../lib/appNames';
import { fmt, hm } from '../lib/format';
import { Reveal } from '../lib/motion';

const w = (pct: number): CSSProperties => ({ '--w': `${pct}%` } as CSSProperties);

// ── Sparkline ───────────────────────────────────────────────────────────

export function Spark({ data }: { data: number[] }) {
  if (data.length < 2) return null;
  const max = Math.max(...data, 1);
  const W = 76;
  const H = 26;
  const pts = data.map((v, i) => [
    (i / (data.length - 1)) * W,
    H - 2 - (v / max) * (H - 5),
  ]);
  const line = pts.map((p, i) => `${i ? 'L' : 'M'}${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(' ');
  return (
    <svg className="spark" viewBox={`0 0 ${W} ${H}`} aria-hidden="true">
      <path className="fillp" d={`${line} L${W} ${H} L0 ${H} Z`} />
      <path d={line} />
    </svg>
  );
}

// ── Ranked word / phrase list ───────────────────────────────────────────

export function WordList({
  title,
  subtitle,
  badge,
  rows,
  kind,
  footer,
}: {
  title: string;
  subtitle: string;
  badge?: string;
  rows: WordStat[];
  kind: 'word' | 'phrase';
  footer?: React.ReactNode;
}) {
  const max = Math.max(...rows.map((r) => r.count), 1);
  // The three or four things you say constantly are the point; the long tail
  // is context. Colouring only the top of the distribution says which is which
  // without adding a legend.
  const loud = rows.length ? Math.max(rows[0].count * 0.25, 1) : 1;

  return (
    <Reveal className="card lift" style={{ display: 'flex', flexDirection: 'column' }}>
      <div className="card-h" style={{ padding: '20px 22px 0' }}>
        <div>
          <div className="card-t">{title}</div>
          <div className="card-s">{subtitle}</div>
        </div>
        {badge ? <span className="eyebrow">{badge}</span> : null}
      </div>
      <div className="wordlist" style={{ flex: 1 }}>
        {rows.length === 0 ? (
          <div className="card-s" style={{ padding: '18px 0' }}>
            Nothing has come back often enough yet.
          </div>
        ) : (
          rows.map((r) => (
            <div className={r.count < loud ? 'wrow dim' : 'wrow'} key={r.word}>
              <div className="w">
                {r.word}
                {kind === 'word' ? (
                  <s>in {Math.round(r.spread * 100)}% of dictations</s>
                ) : null}
              </div>
              <div className="wtrack">
                <div className="wfill" style={w(Math.max(2, (r.count / max) * 100))} />
              </div>
              <div className="c">
                <b>{fmt(r.count)}</b>
                <br />
                <span style={{ fontSize: 11 }}>
                  {kind === 'word' ? `1 in ${r.per}` : 'times'}
                </span>
              </div>
            </div>
          ))
        )}
      </div>
      {footer ? <div style={{ padding: '0 22px 20px' }}>{footer}</div> : null}
    </Reveal>
  );
}

// ── Speaking vs typing ──────────────────────────────────────────────────

export function SavedVsTyping({
  words,
  speakingSeconds,
  typingSeconds,
  wpm,
}: {
  words: number;
  speakingSeconds: number;
  typingSeconds: number;
  wpm: number;
}) {
  const saved = Math.max(0, typingSeconds - speakingSeconds);
  const s = hm(speakingSeconds);
  const t = hm(typingSeconds);
  const g = hm(saved);
  const share = typingSeconds > 0 ? (speakingSeconds / typingSeconds) * 100 : 0;

  return (
    <Reveal className="card lift saved">
      <div className="card-t">Speaking vs. typing</div>
      <div className="card-s">The same {fmt(words)} words, two ways.</div>

      <div className="sbar">
        <div className="top">
          <b>Typing them</b>
          <span>at 40 wpm — a good office pace</span>
        </div>
        <div className="t">
          <div className="f type" style={w(100)} />
        </div>
        <div className="cap">
          {t.h} h {t.m} m
        </div>
      </div>

      <div className="sbar">
        <div className="top">
          <b>Saying them</b>
          <span>at your {Math.round(wpm)} wpm</span>
        </div>
        <div className="t">
          <div className="f talk" style={w(Math.max(3, share))} />
        </div>
        <div className="cap">
          {s.h} h {s.m} m
        </div>
      </div>

      <div className="saved-out">
        <div>
          <div className="eyebrow" style={{ marginBottom: 6 }}>
            You got back
          </div>
          <div className="num big">
            {g.h}h {g.m}m
          </div>
        </div>
        <p>
          {g.h >= 8
            ? 'A full working day, recovered one held key at a time.'
            : 'Recovered one held key at a time.'}
        </p>
      </div>
    </Reveal>
  );
}

// ── Heatmap ─────────────────────────────────────────────────────────────

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

function level(count: number): string {
  if (count === 0) return '';
  if (count < 3) return 'l1';
  if (count < 8) return 'l2';
  if (count < 25) return 'l3';
  return 'l4';
}

export function Heatmap({ cells, weeks = 20 }: { cells: HeatmapCell[]; weeks?: number }) {
  const shown = cells.slice(-weeks * 7);
  if (shown.length === 0) return null;

  const first = new Date(`${shown[0].date}T00:00:00`);
  // Monday-first rows, so the leading partial week is padded rather than
  // shifting every subsequent column by a day.
  const pad = (first.getDay() + 6) % 7;

  const active = shown.filter((c) => c.count > 0).length;
  const best = shown.reduce((a, b) => (b.count > a.count ? b : a), shown[0]);

  let run = 0;
  let longest = 0;
  for (const c of shown) {
    run = c.count > 0 ? run + 1 : 0;
    longest = Math.max(longest, run);
  }

  const columns = Math.ceil((pad + shown.length) / 7);
  const monthLabels = Array.from({ length: columns }, (_, col) => {
    const idx = col * 7 - pad;
    if (idx < 0) return '';
    const d = new Date(`${shown[Math.min(idx, shown.length - 1)].date}T00:00:00`);
    const prevIdx = idx - 7;
    if (prevIdx < 0) return MONTHS[d.getMonth()];
    const prev = new Date(`${shown[prevIdx].date}T00:00:00`);
    return d.getMonth() !== prev.getMonth() ? MONTHS[d.getMonth()] : '';
  });

  const started = shown.find((c) => c.count > 0);

  return (
    <Reveal className="card lift heat">
      <div className="card-t">Your rhythm</div>
      <div className="card-s">Every day you have dictated, and how much.</div>

      <div className="heatwrap">
        <div>
          <div className="heatmonths">
            {monthLabels.map((m, i) => (
              <span key={i}>{m}</span>
            ))}
          </div>
          <div className="heatgrid">
            {Array.from({ length: pad }, (_, i) => (
              <div key={`pad-${i}`} className="hc" style={{ opacity: 0 }} />
            ))}
            {shown.map((c, i) => (
              <div
                key={c.date}
                className={`hc ${level(c.count)}`}
                style={{ transitionDelay: `${Math.min(600, i * 3)}ms` }}
                title={c.count ? `${c.date}: ${c.count} dictation${c.count > 1 ? 's' : ''}` : c.date}
              />
            ))}
          </div>
        </div>
        <div className="heatstats">
          <div className="heatstat">
            <b className="tnum">{active}</b>
            <span>active days</span>
          </div>
          <div className="heatstat">
            <b className="tnum">{longest}</b>
            <span>longest run</span>
          </div>
          <div className="heatstat">
            <b className="tnum">{fmt(best.count)}</b>
            <span>best day</span>
          </div>
        </div>
      </div>

      <div className="heat-legend">
        <span>
          {weeks} weeks
          {started ? ` · you started ${new Date(`${started.date}T00:00:00`).toLocaleDateString('en', { day: 'numeric', month: 'long' })}` : ''}
        </span>
        <span className="sc">
          Quiet
          <i style={{ background: 'var(--card-2)' }} />
          <i style={{ background: '#F0D9CB' }} />
          <i style={{ background: '#E9B694' }} />
          <i style={{ background: '#DE8F5F' }} />
          <i style={{ background: 'var(--accent-deep)' }} />
          Loud
        </span>
      </div>
    </Reveal>
  );
}

// ── Hour of day ─────────────────────────────────────────────────────────

function hourLabel(h: number): string {
  if (h === 0) return 'midnight';
  if (h === 12) return 'noon';
  return `${h % 12 || 12}${h < 12 ? 'am' : 'pm'}`;
}

export function HourBars({ hours }: { hours: number[] }) {
  const max = Math.max(...hours, 1);
  const peak = hours.indexOf(max);

  return (
    <Reveal className="card lift hours">
      <div className="card-t">When your voice shows up</div>
      <div className="card-s">
        Words spoken by hour of day. Your peak is{' '}
        <b style={{ color: 'var(--accent-deep)' }}>{hourLabel(peak)}</b> — {fmt(max)} words live
        there.
      </div>
      <div className="hbars">
        {hours.map((v, h) => (
          <div
            key={h}
            className={`hb${v === max ? ' peak' : v > max * 0.35 ? ' lit' : ''}`}
            style={{ '--h': `${Math.max(2, (v / max) * 100)}%` } as CSSProperties}
          >
            <span className="tip">
              {fmt(v)} words · {hourLabel(h)}
            </span>
          </div>
        ))}
      </div>
      <div className="haxis">
        <span>12a</span>
        <span>6a</span>
        <span>12p</span>
        <span>6p</span>
        <span>11p</span>
      </div>
    </Reveal>
  );
}

// ── Where the words land ────────────────────────────────────────────────

// Muted, distinguishable, and none of them competing with the accent.
const APP_COLORS = ['#2E7D74', '#C86C48', '#B8503A', '#3C6B57', '#4A6FA5', '#8A8478'];

export function AppShare({ rows }: { rows: DesktopUsageRow[] }) {
  const top = rows.slice(0, 6);
  return (
    <Reveal className="card lift apps">
      <div className="card-h">
        <div>
          <div className="card-t">Where the words land</div>
          <div className="card-s">Share of everything you have dictated</div>
        </div>
      </div>
      <div className="applist">
        {top.length === 0 ? (
          <div className="card-s">No dictations yet.</div>
        ) : (
          top.map((row, i) => {
            const name = appLabel(row.target_app);
            return (
              <div className="arow" key={row.target_app || i}>
                <div className="aico" style={{ background: APP_COLORS[i % APP_COLORS.length] }}>
                  {name.slice(0, 1).toUpperCase()}
                </div>
                <div className="meta">
                  <div className="nm">{name}</div>
                  <div className="tr">
                    <span className="fl" style={w(Math.max(2, row.share * 100))} />
                  </div>
                </div>
                <div className="pc">
                  {(row.share * 100).toFixed(row.share < 0.1 ? 1 : 0)}%
                  <s>{fmt(row.count)} dictations</s>
                </div>
              </div>
            );
          })
        )}
      </div>
    </Reveal>
  );
}
