// Typed HTTP client for the Say engine sidecar.
//
// The Tauri shell exposes two commands the React app can invoke:
//   - get_engine_endpoint() -> { host, port, token } | null
//   - reveal_logs_dir()     -> ()
//
// In dev (vite without Tauri), we fall back to reading runtime.json
// over a local fetch helper, but that path will only work when the user
// has the menu-bar agent running with --server.

export type EngineEndpoint = {
  host: string;
  port: number;
  token: string;
};

export type DictationStateName =
  | 'idle'
  | 'loading'
  | 'ready'
  | 'recording'
  | 'processing'
  | 'error';

export type DictationStateEvent = {
  state: DictationStateName;
  message?: string;
  mode?: 'ptt' | 'hands_free' | null;
  error?: string | null;
  /** Segments decoded so far while processing, so a long wait reads as
   *  "3 of 7" instead of an unexplained spinner. */
  progress?: { done: number; total: number } | null;
};

export type Transcript = {
  id: number;
  ts_utc: string;
  text: string;
  word_count: number;
  audio_seconds: number;
  inference_seconds: number;
  model: string;
  inject_method: 'accessibility' | 'clipboard' | string;
  target_app: string | null;
  success: boolean;
  /** Whether a saved WAV exists for this dictation, so the feed can offer
   *  playback without a probe request per row. False for everyone who has
   *  not opted into keeping audio. */
  has_audio: boolean;
  /** Your own version of this transcript, if you have written one. `text`
   *  stays what Say produced; equal to `text` means "checked, and right". */
  corrected: string | null;
  /** Set when a second model heard this clip differently and you have not
   *  checked it yet. Where to look, not a verdict. */
  review: { rank: number; differing_words: number } | null;
};

/** Say a trigger as the whole dictation and its text is typed instead. */
export type SnippetsConfig = {
  snippets: Record<string, string>;
  min_trigger_words: number;
  max_snippets: number;
};

/** One correction: what Say typed and what it became. */
export type Correction = {
  id: number;
  transcript_id: number;
  ts_utc: string;
  source: 'observed' | 'manual';
  inserted: string;
  corrected: string;
  edit_distance: number;
  words: number;
  target_app: string | null;
};

export type ListenerState = {
  kind: 'fn' | 'legacy' | null;
  label: string | null;
  fell_back: boolean;
  fn_error: string | null;
  started: boolean;
  accessibility_trusted: boolean;
};

export type StatsSummary = {
  total_words: number;
  wpm_30d: number;
  streak_days: number;
  fixes_count: number;
  /** Insertions the read-back actually compared in the last 30 days — the rate's
   *  denominator. Excludes ones we could not read (and all history predating the
   *  feature): unmeasured must never be scored as clean. */
  insertions_30d: number;
  /** Share of insertions the user then edited. 0 is the goal. */
  correction_rate: number;
  /** Share of inserted *words* the user had to change. */
  correction_word_rate: number;
  words_this_month: number;
  words_prev_month: number;
  transcript_count: number;
};

export type DesktopUsageRow = {
  target_app: string;
  count: number;
  share: number;
};

export type HeatmapCell = {
  date: string;
  count: number;
};

export type UserProfile = {
  first_name: string;
  /** Desktop first-run flow completed. Local flag; there is no account. */
  onboarded: boolean;
};

// ── Language ────────────────────────────────────────────────────────────
// Which language the decoder expects. `code: null` means detect automatically,
// which is what lets a multilingual model switch mid-sentence.

export type LanguageOption = { code: string; name: string };

/** What a person dictates in. Fixes the picker to a short list, decides the
 *  default (English pinned; everything else detects automatically) and names
 *  the one model worth having for it. */
export type LanguageGroup = {
  id: string;
  name: string;
  blurb: string;
  default_code: string | null;
  recommended_model: string;
  options: LanguageOption[];
};

