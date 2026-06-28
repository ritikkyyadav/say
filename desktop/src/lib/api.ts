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
};

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
  transcripts: (limit = 200, before?: string) =>
    getJSON<Transcript[]>(
      `/transcripts?limit=${limit}${before ? `&before=${encodeURIComponent(before)}` : ''}`,
    ),
  summary: () => getJSON<StatsSummary>('/stats/summary'),
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

// Reveal a file/folder in Finder (selecting it). Used after exporting a ZIP.
export async function revealPath(path: string): Promise<void> {
  try {
    await invokeTauri<void>('reveal_path', { path });
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
