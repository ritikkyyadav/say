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

export async function getEngineEndpoint(force = false): Promise<EngineEndpoint> {
  if (!force && cachedEndpoint) return cachedEndpoint;
  try {
    const ep = await invokeTauri<EngineEndpoint | null>('get_engine_endpoint');
    if (ep && ep.port) {
      cachedEndpoint = ep;
      return ep;
    }
  } catch {
    // Fall through to dev fallback below.
  }
  // Dev fallback: try the conventional dev port + no token.
  const fallback = (import.meta as any).env?.VITE_DEV_ENGINE_PORT;
  if (fallback) {
    cachedEndpoint = { host: '127.0.0.1', port: Number(fallback), token: '' };
    return cachedEndpoint;
  }
  throw new Error(
    'Cannot reach the dictation engine. Make sure the menu-bar agent is running with --server.',
  );
}

function authHeaders(ep: EngineEndpoint): HeadersInit {
  return ep.token ? { Authorization: `Bearer ${ep.token}` } : {};
}

function url(ep: EngineEndpoint, path: string): string {
  return `http://${ep.host}:${ep.port}${path}`;
}

async function getJSON<T>(path: string): Promise<T> {
  const ep = await getEngineEndpoint();
  const r = await fetch(url(ep, path), { headers: authHeaders(ep) });
  if (!r.ok) {
    throw new Error(`${path} → HTTP ${r.status}`);
  }
  return (await r.json()) as T;
}

export const api = {
  health: () => getJSON<{ ok: boolean; state: DictationStateName }>('/health'),
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
};

// SSE subscription. Returns a cleanup function.
export function subscribeEvents(
  onEvent: (e: DictationStateEvent) => void,
  onError?: (err: Event) => void,
): () => void {
  let cancelled = false;
  let es: EventSource | null = null;

  (async () => {
    try {
      const ep = await getEngineEndpoint();
      if (cancelled) return;
      // EventSource cannot set Authorization headers, so we pass the
      // token via query string. The API supports both.
      const u = url(ep, `/events${ep.token ? `?token=${encodeURIComponent(ep.token)}` : ''}`);
      es = new EventSource(u);
      es.addEventListener('state', (ev) => {
        try {
          onEvent(JSON.parse((ev as MessageEvent).data));
        } catch {
          /* ignore */
        }
      });
      es.onerror = (ev) => onError?.(ev);
    } catch (err) {
      onError?.(err as Event);
    }
  })();

  return () => {
    cancelled = true;
    es?.close();
  };
}