export type LanguageConfig = {
  code: string | null;
  auto: boolean;
  name: string;
  /** The picker list for the current focus. */
  options: LanguageOption[];
  focus: string;
  groups: LanguageGroup[];
  recommended_model: string;
  /** False when the active model ignores the setting (the bundled model is
   *  English-only), so the UI can say so instead of implying a choice. */
  applies_to_active_model: boolean;
};

// ── Vocabulary ─────────────────────────────────────────────────────────
// What the transcripts say about the person who spoke them. Computed on the
// engine (say/vocabulary.py) so the thresholds that decide whether a claim is
// true live next to the words that make the claim.

export type WordStat = {
  word: string;
  count: number;
  /** One in every `per` words spoken. */
  per: number;
  /** Share of dictations it appears in, 0–1. */
  spread: number;
};

export type InsightViz =
  | { kind: 'dots'; every: number; rows: number; caption: string; label: string }
  | { kind: 'bars'; rows: [string, number][]; unit?: string };

export type Insight = {
  id: string;
  /** Carries `*emphasis*` markup — render with `markup()` from lib/markup. */
  headline: string;
  /** Carries `**bold**` markup. */
  body: string;
  viz: InsightViz;
};

export type VocabularyReport = {
  total_words: number;
  unique_words: number;
  dictations: number;
  /** Degenerate (decoder-loop) transcripts left out of the counts. */
  excluded: number;
  /** False when the corpus is too thin to say anything honest about. */
  enough_data: boolean;
  top_words: WordStat[];
  phrases: WordStat[];
  fillers: WordStat[];
  hours: number[];      // 24 buckets of words
  weekdays: number[];   // 7 buckets of dictations, Monday first
  sentence_avg: number;
  longest_words: number;
  longest_seconds: number;
  speaking_seconds: number;
  typing_seconds: number;
  insights: Insight[];
};

// ── Overlay appearance ─────────────────────────────────────────────────

export type OverlayPreset = {
  id: string;
  name: string;
  /** Cycled across the twelve columns. Empty = follows the system appearance. */
  colors: string[];
  blurb: string;
  adaptive: boolean;
};

export type OverlayPatternInfo = { id: string; name: string; blurb: string };

export type OverlayConfig = {
  theme: string;
  colors: string[];
  /** How the colours are spread across the twelve columns. Everything except
   *  'alternate' derives the colour from the live audio, so it never repeats. */
  pattern: string;
  patterns: OverlayPatternInfo[];
  show_overlay: boolean;
  contrast: string;
  max_colors: number;
  presets: OverlayPreset[];
};

export type OverlayConfigPatch = Partial<{
  theme: string;
  colors: string[];
  pattern: string;
  show_overlay: boolean;
  contrast: string;
  preview: boolean;
}>;

export type ModelDownloadState = 'idle' | 'downloading' | 'done' | 'error';

export type ModelDownload = {
  state: ModelDownloadState;
  percent: number;
  error: string | null;
};

// One installable on-device STT model: static catalog metadata from
// model_catalog.py plus live cache state from model_manager.py.
export type ModelInfo = {
  id: string;
  name: string;
  family: string;
  params: string;
  download_bytes: number;
  speed: number; // 1 (slow) … 5 (fastest)
  accuracy: number; // 1 (roughest) … 5 (best)
  languages: 'multilingual' | 'english' | string;
  recommended: boolean;
  order: number;
  description: string;
  backend: 'whisper-mlx' | 'moonshine' | string;
  downloaded: boolean;
  bundled: boolean;
  size_bytes: number;
  active: boolean;
  download: ModelDownload;
};

export type ModelsResponse = {
  active: string;
  cache_bytes: number;
  models: ModelInfo[];
};

// One saved audio receipt: a dictation's WAV plus the transcript it produced.
export type Recording = {
  id: number;
  ts_utc: string;
  text: string;
  word_count: number;
  audio_seconds: number;
  model: string;
  inject_method: string;
  target_app: string | null;
  success: boolean;
  size_bytes: number;
  has_audio: boolean;
};

