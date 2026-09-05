// Models, rebuilt for someone who is not going to look up what 809M means.
//
// The old page listed seven cards with `speed 4 / accuracy 4 / 1.62 GB` and
// left you to work it out. This one asks what you are trying to do, points at
// one answer, and states the cost in things people actually have: minutes,
// disk, and how long you wait after letting go of the key. The four smaller
// models are still there, behind a disclosure, because almost nobody needs
// them and showing them first is what made this hard.

import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, ModelInfo } from '../lib/api';
import { bytes, downloadTime } from '../lib/format';
import { Reveal } from '../lib/motion';

type Intent = 'fast' | 'accurate' | 'multi';

const INTENTS: { key: Intent; title: string; blurb: string; icon: JSX.Element }[] = [
  {
    key: 'fast',
    title: 'Answer instantly',
    blurb: 'Short notes, chat replies, quick commands.',
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
        <path d="M13 2L4.5 13.5H11L10 22l8.5-11.5H12L13 2z" />
      </svg>
    ),
  },
  {
    key: 'accurate',
    title: 'Get every word right',
    blurb: 'Long thinking out loud, names, technical terms.',
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
        <circle cx="12" cy="12" r="8.5" />
        <circle cx="12" cy="12" r="3.6" />
      </svg>
    ),
  },
  {
    key: 'multi',
    title: "I don't only speak English",
    blurb: 'Hindi, Spanish, switching mid-sentence.',
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
        <circle cx="12" cy="12" r="9" />
        <path d="M3 12h18M12 3c2.6 3 2.6 15 0 18M12 3c-2.6 3-2.6 15 0 18" />
      </svg>
    ),
  },
];

/** How long you wait after letting go of the key — the only latency number
 *  anyone can feel. Derived from the catalogue's own speed rating so it can
 *  never contradict the meter beside it. */
function latency(speed: number): string {
  if (speed >= 5) return 'Text lands the moment you let go of the key.';
  if (speed >= 4) return 'About half a second behind you.';
  if (speed >= 3) return 'A second or so behind you.';
  return 'A second or two behind you.';
}

function quality(m: ModelInfo): string {
  // The bundled Moonshine backend takes no decoder prompt at all, so the
  // dictionary and custom terms are inert on it (see CLAUDE.md). Its
  // catalogue accuracy rating cannot express that, and without saying so the
  // page claims two models are both "excellent" and then asks you to upgrade
  // between them for no stated reason.
  if (m.backend === 'moonshine') {
    return 'Very good with everyday English. Your dictionary and custom terms do not reach it, so names and technical words are more of a guess.';
  }
  const base =
    m.accuracy >= 5
      ? 'The best transcription that exists offline — rare words, noisy rooms, strong accents.'
      : m.accuracy >= 4
        ? 'Handles names, jargon and accents well, and your dictionary biases it.'
        : m.accuracy >= 3
          ? 'Good with everyday speech; it will trip on names and technical words.'
          : 'Rough. Fine for quick notes, not for anything you have to trust.';
  return m.languages === 'multilingual'
    ? `${base} Understands 99 languages.`
    : `${base} English only.`;
}

function matches(m: ModelInfo, intent: Intent): boolean {
  if (intent === 'multi') return m.languages === 'multilingual' && m.accuracy >= 4;
  if (intent === 'accurate') return m.accuracy >= 4 && !m.bundled;
  return m.speed >= 5;
}

