# Say

On-device macOS voice dictation. Hold Fn, speak, release — text appears in the focused app. No cloud, no API keys. See `README.md` for user-facing docs, screenshots, and privacy details.

> **Ships as "Oceanus".** The repo/codebase is still named `say`, but the built app's *identity* is Oceanus: bundle IDs `com.oceanus.dictation` (engine) / `com.oceanus.dictation.desktop` (shell), executables/bundles `Oceanus` / `Oceanus.app` / `OceanusEngine.app`, and UI/notification branding "Oceanus". This gives a clean macOS TCC (permissions) identity. **Internal data dirs stay `~/Library/Application Support/Say` and `~/Library/Logs/Say`** (the Python↔Rust `runtime.json` handshake depends on this path agreeing on both sides — do not rename it).

---

## Quick Commands

```bash
# ── Python engine (backend) ──
brew install portaudio ffmpeg                    # one-time prerequisites
python3 -m venv ~/.venvs/say && source ~/.venvs/say/bin/activate
pip install -r say/requirements.txt              # pinned deps
python say/app.py --server                       # menu-bar + HTTP API (dev mode)

# ── Desktop UI (frontend) ──
cd desktop && npm install
npm run tauri:dev                                # Vite dev server + Tauri window (port 1420)

# ── Testing ──
~/.venvs/say/bin/python -m pytest say/tests/     # 15 modules, 91 tests, <1s
SAY_CI=1 pytest say/tests/                       # CI mode: skips pyobjc-tap tests needing Accessibility

# ── Linting ──
ruff check say/                                  # line-length 100, rules: E/F/W/I/UP/B/C4/SIM
mypy say/                                        # py311, ignore_missing_imports=true, check_untyped_defs=false

# ── Production build ──
cd say && make all                               # PyInstaller + bundled model → Say.app
cd desktop && make all                           # engine + UI + Tauri → embedded desktop app
cd desktop && make dmg                           # → Oceanus_0.1.0_aarch64.dmg (unsigned)
```

---

## Architecture

```
┌─ Tauri Shell (Rust, macOS window) ─────────────────────────┐
│  React UI — Vite + React Query + SSE                       │
│  Cmd: get_engine_endpoint() → reads runtime.json           │
│  HTTP GET /health /transcripts /stats/* /listener /models   │
│  EventSource GET /events (real-time state stream)           │
└────────────────────────────────────────────────────────────┘
        │  HTTP on 127.0.0.1:<random_port>
        │  Bearer token (per-launch, from runtime.json)
        ▼
┌─ Python Engine ────────────────────────────────────────────┐
│  Fn key (CGEventTap) → AudioRecorder → mlx-whisper         │
│  → Injector (CGEvent or clipboard) → focused app           │
│  State: IDLE → LOADING → READY → RECORDING → PROCESSING   │
│         ↘ ERROR (any stage) ↗                              │
│  FastAPI server → SQLite store → SSE event bus              │
└────────────────────────────────────────────────────────────┘
```

**Two modes:** Dev: `python say/app.py --server` (menu bar + API). Production: Tauri spawns `SayEngine.app --no-menubar` as sidecar, kills on Cmd+Q.

**State machine** (`controller.py` `DictationState` enum): `IDLE` → `LOADING` (model load) → `READY` (waiting for hotkey) → `RECORDING` (mic active) → `PROCESSING` (transcribing) → back to `READY`. Any stage can → `ERROR`.

**Hotkey priority**: Fn key via CGEventTap (preferred) → ctrl+space via pynput (fallback). `listener_factory.py` handles auto-fallback. Fn override neutralizes macOS globe-key action via `AppleFnUsageType=0` in NSGlobalDomain.

---

## File Map — Python Engine (`say/`)

