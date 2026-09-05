// The overlay customiser.
//
// Picking a colour here is not a settings row — you are choosing what you will
// stare at every time you speak — so the page is built around seeing it: a
// live glyph on a backdrop you can switch between light, dark and something
// awkward, and a button that puts the real thing on your real screen.

import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, OverlayConfigPatch } from '../lib/api';
import { Reveal } from '../lib/motion';
import { GlyphPreview } from '../components/GlyphPreview';
import { Pattern } from '../lib/glyphPattern';

type Backdrop = 'light' | 'dark' | 'photo';

const DEFAULT_CUSTOM = ['#D9713F', '#17150F'];

export function Overlay() {
  const qc = useQueryClient();
  const cfg = useQuery({ queryKey: ['overlay'], queryFn: api.overlay });
  const [backdrop, setBackdrop] = useState<Backdrop>('light');
  const [custom, setCustom] = useState<string[]>([]);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    if (cfg.data) setCustom(cfg.data.colors.length ? cfg.data.colors : DEFAULT_CUSTOM);
  }, [cfg.data]);

  const save = useMutation({
    mutationFn: (patch: OverlayConfigPatch) => api.setOverlay(patch),
    onSuccess: (next) => {
      qc.setQueryData(['overlay'], next);
      setNotice(null);
    },
    onError: (e) => setNotice(e instanceof Error ? e.message : 'Could not save that'),
  });

  const flash = useMutation({
    mutationFn: () => api.previewOverlay(),
    onSuccess: (r) =>
      setNotice(r.shown ? 'Shown at the bottom of your screen.' : 'The glyph is not running right now.'),
  });

  const data = cfg.data;
  if (cfg.isError) {
    return (
      <div className="page">
        <header className="hero">
          <h1>Overlay</h1>
        </header>
        <div className="card err">
          <h3>Can&rsquo;t reach the dictation engine</h3>
          <p>Make sure Say is running, then try again.</p>
        </div>
      </div>
    );
  }
  if (!data) {
    return (
      <div className="page">
        <header className="hero">
          <h1>Overlay</h1>
        </header>
        <div className="card-s">Loading…</div>
      </div>
    );
  }

  const active = data.theme;
  const preset = data.presets.find((p) => p.id === active);
  const shownColors = active === 'custom' ? custom : (preset?.colors ?? []);
  const isAdaptive = active !== 'custom' && (preset?.adaptive ?? true);

  const pick = (id: string) => {
    if (id === 'custom') {
      save.mutate({ theme: 'custom', colors: custom.length ? custom : DEFAULT_CUSTOM, preview: true });
    } else {
      save.mutate({ theme: id, preview: true });
    }
  };

  const setColor = (i: number, value: string) => {
    const next = custom.map((c, k) => (k === i ? value.toUpperCase() : c));
    setCustom(next);
  };

  const commitCustom = (next: string[]) => {
    setCustom(next);
    save.mutate({ theme: 'custom', colors: next, preview: true });
  };

  return (
    <div className="page">
      <header className="hero">
        <h1>Make the glyph yours.</h1>
        <p>
          The mark that appears while you speak. Pick your colours, then pick how they land —
          by default the colour comes from <b>what you are actually saying</b>, so the twelve
          columns move as one field and never repeat the same arrangement twice.
        </p>
      </header>

      {/* ── The thing itself ── */}
      <Reveal className="card" style={{ padding: 14, marginBottom: 16 }}>
        <div className={`stage ${backdrop}`}>
          <div className="stage-pick">
            {(['light', 'dark', 'photo'] as Backdrop[]).map((b) => (
              <button
                key={b}
                className={backdrop === b ? 'on' : ''}
                onClick={() => setBackdrop(b)}
              >
                {b === 'photo' ? 'Busy' : b === 'light' ? 'Light' : 'Dark'}
              </button>
            ))}
          </div>
          <GlyphPreview
            colors={shownColors}
            adaptive={isAdaptive}
            backdrop={backdrop}
            pattern={data.pattern as Pattern}
          />
        </div>

        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: 16,
            padding: '14px 8px 4px',
            flexWrap: 'wrap',
          }}
        >
          <div>
            <div className="card-t">
              {active === 'custom' ? 'Your colours' : (preset?.name ?? 'Ink')}
            </div>
            <div className="card-s" style={{ maxWidth: '52ch' }}>
              {active === 'custom'
                ? 'A rim in the opposite tone is added automatically, so it stays readable whatever is behind it.'
                : (preset?.blurb ?? '')}
            </div>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            {notice ? <span className="card-s">{notice}</span> : null}
            <button
              className="btn gho"
              style={{ width: 'auto' }}
              onClick={() => flash.mutate()}
              disabled={flash.isPending || !data.show_overlay}
              title={data.show_overlay ? 'Show it on screen for a moment' : 'The overlay is switched off'}
            >
              Show it on screen
            </button>
          </div>
        </div>
      </Reveal>

      {/* ── Pattern ── */}
      <section className="sec" style={{ marginTop: 22 }}>
        <Reveal className="sec-h">
          <h2>How the colours land</h2>
          <p>
            Only <b>Stripe</b> is a fixed pattern — every other one takes its colour from
            what you are actually saying, so the row stays in step with itself but never
            repeats. Each tile below is running live.
          </p>
        </Reveal>
        <Reveal className="patterns">
          {data.patterns.map((p) => (
            <button
              key={p.id}
              className={data.pattern === p.id ? 'pat on' : 'pat'}
              onClick={() => save.mutate({ pattern: p.id, preview: true })}
              disabled={save.isPending}
            >
              <div className={`pat-stage ${backdrop}`}>
                <GlyphPreview
                  colors={shownColors.length ? shownColors : ['#CA152C', '#2C2C2E']}
                  adaptive={false}
                  backdrop={backdrop}
                  pattern={p.id as Pattern}
                  height={168}
                />
              </div>
              <h4>{p.name}</h4>
              <p>{p.blurb}</p>
            </button>
          ))}
        </Reveal>
      </section>

      {/* ── Presets ── */}
      <section className="sec" style={{ marginTop: 22 }}>
        <Reveal className="sec-h">
          <h2>Ready-made</h2>
          <p>Every one of these keeps a contrast rim, so none of them can disappear on you.</p>
        </Reveal>
        <Reveal className="themes">
          {data.presets.map((p) => (
            <button
              key={p.id}
              className={active === p.id ? 'theme on' : 'theme'}
              onClick={() => pick(p.id)}
              disabled={save.isPending}
            >
              <div className="sw-row">
                {Array.from({ length: 6 }, (_, i) => (
                  <i
                    key={i}
                    style={{
                      background: p.colors.length
                        ? p.colors[i % p.colors.length]
                        : 'var(--ink)',
                      height: `${[38, 62, 100, 82, 62, 38][i]}%`,
                      opacity: p.adaptive ? 0.85 : 1,
                    }}
                  />
                ))}
              </div>
              <h4>{p.name}</h4>
              <p>{p.blurb}</p>
            </button>
          ))}

          <button
            className={active === 'custom' ? 'theme on' : 'theme'}
            onClick={() => pick('custom')}
            disabled={save.isPending}
          >
            <div className="sw-row">
              {Array.from({ length: 6 }, (_, i) => (
                <i
                  key={i}
                  style={{
                    background: custom.length ? custom[i % custom.length] : 'var(--line)',
                    height: `${[38, 62, 100, 82, 62, 38][i]}%`,
                  }}
                />
              ))}
            </div>
            <h4>Your own</h4>
            <p>Up to {data.max_colors} colours, in the order they repeat.</p>
          </button>
        </Reveal>
      </section>

      {/* ── Custom colours ── */}
      <section className="sec">
        <Reveal className="card" style={{ padding: 22 }}>
          <div className="card-t">Your colours</div>
          <div className="card-s" style={{ marginBottom: 16 }}>
            The columns cycle through these left to right. Two reads as alternating slashes;
            three or more rotates.
          </div>
          <div className="swatches">
            {custom.map((c, i) => (
              <div className="swatch-wrap" key={i}>
                <button className="swatch" style={{ background: c }} title={c}>
                  <input
                    type="color"
                    value={c}
                    onChange={(e) => setColor(i, e.target.value)}
                    onBlur={() => commitCustom(custom)}
                    aria-label={`Colour ${i + 1}`}
                  />
                </button>
                {custom.length > 1 ? (
                  <button
                    className="x"
                    onClick={() => commitCustom(custom.filter((_, k) => k !== i))}
                    aria-label={`Remove colour ${i + 1}`}
                  >
                    ×
                  </button>
                ) : null}
              </div>
            ))}
            <button
              className="addswatch"
              disabled={custom.length >= data.max_colors}
              onClick={() => commitCustom([...custom, '#FDFCF8'])}
              aria-label="Add a colour"
            >
              +
            </button>
          </div>
          <div className="card-s" style={{ marginTop: 14 }}>
            {active === 'custom'
              ? 'These are live.'
              : 'Pick “Your own” above to use these.'}
          </div>
        </Reveal>
      </section>

      {/* ── The switch ── */}
      <section className="sec">
        <Reveal className="card" style={{ padding: '18px 22px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
            <button
              className={data.show_overlay ? 'sw on' : 'sw'}
              onClick={() => save.mutate({ show_overlay: !data.show_overlay })}
              disabled={save.isPending}
              role="switch"
              aria-checked={data.show_overlay}
              aria-label="Show the glyph"
            >
              <i />
            </button>
            <div>
              <div className="card-t">Show the glyph while I speak</div>
              <div className="card-s">
                Turn this off to dictate with no on-screen mark at all. It never takes focus
                either way.
              </div>
            </div>
          </div>
        </Reveal>
      </section>
    </div>
  );
}