export type RetentionMode = 'keep_all' | 'days' | 'size';

export type RecordingsConfig = {
  save_recordings: boolean;
  retention_mode: RetentionMode;
  retention_days: number;
  max_bytes: number;
  total_bytes: number;
  count: number;
};

export type RecordingsConfigPatch = Partial<
  Pick<RecordingsConfig, 'save_recordings' | 'retention_mode' | 'retention_days' | 'max_bytes'>
>;

export type ExportResult = {
  path: string | null;
  count: number;
  bytes: number;
};

let cachedEndpoint: EngineEndpoint | null = null;

async function invokeTauri<T>(cmd: string, args?: Record<string, unknown>): Promise<T> {
  // Lazy-load so the dev page works in a plain browser too.
  const w = window as unknown as { __TAURI_INTERNALS__?: unknown };
  if (!w.__TAURI_INTERNALS__) {
    throw new Error('Not running inside Tauri shell');
  }
  const mod = await import('@tauri-apps/api/core');
  return mod.invoke<T>(cmd, args);
}

// Browser-dev path: hit the Vite middleware that re-reads runtime.json on
// every request. This makes the page self-heal when the engine restarts
// with a new port/token — no Vite restart needed.
async function readRuntimeFromDevMiddleware(): Promise<EngineEndpoint | null> {
  try {
    const r = await fetch('/__say/runtime', { cache: 'no-store' });
    if (!r.ok) return null;
    const data = (await r.json()) as Partial<EngineEndpoint> & { error?: string };
    if (data.error || !data.port) return null;
    return {
      host: data.host ?? '127.0.0.1',
      port: Number(data.port),
      token: data.token ?? '',
    };
  } catch {
    return null;
  }
}

export async function getEngineEndpoint(force = false): Promise<EngineEndpoint> {
  if (!force && cachedEndpoint) return cachedEndpoint;
  // 1. Inside the Tauri shell → ask Rust to read runtime.json directly.
  try {
    const ep = await invokeTauri<EngineEndpoint | null>('get_engine_endpoint');
    if (ep && ep.port) {
      cachedEndpoint = ep;
      return ep;
    }
  } catch {
    /* not in Tauri — try dev paths */
  }
  // 2. Plain browser dev → /__say/runtime middleware re-reads runtime.json.
  const live = await readRuntimeFromDevMiddleware();
  if (live) {
    cachedEndpoint = live;
    return live;
  }
  // 3. Last-resort: values baked into the bundle at Vite startup.
  const fallback = (import.meta as any).env?.VITE_DEV_ENGINE_PORT;
  if (fallback) {
    const token = (import.meta as any).env?.VITE_DEV_ENGINE_TOKEN ?? '';
    cachedEndpoint = { host: '127.0.0.1', port: Number(fallback), token };
    return cachedEndpoint;
  }
  throw new Error(
    'Cannot reach the dictation engine. Start it with `python say/app.py --server` (dev) or launch the bundled Say.app.',
  );
}

export function invalidateEngineEndpoint(): void {
  cachedEndpoint = null;
}

function authHeaders(ep: EngineEndpoint): HeadersInit {
  return ep.token ? { Authorization: `Bearer ${ep.token}` } : {};
}

function url(ep: EngineEndpoint, path: string): string {
  return `http://${ep.host}:${ep.port}${path}`;
}

async function getJSON<T>(path: string): Promise<T> {
  const ep = await getEngineEndpoint();
  let r: Response;
  try {
    r = await fetch(url(ep, path), { headers: authHeaders(ep) });
  } catch {
    // Network-level failure → engine may have restarted with a new port.
    cachedEndpoint = null;
    throw new Error(`${path} → connection refused`);
  }
  if (r.status === 401) {
    // Token mismatch — engine restarted with a new token. Force re-read next call.
    cachedEndpoint = null;
    throw new Error(`${path} → HTTP 401 (token expired)`);
  }
  if (!r.ok) {
    throw new Error(`${path} → HTTP ${r.status}`);
  }
  return (await r.json()) as T;
}