| File | Purpose | Key Exports |
|------|---------|-------------|
| `app.py` | Menu-bar entry point (rumps). Orchestrates hotkey, overlay, recorder, controller, server, system events. | `SayApp(rumps.App)`, `main()`, `_run_server_only()` |
| `main.py` | Terminal/debug entry point (no menu bar). | `main()` |
| `controller.py` | State machine coordinating record → transcribe → inject pipeline. Thread-safe via RLock. | `DictationController`, `DictationState(Enum)`, `DictationEvent(dataclass)` |
| `audio.py` | 16kHz mono mic stream, **live only during a recording** (opens on `start()`, closes ~0.6s after `stop()` via debounced `_schedule_close` so the macOS mic indicator goes dark when idle). Back-to-back dictations reuse the open stream; reopen retries w/ backoff + PortAudio reset to dodge CoreAudio's -9986 race. | `AudioRecorder` — `.start()`, `.stop() → np.ndarray`, `.cancel()`, `.shutdown()`, `.set_amplitude_callback()`, `.set_event_callback()` |
| `transcriber.py` | mlx-whisper wrapper. Lazy load, warmup, hallucination filter, model swap. | `Transcriber` — `.load()`, `.transcribe(audio) → str`, `.swap_model(path)`, `.predownload(cb)` |
| `model_catalog.py` | **Source of truth** for installable STT models + HF-cache state. Pure/testable (honors `HF_HUB_CACHE`). `config.VALID_MODELS` must equal `CATALOG_IDS` (guarded by test). | `ModelInfo`, `CATALOG`, `CATALOG_IDS`, `catalog()`, `get(id)`, `is_downloaded(id)`, `disk_usage_bytes(id)`, `bundled_dir(id)`, `to_dict(info)` |
| `model_manager.py` | Runtime model ops: download (real-byte progress + disk pre-check + true cancel via tqdm-abort), delete, live-swap. Singleton holds the live controller so changes apply with no relaunch. | `ModelManager` — `.list_models()`, `.set_active(id)`, `.start_download(id)`, `.cancel_download(id)`, `.download_status(id)`, `.delete(id)`, `.register_engine(ctrl)`, `.total_disk_bytes()`; `get_manager()`, `InsufficientStorage` |
| `injector.py` | Routes text to focused app. Auto tries CGEvent first, clipboard fallback. | `inject(text) → InjectionResult(success, method, error)` |
| `text_inject_cgevent.py` | CGEvent Unicode injection. Chunks text into ~20 UTF-16 units per event, 2ms inter-chunk delay. | `inject_text_cgevent(text) → InjectResult` |
| `text_inject_clipboard.py` | NSPasteboard + Cmd+V fallback. Saves/restores clipboard. 30ms pre-paste, 200ms post-paste settle. | `inject_text_clipboard(text) → InjectResult` |
| `hotkey.py` | pynput-based global hotkey listener. Tap-to-toggle: hold <0.30s → hands-free, hold ≥0.30s → PTT, Esc cancels. | `HotkeyListener` — `.start(timeout)`, `.stop()`, `.force_idle()`, `.mode`, `parse_hotkey(str)` |
| `hotkey_fn.py` | CGEventTap Fn-key listener. Fn hold → PTT, Fn+Space → hands-free toggle, Esc cancels. Swallows Fn/Space/Esc events. | `FnHotkeyListener` — `.start(timeout)`, `.stop()`, `.force_idle()`, `.mode` |
| `listener_factory.py` | Auto-selects best hotkey listener. Tries Fn first, falls back to pynput. | `make_listener(on_start, on_stop, on_cancel) → ListenerInfo(listener, kind, label, fell_back)` |
| `fn_override.py` | Temporarily sets AppleFnUsageType=0 to suppress macOS globe-key action. Crash-safe: persists backup to disk before mutation. | `acquire()`, `release()` |
| `overlay.py` | Floating waveform pill (NSView). Compact 178×46pt capsule, ✕ cancel / ✓ finish discs, 18 tightly-packed bars on a center-weighted bell envelope, **critically-damped spring** per-bar height @ 60fps (lively overshoot, snappy attack), wake sound (Tink.aiff). | `DictationOverlay` — `.show_idle_dot()`, `.show_recording(mode)`, `.show_processing()`, `.hide()`, `.push_amplitude(float)` |
| `config.py` | **Single source of truth** for shipped defaults. No hardcoded values elsewhere. | Constants: `MODEL`, `HOTKEY`, `SAMPLE_RATE`, `MAX_RECORDING_SECONDS`, `PRE_ROLL_SECONDS`, `INJECT_METHOD`, `LANGUAGE`, etc. |
| `settings.py` | User overrides persisted to `settings.json`. Mirrors changes to `config` module at runtime. | `get(key)`, `set_value(key, val)`, `apply_to_config()`, `is_first_run()`, `mark_first_run_complete()` |
| `settings_window.py` | Preferences NSWindow. Live binding — no Save button, each change persists immediately. 540×620pt. | `_SettingsController(NSView)` |
| `permissions.py` | Environment checks (OS, Python, CPU, modules) and deep-links to macOS Privacy Settings. | `check_environment() → [EnvironmentCheck]`, `open_privacy_settings(section)` |
| `validation.py` | Validates config at startup. Checks MODEL, HOTKEY, INJECT_METHOD, SAMPLE_RATE, etc. | `validate_config() → [str]`, `validate_or_raise()` |
| `brand.py` | Design tokens: color palettes (Sky, Brown, Pink, Neutral, Overlay), typography helpers, asset loading. | `font(size, weight)`, `mono(size)`, `logo_image()`, `menubar_template()` |
| `wizard.py` | First-run onboarding NSWindow. 3 permission cards (Mic, Accessibility, Input Monitoring) with Settings deep-links. | `run_wizard(on_finish)` |
| `first_run.py` | Onboarding router. Tries branded wizard, falls back to alert chain. | `run_wizard(alert_fn, on_finish)` |
| `menubar_icon.py` | Generates and caches menu-bar icons with status badges (recording dot, processing rings, error cross). | `IconState(Enum)`, `icon_for(state) → NSImage`, `icon_path_for(state)` |
| `device_watcher.py` | Polls default input device every 2s. Notifies on change (e.g., AirPods unplugged). | `DeviceWatcher(on_change)` — `.start()`, `.stop()` |
| `system_events.py` | Subscribes to screen-lock, sleep/wake NSNotifications. Cancels active recordings on lock/sleep. | `SystemEventWatcher(callback)` — `.start()`, `.stop()` |
| `notifications.py` | macOS native notifications via osascript. Respects `SHOW_NOTIFICATIONS` flag. | `notify(title, text)`, `notify_transcription_complete(text)`, `notify_error(msg)` |
| `logging_setup.py` | stderr + rotating file log (`~/Library/Logs/Say/say.log`, 10MB × 5). Crash handler for uncaught exceptions. | `configure_logging(level)`, `get_logger(name)`, `install_crash_handler()` |
| `metrics.py` | JSONL ledger at `~/Library/Logs/Say/metrics.jsonl`. Records transcription timing + errors. | `get_ledger() → MetricsLedger`, `.record(**fields)` |
| `single_instance.py` | fcntl-based lock at `~/Library/Application Support/Say/say.lock`. Fail-open: if lock can't be created, runs anyway. | `acquire() → bool`, `release()` |