function Meters({ m }: { m: ModelInfo }) {
  const rows: [string, string, number][] = [
    ['Speed', ['Slow', 'Steady', 'Quick', 'Very fast', 'Instant'][m.speed - 1] ?? '—', m.speed],
    ['Accuracy', ['Rough', 'Basic', 'Good', 'Excellent', 'The best'][m.accuracy - 1] ?? '—', m.accuracy],
    [
      'Languages',
      m.languages === 'multilingual' ? '99' : 'English',
      m.languages === 'multilingual' ? 5 : 1,
    ],
  ];
  return (
    <div className="meters">
      {rows.map(([label, value, n]) => (
        <div className="meter" key={label}>
          <div className="top">
            <span>{label}</span>
            <b>{value}</b>
          </div>
          <div className="pips">
            {Array.from({ length: 5 }, (_, i) => (
              <i key={i} className={i < n ? 'on' : ''} />
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

function Card({ m, match }: { m: ModelInfo; match: boolean }) {
  const qc = useQueryClient();
  const invalidate = () => qc.invalidateQueries({ queryKey: ['models'] });

  const use = useMutation({ mutationFn: () => api.setActiveModel(m.id), onSuccess: invalidate });
  const download = useMutation({ mutationFn: () => api.downloadModel(m.id), onSuccess: invalidate });
  const cancel = useMutation({ mutationFn: () => api.cancelDownload(m.id), onSuccess: invalidate });
  const remove = useMutation({ mutationFn: () => api.deleteModel(m.id), onSuccess: invalidate });

  const busy = use.isPending || download.isPending || cancel.isPending || remove.isPending;
  const downloading = m.download.state === 'downloading';
  const err =
    (use.error ?? download.error ?? remove.error) instanceof Error
      ? (use.error ?? download.error ?? remove.error)!.message
      : m.download.error;

  return (
    <Reveal
      className={`card lift mcard${m.active ? ' active' : ''}${match && !m.active ? ' match' : ''}`}
    >
      <div>
        <div className="mtop">
          <h3>{m.name}</h3>
          {m.active ? <span className="tag now">In use</span> : null}
          {!m.active && m.downloaded ? <span className="tag rec">On this Mac</span> : null}
          <span className="tag have">{m.params}</span>
        </div>
        <p className="mdesc">
          <b>{latency(m.speed)}</b> {quality(m)}
        </p>
        <div className="facts">
          {m.bundled ? (
            <div className="fact">
              <b>Included</b>nothing to download
            </div>
          ) : m.downloaded ? (
            <div className="fact">
              <b>{bytes(m.size_bytes || m.download_bytes)}</b>already downloaded
            </div>
          ) : (
            <div className="fact">
              <b>{bytes(m.download_bytes)}</b>
              {downloadTime(m.download_bytes)} to download
            </div>
          )}
          <div className="fact">
            <b>{bytes(m.size_bytes || m.download_bytes)}</b>on your disk
          </div>
          <div className="fact">
            <b>{m.languages === 'multilingual' ? '99 languages' : 'English'}</b>
            {m.languages === 'multilingual' ? 'switch freely' : 'only'}
          </div>
        </div>
      </div>

      <div>
        <Meters m={m} />
        <div className="mact">
          {m.active ? (
            <button className="btn gho" disabled>
              In use
            </button>
          ) : downloading ? (
            <>
              <button className="btn gho" onClick={() => cancel.mutate()} disabled={busy}>
                Cancel
              </button>
              <div className="prog">
                <i style={{ width: `${Math.round(m.download.percent)}%` }} />
              </div>
            </>
          ) : m.downloaded ? (
            <button className="btn pri" onClick={() => use.mutate()} disabled={busy}>
              {use.isPending ? 'Switching…' : 'Use this one'}
            </button>
          ) : (
            <button
              className={match ? 'btn acc' : 'btn pri'}
              onClick={() => download.mutate()}
              disabled={busy}
            >
              {download.isPending ? 'Starting…' : `Download ${bytes(m.download_bytes)}`}
            </button>
          )}

          {!m.active && m.downloaded && !m.bundled ? (
            <button
              className="btn gho"
              style={{ marginTop: 8 }}
              onClick={() => remove.mutate()}
              disabled={busy}
            >
              Remove
            </button>
          ) : null}

          <div className="note">
            {err
              ? err
              : m.active
                ? 'What you are using right now.'
                : m.bundled
                  ? 'Already installed — this is what Say ships with.'
                  : match
                    ? 'The upgrade for what you picked above.'
                    : ''}
          </div>
        </div>
      </div>
    </Reveal>
  );
}

export function Models() {
  const [intent, setIntent] = useState<Intent>('fast');
  const { data, isLoading, error } = useQuery({
    queryKey: ['models'],
    queryFn: api.models,
    refetchInterval: (q) =>
      q.state.data?.models.some((m) => m.download.state === 'downloading') ? 800 : false,
  });

  const [primary, rest] = useMemo(() => {
    const models = [...(data?.models ?? [])].sort((a, b) => a.order - b.order);
    // Lead with what you are using, the best all-round upgrade, and the most
    // accurate. Everything else is a compromise for old hardware.
    const lead = models.filter(
      (m) => m.active || m.bundled || m.recommended || m.accuracy >= 5 || (m.accuracy >= 4 && m.speed >= 4),
    );
    const ids = new Set(lead.map((m) => m.id));
    return [lead, models.filter((m) => !ids.has(m.id))];
  }, [data]);

  const suggestion = useMemo(() => {
    const candidates = primary.filter((m) => matches(m, intent));
    // Cheapest thing that satisfies the intent — never upsell past it.
    return candidates.sort((a, b) => a.download_bytes - b.download_bytes)[0] ?? null;
  }, [primary, intent]);

  const [showRest, setShowRest] = useState(false);

  const answer = (() => {
    if (!suggestion) return 'Loading what you have installed…';
    if (suggestion.active) {
      return `**Stay where you are.** ${suggestion.name} is already what you are using, and it is the right pick for that. Nothing to download, nothing to decide.`;
    }
    if (suggestion.downloaded) {
      return `**Switch to ${suggestion.name}.** It is already on this Mac — switching takes effect immediately, no relaunch.`;
    }
    return `**Download ${suggestion.name}.** It costs ${bytes(suggestion.download_bytes)} and ${downloadTime(suggestion.download_bytes)}, and in exchange ${
      intent === 'multi'
        ? 'it understands 99 languages and lets you switch between them mid-sentence.'
        : 'it stops guessing at names, jargon and unusual words.'
    }`;
  })();

  return (
    <div className="page">
      <header className="hero">
        <h1>Pick how Say listens.</h1>
        <p>
          The only real trade is <b>how fast it answers</b> against <b>how well it hears you</b> —
          and how much disk it takes. Switching is instant, and you can change your mind whenever.
        </p>
      </header>

      <Reveal className="chooser">
        <div className="card-t" style={{ fontSize: 16 }}>
          What matters most to you right now?
        </div>
        <div className="card-s">Pick one and we&rsquo;ll point at the right model.</div>
        <div className="chips">
          {INTENTS.map((it) => (
            <button
              key={it.key}
              className={intent === it.key ? 'chip on' : 'chip'}
              onClick={() => setIntent(it.key)}
            >
              <span className="ic">{it.icon}</span>
              <h4>{it.title}</h4>
              <p>{it.blurb}</p>
            </button>
          ))}
        </div>
        <div className="answer">
          <span className="b">→</span>
          <p>
            {answer.split(/(\*\*[^*]+\*\*)/g).map((part, i) =>
              part.startsWith('**') ? <b key={i}>{part.slice(2, -2)}</b> : <span key={i}>{part}</span>,
            )}
          </p>
        </div>
      </Reveal>

      <div className="sec">
        {isLoading ? (
          <div className="card-s">Loading models…</div>
        ) : error ? (
          <div className="card err">
            <h3>Can&rsquo;t reach the dictation engine</h3>
            <p>Make sure Say is running, then try again.</p>
          </div>
        ) : (
          <>
            {primary.map((m) => (
              <Card key={m.id} m={m} match={suggestion?.id === m.id} />
            ))}

            {rest.length > 0 ? (
              <>
                <button className="more" onClick={() => setShowRest((v) => !v)}>
                  {showRest
                    ? 'Hide the smaller models'
                    : `Show ${rest.length} more — smaller models for tight disks and older Macs`}
                </button>
                {showRest
                  ? rest.map((m) => <Card key={m.id} m={m} match={false} />)
                  : null}
              </>
            ) : null}
          </>
        )}
      </div>

      {data ? (
        <Reveal className="strip" style={{ marginTop: 16 }}>
          <span>Stored on disk</span>
          <span>
            <b>{bytes(data.cache_bytes)}</b> — removing one frees its space immediately
          </span>
        </Reveal>
      ) : null}
    </div>
  );
}
