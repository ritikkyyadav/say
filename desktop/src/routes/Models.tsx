// Models, built around the only question a person actually has here: will it
// understand me? First you say what you dictate in — English only, European
// languages, Hindi/Urdu and South Asian, or anything — and the page tells you
// in one sentence whether you already have what that needs. Below that, every
// model the app can run is listed once, with its status in plain words and one
// action. Nothing is hidden behind a disclosure and nothing is a meter you
// have to interpret.

import { useMemo } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, LanguageConfig, LanguageGroup, ModelInfo } from '../lib/api';
import { bytes, downloadTime } from '../lib/format';
import { Reveal } from '../lib/motion';

const SPEED = ['Slow', 'Steady', 'Quick', 'Fast', 'Instant'];
const ACCURACY = ['Rough', 'Basic', 'Good', 'Excellent', 'The best'];

/** One sentence per model, in the words a person would use. Falls back to
 *  the catalogue's own description for anything not listed here. */
const WHAT_FOR: Record<string, string> = {
  'moonshine/medium-streaming-en':
    'Built in. English only, and the fastest there is. Your dictionary does not reach it, so names and jargon are a guess.',
  'mlx-community/whisper-large-v3-turbo':
    'The one to have for any language: fast, accurate, understands 99 languages, and your dictionary biases it.',
  'mlx-community/whisper-large-v3-mlx':
    'The most accurate, and slow — it can take several seconds per sentence on this Mac. For when every word matters more than the wait.',
  'mlx-community/whisper-medium-mlx':
    'Older and slower than Turbo with no accuracy gain. Only if Turbo will not fit on your disk.',
  'mlx-community/whisper-small-mlx':
    'Small and quick. Fine for short, clear speech; it trips on names and technical words.',
  'mlx-community/whisper-base-mlx':
    'Tiny and rough. For very old Macs, or for trying the multilingual path before downloading a real model.',
  'mlx-community/whisper-tiny-mlx':
    'The smallest there is. For testing only; do not dictate anything you have to trust into it.',
};

function whatFor(m: ModelInfo): string {
  return WHAT_FOR[m.id] ?? m.description;
}

function statusOf(m: ModelInfo): { key: 'now' | 'dl' | 'have' | 'none'; label: string } {
  if (m.active) return { key: 'now', label: 'In use' };
  if (m.download.state === 'downloading') {
    const pct = Math.round(m.download.percent);
    return { key: 'dl', label: pct > 0 ? `Downloading ${pct}%` : 'Starting download' };
  }
  if (m.downloaded) return { key: 'have', label: m.bundled ? 'Built in' : 'Installed' };
  return { key: 'none', label: 'Not installed' };
}

// ── The focus chooser ──────────────────────────────────────────────────────

function FocusStatus({
  lang,
  models,
}: {
  lang: LanguageConfig;
  models: ModelInfo[];
}) {
  const active = models.find((m) => m.active);
  const rec = models.find((m) => m.id === lang.recommended_model);
  const group = lang.groups.find((g) => g.id === lang.focus);
  if (!group || !rec) return null;

  const activeUnderstands = active
    ? lang.focus === 'english' || active.languages === 'multilingual'
    : false;

  let line: string;
  let tone: 'ok' | 'warn' = 'ok';
  if (lang.focus === 'english') {
    line = active?.id === rec.id
      ? `You are set. ${rec.name} is built in, in use, and the fastest for English.`
      : `${active?.name ?? 'The model in use'} understands English too, but ${rec.name} is built in and faster. Switch below if you want instant results.`;
  } else if (activeUnderstands && active) {
    line = active.id === rec.id
      ? `You are set. ${active.name} understands these languages and is in use.`
      : `You are set. ${active.name} understands these languages. ${rec.name} is the faster all-rounder if you want it.`;
  } else {
    tone = 'warn';
    line = rec.downloaded
      ? `The model in use only understands English. ${rec.name} is installed — press Use below.`
      : `The model in use only understands English. Download ${rec.name} below (${bytes(rec.download_bytes)}, about ${downloadTime(rec.download_bytes)}), then press Use.`;
  }
  return <div className={`focus-line ${tone}`}>{line}</div>;
}