## File Map — Server Layer (`say/server/`)

| File | Purpose | Key Exports |
|------|---------|-------------|
| `api.py` | FastAPI app. 14 endpoints (incl. model mgmt) + SSE stream. CORS methods GET/POST/DELETE; origins `tauri://localhost`, `http://localhost:1420`, `http://127.0.0.1:1420`. Auth: optional Bearer token. | `create_app(store, auth_token) → FastAPI`, `serve_blocking()`, `serve_in_thread()`, `ActiveModelRequest` |
| `store.py` | SQLite at `~/Library/Application Support/Say/transcripts.db`. Transcript CRUD + aggregated stats (WPM, streaks, per-app usage). | `TranscriptStore` — `.insert()`, `.list(limit, before)`, `.summary()`, `.desktop_usage(days)`, `.heatmap(days)`, `get_frontmost_app()` |
| `events.py` | In-process pub/sub bus. Fans out DictationEvent to SSE clients. Queue size 64, slow consumers dropped. | `EventBus` — `.publish_threadsafe(payload)`, `.subscribe()`, `.snapshot()`, `get_bus()` |
| `runtime.py` | Writes `runtime.json` handshake (`host`, `port`, `token`, `pid`). File mode 0600. | `write_runtime(port, token, pid)`, `read_runtime()`, `clear_runtime()` |

