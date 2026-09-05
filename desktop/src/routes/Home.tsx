// Home: the insights, then the transcripts, on one scroll.
//
// There is no separate Insights tab and no separate Recordings tab. You land
// on what the app has learned about you, and if you keep scrolling you reach
// what you actually said — with playback on the row rather than behind
// another destination.

import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, invalidateEngineEndpoint } from '../lib/api';
import { fmt, greeting, hm, longDuration } from '../lib/format';
import { CountUp, Reveal } from '../lib/motion';
import { groupByDay } from '../lib/grouping';
import { AppShare, Heatmap, HourBars, SavedVsTyping, Spark, WordList } from '../components/Charts';
import { Discovery } from '../components/Discovery';
import { TranscriptRow } from '../components/TranscriptRow';
import { PermissionsBanner } from '../components/PermissionsBanner';

const PAGE = 40;

// The engine reads the first name from the OS account, which is often stored
// lowercase. Greeting someone by a lowercase version of their own name reads
// as a bug in the app, not as a style.
function titleCase(name?: string): string {
  if (!name) return '';
  return name.charAt(0).toUpperCase() + name.slice(1);
}

function Kpi({
  label,
  children,
  sub,
  badge,
  spark,
  delay,
}: {
  label: string;
  children: React.ReactNode;
  sub?: string;
  badge?: string;
  spark?: number[];
  delay?: number;
}) {
  return (
    <Reveal className="card lift kpi" delay={delay}>
      <div className="lab">
        <i />
        {label}
      </div>
      <div className="val num">{children}</div>
      <div className="foot">
        <span className="sub">{sub}</span>
        {badge ? <span className="pill-good">{badge}</span> : null}
        {spark && spark.length > 1 ? <Spark data={spark} /> : null}
      </div>
    </Reveal>
  );
}