function Chooser({
  lang,
  models,
  onFocus,
  onCode,
  busy,
  error,
}: {
  lang: LanguageConfig;
  models: ModelInfo[];
  onFocus: (id: string) => void;
  onCode: (code: string | null) => void;
  busy: boolean;
  error: string | null;
}) {
  const pinned = lang.focus !== 'english';
  return (
    <Reveal className="chooser">
      <div className="card-t" style={{ fontSize: 16 }}>
        What do you dictate in?
      </div>
      <div className="card-s">
        Pick one. It sets the language the engine listens for and tells you which model to have.
      </div>
      <div className="chips focus">
        {lang.groups.map((g: LanguageGroup) => (
          <button
            key={g.id}
            className={lang.focus === g.id ? 'chip on' : 'chip'}
            onClick={() => onFocus(g.id)}
            disabled={busy}
          >
            <h4>{g.name}</h4>
            <p>{g.blurb}</p>
          </button>
        ))}
      </div>

      <FocusStatus lang={lang} models={models} />

      <div className="focus-lang">
        <span>Language</span>
        <select
          className="sel"
          aria-label="Language"
          value={lang.code ?? 'auto'}
          disabled={busy || !pinned}
          onChange={(e) => onCode(e.target.value === 'auto' ? null : e.target.value)}
        >
          {pinned ? <option value="auto">Automatic — detect, switch mid-sentence</option> : null}
          {lang.options.map((o) => (
            <option key={o.code} value={o.code}>
              {o.name}
            </option>
          ))}
        </select>
        <span className="card-s" style={{ marginTop: 0 }}>
          {pinned
            ? 'Automatic is right for most people. Pin one language only if detection keeps guessing wrong.'
            : 'Fixed to English for this choice.'}
        </span>
        {error ? <span className="card-s" style={{ color: 'var(--accent-deep)', marginTop: 0 }}>{error}</span> : null}
      </div>
    </Reveal>
  );
}

// ── One model, one row ─────────────────────────────────────────────────────

function ModelRow({ m, recommended }: { m: ModelInfo; recommended: boolean }) {
  const qc = useQueryClient();
  const invalidate = () => qc.invalidateQueries({ queryKey: ['models'] });
  const use = useMutation({ mutationFn: () => api.setActiveModel(m.id), onSuccess: invalidate });
  const download = useMutation({ mutationFn: () => api.downloadModel(m.id), onSuccess: invalidate });
  const cancel = useMutation({ mutationFn: () => api.cancelDownload(m.id), onSuccess: invalidate });
  const remove = useMutation({ mutationFn: () => api.deleteModel(m.id), onSuccess: invalidate });

  const busy = use.isPending || download.isPending || cancel.isPending || remove.isPending;
  const status = statusOf(m);
  const err =
    (use.error ?? download.error ?? remove.error ?? cancel.error) instanceof Error
      ? (use.error ?? download.error ?? remove.error ?? cancel.error)!.message
      : m.download.error;

  return (
    <div className={`mrow${m.active ? ' now' : ''}${recommended ? ' rec' : ''}`}>
      <div className="mrow-main">
        <div className="mrow-top">
          <span className={`mstat ${status.key}`}>{status.label}</span>
          <h3>{m.name}</h3>
          {recommended ? <span className="tag rec">Recommended for you</span> : null}
        </div>
        <p className="mrow-what">{whatFor(m)}</p>
        <div className="mrow-facts">
          <span>
            <b>{m.languages === 'multilingual' ? '99 languages' : 'English only'}</b>
          </span>
          <span>
            <b>{bytes(m.size_bytes || m.download_bytes)}</b> {m.downloaded ? 'on disk' : 'to download'}
          </span>
          <span>
            Speed <b>{SPEED[m.speed - 1] ?? '—'}</b>
          </span>
          <span>
            Accuracy <b>{ACCURACY[m.accuracy - 1] ?? '—'}</b>
          </span>
        </div>
        {status.key === 'dl' ? (
          <div className="prog" style={{ maxWidth: 420 }}>
            <i style={{ width: `${Math.max(2, Math.round(m.download.percent))}%` }} />
          </div>
        ) : null}
        {err ? <div className="mrow-err">{err}</div> : null}
      </div>

      <div className="mrow-act">
        {m.active ? (
          <button className="btn gho" disabled>
            In use
          </button>
        ) : status.key === 'dl' ? (
          <button className="btn gho" onClick={() => cancel.mutate()} disabled={busy}>
            Cancel
          </button>
        ) : m.downloaded ? (
          <button className={recommended ? 'btn acc' : 'btn pri'} onClick={() => use.mutate()} disabled={busy}>
            {use.isPending ? 'Switching…' : 'Use'}
          </button>
        ) : (
          <button className={recommended ? 'btn acc' : 'btn pri'} onClick={() => download.mutate()} disabled={busy}>
            {download.isPending ? 'Starting…' : `Download ${bytes(m.download_bytes)}`}
          </button>
        )}
        {!m.active && m.downloaded && !m.bundled && status.key !== 'dl' ? (
          <button className="btn gho" onClick={() => remove.mutate()} disabled={busy}>
            Remove
          </button>
        ) : null}
      </div>
    </div>
  );
}