// Pull a human-readable message out of FastAPI's `{ detail }` error body so
// mutation failures surface something better than a bare status code.
async function errorDetail(r: Response, path: string): Promise<string> {
  try {
    const body = (await r.json()) as { detail?: string };
    if (body?.detail) return body.detail;
  } catch {
    /* non-JSON body */
  }
  return `${path} → HTTP ${r.status}`;
}

async function sendJSON<T>(method: 'POST' | 'DELETE', path: string, body?: unknown): Promise<T> {
  const ep = await getEngineEndpoint();
  let r: Response;
  try {
    r = await fetch(url(ep, path), {
      method,
      headers: {
        ...authHeaders(ep),
        ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch {
    cachedEndpoint = null;
    throw new Error(`${path} → connection refused`);
  }
  if (r.status === 401) {
    cachedEndpoint = null;
    throw new Error(`${path} → HTTP 401 (token expired)`);
  }
  if (!r.ok) {
    throw new Error(await errorDetail(r, path));
  }
  return (await r.json()) as T;
}

export const api = {
  health: () => getJSON<{ ok: boolean; state: DictationStateName }>('/health'),
  listener: () => getJSON<ListenerState>('/listener'),
  profile: () => getJSON<UserProfile>('/user/profile'),
  setProfile: (patch: { first_name?: string; onboarded?: boolean }) =>
    sendJSON<UserProfile>('POST', '/user/profile', patch),
  transcripts: (limit = 200, before?: string) =>
    getJSON<Transcript[]>(
      `/transcripts?limit=${limit}${before ? `&before=${encodeURIComponent(before)}` : ''}`,
    ),
  summary: () => getJSON<StatsSummary>('/stats/summary'),
  vocabulary: () => getJSON<VocabularyReport>('/stats/vocabulary'),
  desktopUsage: (days = 30) =>
    getJSON<DesktopUsageRow[]>(`/stats/desktop_usage?days=${days}`),
  heatmap: (days = 180) =>
    getJSON<HeatmapCell[]>(`/stats/heatmap?days=${days}`),
  copyTranscript: async (id: number): Promise<void> => {
    const ep = await getEngineEndpoint();
    await fetch(url(ep, `/transcripts/${id}/copy`), {
      method: 'POST',
      headers: authHeaders(ep),
    });
  },
  // Your own version of a transcript. Saving the text unchanged is how you
  // say "this one was right".
  correctTranscript: (id: number, text: string) =>
    sendJSON<Correction>('POST', `/transcripts/${id}/correction`, { text }),
  snippets: () => getJSON<SnippetsConfig>('/snippets'),
  // The whole set at once; the engine validates it before writing anything.
  setSnippets: (snippets: Record<string, string>) =>
    sendJSON<SnippetsConfig>('POST', '/snippets', { snippets }),
  clearTranscriptCorrection: (id: number) =>
    sendJSON<{ deleted: boolean }>('DELETE', `/transcripts/${id}/correction`),

  // ── Language ──────────────────────────────────────────────────────────
  language: () => getJSON<LanguageConfig>('/language'),
  // A focus without a code applies the focus's default; `code: null` pins
  // automatic detection. Undefined keys are dropped by JSON.stringify, which
  // is what lets the engine tell "not sent" from "sent as null".
  setLanguage: (patch: { code?: string | null; focus?: string }) =>
    sendJSON<LanguageConfig>('POST', '/language', patch),

  // ── Overlay appearance ────────────────────────────────────────────────
  overlay: () => getJSON<OverlayConfig>('/overlay'),
  setOverlay: (patch: OverlayConfigPatch) =>
    sendJSON<OverlayConfig>('POST', '/overlay', patch),
  previewOverlay: () => sendJSON<{ shown: boolean }>('POST', '/overlay/preview'),

  // ── Model management ──────────────────────────────────────────────────
  models: () => getJSON<ModelsResponse>('/models'),
  setActiveModel: (id: string) => sendJSON<{ active: string }>('POST', '/models/active', { id }),
  // `id` carries a slash (e.g. mlx-community/whisper-…); the engine route is a
  // `:path` param, so the slash is passed through unencoded on purpose.
  downloadModel: (id: string) => sendJSON<ModelDownload>('POST', `/models/${id}/download`),
  cancelDownload: (id: string) => sendJSON<{ cancelled: boolean }>('POST', `/models/${id}/cancel`),
  deleteModel: (id: string) => sendJSON<Record<string, never>>('DELETE', `/models/${id}`),

  // ── Recordings (audio receipts) ───────────────────────────────────────
  recordings: (limit = 500) => getJSON<Recording[]>(`/recordings?limit=${limit}`),
  recordingsConfig: () => getJSON<RecordingsConfig>('/recordings/config'),
  setRecordingsConfig: (patch: RecordingsConfigPatch) =>
    sendJSON<RecordingsConfig>('POST', '/recordings/config', patch),
  exportRecordings: () => sendJSON<ExportResult>('POST', '/recordings/export'),
  deleteRecording: (id: number) => sendJSON<{ deleted: boolean }>('DELETE', `/recordings/${id}`),
  clearRecordings: () => sendJSON<{ deleted: number }>('DELETE', '/recordings'),
  // Fetch a clip's WAV with the bearer header and hand back a blob: URL the
  // <audio> element can play. Caller is responsible for URL.revokeObjectURL.
  recordingAudioUrl: async (id: number): Promise<string> => {
    const ep = await getEngineEndpoint();
    const r = await fetch(url(ep, `/recordings/${id}/audio`), { headers: authHeaders(ep) });
    if (!r.ok) throw new Error(`/recordings/${id}/audio → HTTP ${r.status}`);
    return URL.createObjectURL(await r.blob());
  },
};

export type PrivacyPane = 'microphone' | 'accessibility' | 'input-monitoring';

export async function openPrivacySettings(pane: PrivacyPane): Promise<void> {
  try {
    await invokeTauri<void>('open_privacy_settings', { pane });
  } catch {
    /* dev / non-Tauri — silently no-op */
  }
}

// SSE subscription. Returns a cleanup function.
// Reconnects automatically with exponential backoff when the connection drops.
export function subscribeEvents(
  onEvent: (e: DictationStateEvent) => void,
  onError?: (err: Event) => void,
): () => void {
  let cancelled = false;
  let es: EventSource | null = null;
  let retryTimer: ReturnType<typeof setTimeout> | null = null;
  let retryDelay = 1_000;

  async function connect() {
    if (cancelled) return;
    try {
      const ep = await getEngineEndpoint();
      if (cancelled) return;
      // EventSource cannot set Authorization headers, so we pass the
      // token via query string. The API supports both.
      const u = url(ep, `/events${ep.token ? `?token=${encodeURIComponent(ep.token)}` : ''}`);
      es = new EventSource(u);
      es.addEventListener('state', (ev) => {
        retryDelay = 1_000; // reset backoff on successful event
        try {
          onEvent(JSON.parse((ev as MessageEvent).data));
        } catch {
          /* ignore parse errors */
        }
      });
      es.onerror = () => {
        es?.close();
        es = null;
        // Invalidate cached endpoint so the next attempt re-reads runtime.json.
        cachedEndpoint = null;
        onError?.(new Event('error'));
        if (!cancelled) {
          retryTimer = setTimeout(() => {
            retryDelay = Math.min(retryDelay * 2, 15_000);
            connect();
          }, retryDelay);
        }
      };
    } catch {
      onError?.(new Event('error'));
      if (!cancelled) {
        retryTimer = setTimeout(() => {
          retryDelay = Math.min(retryDelay * 2, 15_000);
          connect();
        }, retryDelay);
      }
    }
  }

  connect();

  return () => {
    cancelled = true;
    if (retryTimer !== null) clearTimeout(retryTimer);
    es?.close();
  };
}