## File Map — Desktop UI (`desktop/src/`)

| File | Purpose | Key Exports / Props |
|------|---------|---------------------|
| `main.tsx` | React entry. QueryClient with 30s staleTime, 4 retries (exponential backoff 1s→8s). BrowserRouter. | — |
| `App.tsx` | Root layout. Sidebar + header (RecordingDot, HotkeyPill) + Routes (`/` → Home, `/insights` → Insights, `/models` → Models). Keyboard: Cmd+1/2/3. | `App()` |
| `routes/Home.tsx` | Transcript feed. Queries `api.transcripts(200)`, groups by day via `groupByDay()`. 3 stat tiles (words, WPM, streak). PermissionsBanner. | `Home()`, `StatTile({ label, value })` |
| `routes/Insights.tsx` | Analytics. Tabs: "Your Usage" (3 KPI cards + per-app bars + heatmap) / "Your Voice" (coming soon). WPM percentile via `topPercentForWpm()`. | `Insights()`, `YourUsage()`, `YourVoice()` |
| `routes/Models.tsx` | Model picker. Lists catalog via `api.models`, polls every 800ms while any download runs. Privacy + total-on-disk strip. | `Models()` |
| `components/KpiCard.tsx` | Metric card with title, value, subtitle, right badge, optional chart. Also exports `HalfDonut({ percent })` and `DeltaBadge({ deltaPct })`. | `KpiCard({ title, value, subtitle?, right?, chart? })` |
| `components/ModelCard.tsx` | One model row: identity, speed/accuracy dot-meters, state-driven action (Use / Download+progress / Remove / Active). Mutations invalidate `['models']`. | `ModelCard({ model: ModelInfo })`, `formatBytes(n)` |
| `components/TranscriptCard.tsx` | Single transcript row. Copy button with "Copied" toast (1.5s). Calls `api.copyTranscript(id)`. | `TranscriptCard({ row: Transcript })` |
| `components/RecordingDot.tsx` | Red pulsing dot in header. Shows only when `useEngineState() === 'recording'`. | `RecordingDot()` |
| `components/Sidebar.tsx` | Left nav. Home, Insights, Models (active). Dictionary/Snippets/Style/Transforms/Scratchpad (disabled, "soon"). Transcript count footer. | `Sidebar()` |
| `components/StreakHeatmap.tsx` | GitHub-style SVG heatmap. 7-row week grid, 5-shade dusty-rose scale. Shows active days + total dictations. | `StreakHeatmap({ data: HeatmapCell[] })` |
| `components/UsageGauge.tsx` | Horizontal bar for per-app usage share. | `UsageGauge({ label, share, count })` |
| `components/HotkeyPill.tsx` | Hotkey status pill. Queries `/listener` every 4s. Green = Fn ready, amber = fallback, muted = connecting. | `HotkeyPill()` |
| `components/PermissionsBanner.tsx` | Permission guide banner. 3 panes (Mic, Accessibility, Input Monitoring) with "Open Settings" buttons via Tauri command. Auto-hides when Fn active. | `PermissionsBanner()` |
| `lib/api.ts` | Typed HTTP client + SSE subscriber. Endpoint discovery via Tauri `get_engine_endpoint` or `VITE_DEV_ENGINE_PORT` env. `sendJSON` for POST/DELETE (surfaces FastAPI `detail`). Auto-reconnect SSE with exponential backoff (1s→15s max). | `api` object (incl. `models`, `setActiveModel`, `downloadModel`, `deleteModel`), `subscribeEvents()`, `openPrivacySettings()`, types: `Transcript`, `ModelInfo`, `ModelsResponse`, `ListenerState`, `StatsSummary`, etc. |
| `lib/useEngineState.ts` | React hook. Subscribes to SSE, returns current `DictationStateName`. Cleans up on unmount. | `useEngineState() → DictationStateName` |
| `lib/grouping.ts` | Groups transcripts by local-timezone day. Labels: TODAY / YESTERDAY / MONTH DAY, YEAR. | `groupByDay(rows, now?) → DayGroup[]` |
| `lib/appNames.ts` | Bundle ID → display name map (30+ common macOS apps). Falls back to reverse-DNS suffix. | `appLabel(bundleOrName?) → string` |