// ── The page ───────────────────────────────────────────────────────────────

export function Models() {
  const qc = useQueryClient();
  const models = useQuery({
    queryKey: ['models'],
    queryFn: api.models,
    refetchInterval: (q) =>
      q.state.data?.models.some((m) => m.download.state === 'downloading') ? 800 : false,
  });
  const lang = useQuery({ queryKey: ['language'], queryFn: api.language });
  const setLang = useMutation({
    mutationFn: (patch: { code?: string | null; focus?: string }) => api.setLanguage(patch),
    onSuccess: (next) => qc.setQueryData(['language'], next),
  });

  const rows = useMemo(() => {
    const all = [...(models.data?.models ?? [])];
    // In use first, then what is on this Mac, then what is not — and inside
    // each group the catalogue's own order (best daily driver first).
    const rank = (m: ModelInfo) => (m.active ? 0 : m.downloaded ? 1 : 2);
    return all.sort((a, b) => rank(a) - rank(b) || a.order - b.order);
  }, [models.data]);

  if (models.isError || lang.isError) {
    return (
      <div className="page">
        <header className="hero">
          <h1>Models</h1>
        </header>
        <div className="card err">
          <h3>Can&rsquo;t reach the dictation engine</h3>
          <p>Make sure Say is running, then try again.</p>
        </div>
      </div>
    );
  }

  const recommended = lang.data?.recommended_model;
  const langError = setLang.error instanceof Error ? setLang.error.message : null;

  return (
    <div className="page">
      <header className="hero">
        <h1>Will it understand you?</h1>
        <p>
          Say what you dictate in, and the page tells you whether you already have the right
          model. Every model the app can run is listed below with its status in plain words.
          Switching is instant and never needs a relaunch.
        </p>
      </header>

      {lang.data && models.data ? (
        <Chooser
          lang={lang.data}
          models={models.data.models}
          onFocus={(id) => setLang.mutate({ focus: id })}
          onCode={(code) => setLang.mutate({ code })}
          busy={setLang.isPending}
          error={langError}
        />
      ) : (
        <div className="card-s">Loading…</div>
      )}

      <section className="sec">
        <Reveal className="sec-h">
          <h2>Every model, and where it stands</h2>
          <p>
            <b>In use</b> is what transcribes right now. <b>Installed</b> is on this Mac and one
            click away. <b>Not installed</b> has to be downloaded first.
          </p>
        </Reveal>
        {models.isLoading ? (
          <div className="card-s">Loading models…</div>
        ) : (
          <div className="mlist">
            {rows.map((m) => (
              <ModelRow key={m.id} m={m} recommended={m.id === recommended} />
            ))}
          </div>
        )}
      </section>

      {models.data ? (
        <Reveal className="strip" style={{ marginTop: 16 }}>
          <span>Stored on disk</span>
          <span>
            <b>{bytes(models.data.cache_bytes)}</b> across the installed models — removing one frees its
            space immediately
          </span>
        </Reveal>
      ) : null}
    </div>
  );
}