function Disconnected({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  const msg = error instanceof Error ? error.message : String(error ?? 'Unknown error');
  return (
    <div className="card err">
      <h3>Can&rsquo;t reach the dictation engine</h3>
      <p>
        The window talks to the engine over a local socket. Make sure Say is running, then
        try again.
      </p>
      <div className="mono strip" style={{ marginBottom: 14 }}>
        <span>~/.venvs/say/bin/python say/app.py --server</span>
      </div>
      <p className="mono" style={{ fontSize: 12 }}>{msg}</p>
      <button className="btn pri" style={{ width: 'auto' }} onClick={onRetry}>
        Retry
      </button>
    </div>
  );
}

export function Home() {
  const qc = useQueryClient();
  const [query, setQuery] = useState('');
  const [shown, setShown] = useState(PAGE);

  const profile = useQuery({ queryKey: ['profile'], queryFn: api.profile });
  const summary = useQuery({ queryKey: ['summary'], queryFn: api.summary });
  const vocab = useQuery({ queryKey: ['vocabulary'], queryFn: api.vocabulary });
  const transcripts = useQuery({ queryKey: ['transcripts'], queryFn: () => api.transcripts(500) });
  const usage = useQuery({ queryKey: ['desktop_usage'], queryFn: () => api.desktopUsage(30) });
  const heat = useQuery({ queryKey: ['heatmap'], queryFn: () => api.heatmap(180) });
  const recCfg = useQuery({ queryKey: ['recordings-config'], queryFn: api.recordingsConfig });

  const setKeepAudio = useMutation({
    mutationFn: (on: boolean) => api.setRecordingsConfig({ save_recordings: on }),
    onSuccess: (cfg) => {
      qc.setQueryData(['recordings-config'], cfg);
      qc.invalidateQueries({ queryKey: ['transcripts'] });
    },
  });

  const v = vocab.data;
  const s = summary.data;

  const filtered = useMemo(() => {
    const rows = transcripts.data ?? [];
    const q = query.trim().toLowerCase();
    if (!q) return rows;
    return rows.filter(
      (r) =>
        r.text.toLowerCase().includes(q) ||
        (r.target_app ?? '').toLowerCase().includes(q),
    );
  }, [transcripts.data, query]);

  const groups = useMemo(() => groupByDay(filtered.slice(0, shown)), [filtered, shown]);
  const spark = useMemo(() => (heat.data ?? []).slice(-30).map((c) => c.count), [heat.data]);

  const retry = () => {
    invalidateEngineEndpoint();
    qc.invalidateQueries();
  };

  if (transcripts.isError && !transcripts.data) {
    return (
      <div className="page">
        <header className="hero">
          <h1>{greeting()}.</h1>
        </header>
        <Disconnected error={transcripts.error} onRetry={retry} />
      </div>
    );
  }

  const words = v?.total_words ?? s?.total_words ?? 0;
  const speaking = v?.speaking_seconds ?? 0;
  const typing = v?.typing_seconds ?? 0;
  const saved = hm(Math.max(0, typing - speaking));
  const mic = hm(speaking);
  const wpm = speaking > 0 ? (words / speaking) * 60 : (s?.wpm_30d ?? 0);
  const perDictation = v && v.dictations > 0 ? Math.round(speaking / v.dictations) : 0;

  return (
    <div className="page">
      <header className="hero">
        <h1>
          {greeting()}, {titleCase(profile.data?.first_name) || 'there'}.
          {words > 0 ? (
            <>
              <br />
              You&rsquo;ve said <em>{fmt(words)}</em> words.
            </>
          ) : null}
        </h1>
        {v && v.dictations > 0 ? (
          <p>
            Across <b>{fmt(v.dictations)} dictations</b>. That&rsquo;s{' '}
            <b>{longDuration(speaking)}</b> of talking — and about{' '}
            <b>{longDuration(Math.max(0, typing - speaking))}</b> you didn&rsquo;t spend typing.
          </p>
        ) : (
          <p>
            Hold <span className="kbd">Fn</span> and speak. Release, and the text appears in
            whatever app you&rsquo;re working in.
          </p>
        )}
      </header>

      <PermissionsBanner />

      <div className="split">
        <div className="kpis">
          <Kpi
            label="Words spoken"
            sub={v ? `${fmt(v.unique_words)} different ones` : undefined}
            spark={spark}
          >
            <CountUp value={words} />
          </Kpi>

          <Kpi
            label="Time at the mic"
            sub={perDictation ? `${perDictation}s per dictation, typically` : undefined}
            delay={60}
          >
            <CountUp value={mic.h} format={(n) => String(Math.round(n))} />
            <u>h</u>
            <CountUp value={mic.m} format={(n) => String(Math.round(n))} />
            <u>m</u>
          </Kpi>

          <Kpi
            label="Your pace"
            sub={wpm > 0 ? `${(wpm / 40).toFixed(1)}× faster than typing` : undefined}
            badge={s?.streak_days ? `${s.streak_days}-day streak` : undefined}
            delay={120}
          >
            <CountUp value={wpm} format={(n) => String(Math.round(n))} />
            <u>wpm</u>
          </Kpi>

          <Kpi
            label="Hours you got back"
            sub="vs. typing the same words"
            delay={180}
          >
            <CountUp value={saved.h} format={(n) => String(Math.round(n))} />
            <u>h</u>
            <CountUp value={saved.m} format={(n) => String(Math.round(n))} />
            <u>m</u>
          </Kpi>
        </div>

        <Reveal delay={80}>
          {v?.enough_data && v.insights.length > 0 ? (
            <Discovery insights={v.insights} />
          ) : (
            <div className="disc">
              <div className="disc-top">
                <span className="disc-tag">
                  <i />
                  What we noticed
                </span>
              </div>
              <div className="disc-stage">
                <div className="slide on">
                  <h3>Still listening.</h3>
                  <div className="body">
                    Once there is enough here to say something true, this is where it lands —
                    the words you lean on, the phrases you repeat, the hour you do your best
                    thinking. Nothing gets claimed until the numbers can carry it.
                  </div>
                </div>
              </div>
            </div>
          )}
        </Reveal>
      </div>

      {v?.enough_data ? (
        <>
          <section className="sec">
            <Reveal className="sec-h">
              <h2>The words you reach for</h2>
              <p>
                Common words everyone uses are set aside. What is left is the vocabulary that
                is actually yours.
              </p>
            </Reveal>
            <div className="two">
              <WordList
                title="Most-said words"
                subtitle="How often each one comes back"
                badge={`${fmt(v.total_words)} total`}
                rows={v.top_words}
                kind="word"
              />
              <WordList
                title="Phrases you reach for"
                subtitle="Whole expressions, not just words"
                badge="Habits"
                rows={v.phrases}
                kind="phrase"
                footer={
                  <div className="strip">
                    <span>Vocabulary breadth</span>
                    <span>
                      <b>{fmt(v.unique_words)}</b> distinct words
                      {v.sentence_avg > 0 ? (
                        <>
                          {' · '}
                          <b>{v.sentence_avg}</b> words per sentence
                        </>
                      ) : null}
                    </span>
                  </div>
                }
              />
            </div>
          </section>

          <section className="sec">
            <div className="two">
              <SavedVsTyping
                words={v.total_words}
                speakingSeconds={speaking}
                typingSeconds={typing}
                wpm={wpm}
              />
              {heat.data ? <Heatmap cells={heat.data} /> : <div />}
            </div>
          </section>

          <section className="sec">
            <div className="two">
              <HourBars hours={v.hours} />
              <AppShare rows={usage.data ?? []} />
            </div>
          </section>
        </>
      ) : null}

      <div className="tsec-h">
        <h2>Transcripts</h2>
        <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
          {recCfg.data ? (
            <label
              style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12.5, color: 'var(--muted)', cursor: 'pointer' }}
              title="Keep a local copy of each clip so you can play it back here"
            >
              <button
                className={recCfg.data.save_recordings ? 'sw on' : 'sw'}
                disabled={setKeepAudio.isPending}
                onClick={() => setKeepAudio.mutate(!recCfg.data!.save_recordings)}
                role="switch"
                aria-checked={recCfg.data.save_recordings}
                aria-label="Keep audio"
              >
                <i />
              </button>
              Keep audio
            </label>
          ) : null}
          <div className="search">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <circle cx="11" cy="11" r="7" />
              <path d="M20 20l-3.5-3.5" />
            </svg>
            <input
              value={query}
              onChange={(e) => {
                setQuery(e.target.value);
                setShown(PAGE);
              }}
              placeholder="Search everything you've said…"
            />
          </div>
        </div>
      </div>

      {transcripts.isLoading ? (
        <div className="card-s">Loading transcripts…</div>
      ) : groups.length === 0 ? (
        <div className="card empty">
          <h3>{query ? 'Nothing matches that' : 'No dictations yet'}</h3>
          <p>
            {query ? (
              'Try a different word.'
            ) : (
              <>
                Hold <span className="kbd">Fn</span> and speak. Release, and the text appears
                in whatever app you&rsquo;re working in.
              </>
            )}
          </p>
        </div>
      ) : (
        <>
          {groups.map((g) => (
            <section key={g.isoDate}>
              <div className="daylab">
                <span>{g.label}</span>
                <i />
              </div>
              {g.rows.map((row) => (
                <TranscriptRow key={row.id} row={row} />
              ))}
            </section>
          ))}
          {filtered.length > shown ? (
            <button className="more" onClick={() => setShown((n) => n + PAGE)}>
              Load older dictations — {fmt(filtered.length - shown)} more
            </button>
          ) : null}
        </>
      )}
    </div>
  );
}