## File Map — Tauri Shell (`desktop/src-tauri/src/`)

| File | Purpose | Key Exports |
|------|---------|-------------|
| `main.rs` | Window lifecycle. Spawns engine sidecar on setup, kills on ExitRequested. Cmd+W hides window (doesn't close). Commands: `get_engine_endpoint`, `open_privacy_settings`, `reveal_logs_dir`. | `EngineEndpoint { host, port, token }` |
| `sidecar.rs` | Engine process manager. Finds SayEngine.app binary, spawns with `--no-menubar`, reads `runtime.json`. Graceful SIGTERM → 3s wait → SIGKILL. | `Sidecar` — `::spawn()`, `.kill()`, `is_engine_alive()`, `read_runtime_info()` |

---

## API Contract

**Base**: `http://127.0.0.1:<port>` (port from `runtime.json`). **Auth**: `Authorization: Bearer <token>` header, or `?token=<token>` query param for SSE.

| Endpoint | Method | Params | Response |
|----------|--------|--------|----------|
| `/health` | GET | — | `{ ok: bool, state: str }` |
| `/listener` | GET | — | `ListenerState` |
| `/user/profile` | GET | — | `{ first_name: str }` |
| `/transcripts` | GET | `limit=200`, `before?=<iso>` | `Transcript[]` |
| `/stats/summary` | GET | — | `StatsSummary` |
| `/stats/desktop_usage` | GET | `days=30` | `[{ target_app, count, share }]` |
| `/stats/heatmap` | GET | `days=180` | `[{ date, count }]` |
| `/transcripts/{id}/copy` | POST | — | `{}` (telemetry stub) |
| `/models` | GET | — | `{ active: str, cache_bytes: int, models: ModelInfo[] }` |
| `/models/active` | POST | body `{ id }` | `{ active: str }` — persists + live-swaps engine; 400 if unknown |
| `/models/{id}/download` | POST | `id` is `:path` (has `/`) | `ModelDownload` — starts bg download; 404 unknown, 507 if disk full |
| `/models/{id}/cancel` | POST | `id` is `:path` | `{ cancelled: bool }` — aborts an in-flight download, cleans partial |
| `/models/{id}` | DELETE | `id` is `:path` | `{}` — 409 if active/bundled/downloading |
| `/events` | GET (SSE) | `token?` | Stream of `DictationStateEvent` |

**Key Types:**
```typescript
type DictationStateName = 'idle' | 'loading' | 'ready' | 'recording' | 'processing' | 'error';
type DictationStateEvent = { state: DictationStateName; message?: string; mode?: 'ptt' | 'hands_free'; error?: string };
type Transcript = { id: number; ts_utc: string; text: string; word_count: number; audio_seconds: number; inference_seconds: number; model: string; inject_method: string; target_app: string | null; success: boolean };
type ListenerState = { kind: 'fn' | 'legacy' | null; label: string | null; fell_back: boolean; fn_error: string | null; started: boolean; accessibility_trusted: boolean };
type StatsSummary = { total_words: number; wpm_30d: number; streak_days: number; fixes_count: number; words_this_month: number; words_prev_month: number; transcript_count: number };
type ModelDownload = { state: 'idle' | 'downloading' | 'done' | 'error'; percent: number; error: string | null };
type ModelInfo = { id: string; name: string; family: string; params: string; download_bytes: number; speed: number; accuracy: number; languages: string; recommended: boolean; order: number; description: string; downloaded: boolean; bundled: boolean; size_bytes: number; active: boolean; download: ModelDownload };
type ModelsResponse = { active: string; cache_bytes: number; models: ModelInfo[] };
```

---

## Code Conventions

**Python (`say/`):**
- Python ≥3.11. Ruff: line-length 100, rules E/F/W/I/UP/B/C4/SIM. Ignores: E501, B008, SIM105, SIM117, UP038, B904, C401, UP042. Tests ignore E501.
- mypy: py311, `ignore_missing_imports=true`, `check_untyped_defs=false`.
- `config.py` is the single source of truth — never hardcode values that belong there.
- `settings.py` handles user overrides — reads from `settings.json`, mirrors to `config` at runtime.
- `model_catalog.py` is the source of truth for installable models; `config.VALID_MODELS` must equal `model_catalog.CATALOG_IDS` (test-guarded). Route model changes through `model_manager.set_active()` so the running engine swaps live (no relaunch); the engine registers itself via `register_engine()` at startup.
- Thread safety via `RLock` for state, `Lock` for file I/O. Audio callbacks run on sounddevice thread — never block them.
- Module-level singletons with lock: `get_bus()`, `get_ledger()`, `TranscriptStore()`.
- State changes always emit `DictationEvent` via both `status_callback` and `EventBus.publish_threadsafe()`.
- **Mic stream is recording-scoped**, not always-on. Never reopen the `sd.InputStream` immediately after closing — CoreAudio releases the device asynchronously and a fast reopen throws `PaErrorCode -9986`. `audio.py` handles this via debounced close (`_IDLE_CLOSE_DELAY`) + retry/PortAudio-reset in `_ensure_stream_open`; don't bypass them.
- **Hallucination filter is punctuation-insensitive.** Whisper emits silence-hallucinations with trailing punctuation (`"Thank you."`). Compare via `transcriber._normalize_for_filter()` (strips surrounding `.!?,…"'\`-`), and keep `_HALLUCINATION_FILTER` phrases punctuation-free.
- **App identity ≠ data path.** Built app is "Oceanus" (`com.oceanus.*`) but on-disk state lives under `…/Say/`. Changing one without the other breaks either TCC identity or the `runtime.json` handshake.

**TypeScript (`desktop/src/`):**
- TypeScript strict mode, target ES2021, path alias `@/*` → `src/*`.
- React Query for all data fetching (30s staleTime, 4 retries with exponential backoff).
- SSE for real-time state — `useEngineState()` hook, auto-reconnect with exponential backoff capped at 15s.
- Tailwind for styling. Brand color: dusty rose (`accent` in tailwind.config.js). Background: `canvas` (#F4EFE6). Font: Inter/SF Pro.
- Tauri commands called via lazy-loaded `@tauri-apps/api`. Dev fallback reads `VITE_DEV_ENGINE_PORT` from `runtime.json` in vite.config.ts.

**Rust (`desktop/src-tauri/`):**
- Edition 2021, MSRV 1.77. Tauri 2 with `macos-private-api` and `tray-icon` features.
- Unix-specific: `libc` for SIGTERM/SIGKILL sidecar management.

---

## Key Constants (`config.py`)

```python
MODEL = 'mlx-community/whisper-large-v3-turbo'  # fast daily driver
HOTKEY = 'fn'                                     # prefer Fn listener
HOTKEY_LEGACY = 'ctrl+space'                      # pynput fallback
SAMPLE_RATE = 16_000                              # Whisper requirement
MAX_RECORDING_SECONDS = 60
MIN_AUDIO_SECONDS = 0.4                           # reject too-short clips
PRE_ROLL_SECONDS = 0.5                            # ring buffer before hotkey
DEVICE_WATCH_INTERVAL_S = 2.0                     # input device polling
LANGUAGE = 'en'                                   # or None for auto-detect
INJECT_METHOD = 'auto'                            # 'auto' | 'accessibility' | 'clipboard'
INITIAL_PROMPT = ''                               # Whisper vocabulary bias (<100 tokens)
SHOW_NOTIFICATIONS = True
OVERRIDE_FN_KEY_ACTION = True                     # neutralize globe-key
PREFER_LEGACY_HOTKEY = False
```

User overrides persist to `~/Library/Application Support/Say/settings.json`. Whitelisted keys: MODEL, LANGUAGE, INJECT_METHOD, INITIAL_PROMPT, SHOW_NOTIFICATIONS, SHOW_OVERLAY, OVERLAY_POSITION, PRE_ROLL_SECONDS, MAX_RECORDING_SECONDS, MIN_AUDIO_SECONDS, DEVICE_WATCH_INTERVAL_S.

---

## Data Storage

**SQLite** at `~/Library/Application Support/Say/transcripts.db`:
```sql
CREATE TABLE transcripts (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  ts_utc          TEXT NOT NULL,          -- ISO 8601
  text            TEXT NOT NULL,
  word_count      INTEGER NOT NULL,
  audio_seconds   REAL NOT NULL,
  inference_seconds REAL NOT NULL,
  model           TEXT NOT NULL,
  inject_method   TEXT NOT NULL,          -- 'accessibility' | 'clipboard'
  target_app      TEXT,                   -- macOS bundle ID or NULL
  success         INTEGER NOT NULL        -- 0 or 1
);
CREATE INDEX idx_ts ON transcripts(ts_utc);
```

**Other files:**
| Path | Contents | Mode |
|------|----------|------|
| `~/Library/Application Support/Say/runtime.json` | `{ host, port, token, pid }` — per-launch handshake | 0600 |
| `~/Library/Application Support/Say/settings.json` | User config overrides | default |
| `~/Library/Application Support/Say/say.lock` | Single-instance advisory lock (fcntl) | default |
| `~/Library/Application Support/Say/fn_usage_backup.json` | Original AppleFnUsageType (crash-safe restore) | default |
| `~/Library/Application Support/Say/first_run_complete` | Marker file (onboarding done) | default |
| `~/Library/Logs/Say/say.log` | Rotating log (10MB × 5 files) | default |
| `~/Library/Logs/Say/metrics.jsonl` | Transcription timing + error records (JSON per line) | default |
| `~/.cache/huggingface/hub/models--mlx-community--whisper-large-v3-turbo/` | Cached Whisper model | default |

Audio is **never written to disk** — stays in-memory ring buffer only.

---

## Permissions & Security

| Permission | Required By | Why |
|-----------|-------------|-----|
| Microphone | Engine | Audio capture via sounddevice |
| Accessibility | Engine | CGEvent keystroke injection + Fn CGEventTap listener |
| Input Monitoring | Engine (Fn mode only) | CGEventTap requires it for Fn key detection |

Security model: loopback-only API (127.0.0.1), per-launch random Bearer token, CORS restricted to `tauri://localhost` + dev origins, audio never persisted, clipboard restored after injection, `runtime.json` mode 0600.

---

## Testing

15 test modules, 91 tests, <1s runtime. Run: `pytest say/tests/`

| Module | Tests | Covers |
|--------|-------|--------|
| `test_api.py` | 14 | FastAPI endpoints, auth, CORS, model mgmt routes (incl. 507/cancel) |
| `test_audio.py` | 5 | AudioRecorder pre-roll, event dispatch |
| `test_model_catalog.py` | 8 | Catalog data, HF-cache state, disk usage, config sync |
| `test_model_manager.py` | 12 | Model swap, download progress/cancel, disk pre-check, delete guards |
| `test_controller.py` | 4 | DictationController state transitions |
| `test_hotkey.py` | 4 | HotkeyListener tap-to-toggle, hotkey parsing |
| `test_hotkey_fn.py` | 5 | FnHotkeyListener CGEventTap behavior |
| `test_injector.py` | 5 | Injection routing (auto/accessibility/clipboard) |
| `test_listener_factory.py` | 3 | Auto-fallback from Fn to pynput |
| `test_settings.py` | 5 | Persistence, apply_to_config, first-run marker |
| `test_single_instance.py` | 3 | fcntl lock acquisition/release |
| `test_store.py` | 7 | SQLite CRUD, stats aggregation |
| `test_text_inject.py` | 5 | CGEvent + clipboard injection |
| `test_transcriber.py` | 7 | Model loading, hallucination filter (incl. punctuation-normalized variants), swap |
| `test_validation.py` | 4 | Config validation rules |

CI: GitHub Actions on `macos-14` (arm64), Python 3.12. `SAY_CI=1` skips tests needing Accessibility/Input Monitoring grants.

---

## Build Pipeline

### Python Engine (`say/Makefile`)

| Target | What it does |
|--------|-------------|
| `make all` | install → predownload → build → bundle-model → dmg |
| `make install` | pip install requirements.txt + pyinstaller + create-dmg-cli |
| `make predownload` | Download Whisper model from HuggingFace |
| `make build` | PyInstaller via `Say.spec` → `dist/Say.app` (arm64, LSUIElement=true) |
| `make bundle-model` | Copy HF model cache into `Say.app/Contents/Resources/models/` |
| `make dmg` | create-dmg → `Say-v0.1.0-unsigned.dmg` |
| `make sign` | Codesign with `$DEVELOPER_ID` (optional, exits cleanly if unset) |
| `make notarize` | Submit to Apple notary with `$APPLE_ID`, `$APPLE_TEAM_ID`, `$APPLE_APP_PASSWORD` |
| `make clean` | Remove build/ and dist/ |

Env vars: `SAY_PYTHON` (default `~/.venvs/say/bin/python`), `SAY_MODEL` (default `mlx-community/whisper-large-v3-turbo`).

### Desktop Shell (`desktop/Makefile`)

| Target | What it does |
|--------|-------------|
| `make all` | engine → ui-build → app → embed-engine → patch-plist |
| `make engine` | Builds Python engine via `make -C ../say all` |
| `make ui-build` | `npm run build` (Vite) |
| `make app` | `npm run tauri:build` |
| `make embed-engine` | Copies `Oceanus.app` (engine) into shell's `Contents/Resources/OceanusEngine.app` |
| `make patch-plist` | Adds usage strings to Info.plist, renames executable to "Say", re-signs |
| `make dmg` | create-dmg → `Say_0.1.0_aarch64.dmg` |
| `make clean` | Remove node_modules, dist, src-tauri/target, src-tauri/gen |

### PyInstaller Spec (`say/Say.spec`)

Target arch: arm64. Bundle ID: `com.oceanus.dictation`. App/binary name: `Oceanus` (`name='Oceanus'` in `Say.spec`; spec filename unchanged). LSUIElement: true (no Dock icon). Hidden imports include: mlx.*, tiktoken_ext.*, rumps, pynput._darwin, fastapi, uvicorn, sse_starlette, server.*. Collects data from mlx_whisper + tiktoken_ext, binaries from mlx (Metal libs).

---

## Self-Improvement Protocol

This CLAUDE.md is a living document. Agents **must** update it when they:

1. **Add a new file** → Add a row to the appropriate File Map table with purpose and key exports.
2. **Add/change an API endpoint** → Update the API Contract table and types.
3. **Add/change a config constant** → Update the Key Constants section.
4. **Change the state machine** → Update the Architecture section's state diagram.
5. **Add a new test module** → Update the Testing table and counts.
6. **Add a new Makefile target** → Update the Build Pipeline section.
7. **Change the SQLite schema** → Update the Data Storage SQL block.
8. **Add a new dependency** → No action needed (deps are in pyproject.toml/package.json, not here).
9. **Discover a non-obvious pattern or gotcha** → Add to Code Conventions.

**When updating**: Keep the same format (tables, not prose). Keep descriptions to one line per row. The goal is density — every line should save a future agent from reading a file.
