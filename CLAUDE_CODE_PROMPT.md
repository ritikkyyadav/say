# Claude Code Build Prompt — Say Desktop App (Wispr Flow-style UI on local Whisper)

Copy everything below into Claude Code (or Codex) at the repo root `/Users/ritikyadav890/Documents/Say`. The prompt is self-contained.

---

## Role

You are a senior macOS desktop application engineer. You are extending an existing, working Python dictation engine (`say/`) by adding a polished desktop GUI that ships as a native, installable macOS app today and a Windows app later. You do not rewrite the dictation engine. You wrap it.

## Context (read before editing anything)

Repo root: `/Users/ritikyadav890/Documents/Say`. The engine is already implemented and tested in `say/`:

- `say/controller.py` — `DictationController` state machine (idle → recording → processing → ready). Emits `DictationEvent` via a `status_callback`.
- `say/audio.py` — `AudioRecorder` with 500ms pre-roll, amplitude callback.
- `say/transcriber.py` — MLX Whisper local inference. Default model `mlx-community/whisper-large-v3-turbo`.
- `say/injector.py` + `text_inject_cgevent.py` / `text_inject_clipboard.py` — Unicode-correct text injection with auto-fallback.
- `say/listener_factory.py` — Fn-key listener with pynput `ctrl+space` fallback.
- `say/overlay.py` — floating waveform overlay (keep it).
- `say/app.py` — current rumps menu-bar entrypoint. **Do not delete.** It is the menu-bar agent that runs in the background.
- `say/metrics.py` — `MetricsLedger` writes one JSON line per transcription to `~/Library/Logs/Say/metrics.jsonl`. Schema: `{ts, event, audio_seconds, inference_seconds, model, method, success}`.
- Storage today: ephemeral. Audio is never written. Transcripts are injected and forgotten. No transcript history exists.

Constraints that must hold:

- 100% local. No cloud calls after the model download. No telemetry.
- Audio is never written to disk.
- The transcript history this UI shows is stored locally, in the user's home directory only.
- The menu-bar agent stays the source of truth for hotkeys and recording. The new desktop window is a viewer/dashboard that talks to it.
- macOS 13+ Apple Silicon target first. Windows is a secondary target — code must avoid macOS-only assumptions in the UI layer and storage layer.

## What you are building (scope, narrow)

A single desktop window app called **Say** that mimics the Wispr Flow design language and ships as `Say.app`. **Only two screens are in scope.** Do not add Dictionary, Snippets, Style, Transforms, Scratchpad, Settings, or any other sidebar entries beyond what's listed below. Render placeholder rows in the sidebar for the not-yet-built items so the layout matches, but they are non-interactive.

### Screen 1 — Home

A reverse-chronological transcript history grouped by day, like the Wispr Flow Home screen.

Layout:
- Top: `Welcome back, {first_name}`. First name comes from `$USER` or system `NSFullUserName`.
- Right rail card: "Total words" (lifetime), "Words per minute" (rolling 30-day median), "Streak (days)".
- Main feed: cards grouped by date header (`TODAY`, `YESTERDAY`, then `MONTH DD, YYYY`). Each card has: time of day (e.g. `08:52 PM`), full transcript text, and a quiet hover affordance to copy. No edit, no delete in v1 — just copy.
- Empty state: "No dictations yet. Press Fn (or your fallback hotkey) to start."

### Screen 2 — Insights

Two tabs: **Your Usage** (build this), **Your Voice** (placeholder, "Coming soon").

Your Usage layout matches the Wispr screenshot:
- Three KPI cards in a row:
  1. **Words per minute** with a half-donut gauge and a percentile pill (e.g. "Top 18%"). Percentile is a fixed reference cohort baked in as a constant table for now — document the assumption.
  2. **Fixes made by Whisper** — count of corrections logged in metrics. v1: report `0` for "words corrected" and `0` for "dictionary fixes" until the engine emits those events. Wire the UI; leave a TODO for the engine.
  3. **Total words dictated** with a "↘ N% this month" delta vs. previous 30 days.
- Below, two tiles side by side:
  1. **Desktop usage** by destination app — a list with horizontal bar gauges. Source: the `target_app` field added to the metrics ledger by this PR (see "Engine changes" below).
  2. **Streak heatmap** — GitHub-style 7-row × ~22-column grid covering the last ~6 months, shaded by daily dictation count.

The two screens above are the only screens that have to function in v1.

## Architecture decision (do this, do not redesign)

**Tauri 2.0 + React + Vite + TypeScript + Tailwind for the UI shell. The existing Python engine runs unchanged as a sidecar process. IPC is local HTTP on `127.0.0.1` over a random port chosen at startup.**

Why this stack:
- Tauri produces a real `.app` and a `.dmg` on macOS, and `.msi` / `.exe` on Windows, with native window chrome and code-signing hooks. Bundle size is small, memory is much lower than Electron.
- React + Tailwind is the fastest way to reproduce the Wispr Flow look exactly.
- The Python engine ships as a PyInstaller-bundled binary inside `Say.app/Contents/Resources/say-engine`. Tauri spawns it on launch and kills it on quit. No Python install required on the user's machine.

If you cannot make Tauri work in this environment, the only acceptable fallback is **PySide6** with QML/QtQuick styled to match. Do not use Electron. Do not use plain Tkinter. Do not introduce a web server on a fixed port. Do not require Docker, Node global installs, or Rust nightly.

### Repo layout to add

```
/Users/ritikyadav890/Documents/Say/
├── say/                       # existing engine — do not break
├── desktop/                   # NEW: Tauri shell + React UI
│   ├── src-tauri/
│   │   ├── tauri.conf.json
│   │   ├── Cargo.toml
│   │   └── src/
│   │       ├── main.rs        # spawns sidecar, exposes commands, handles tray
│   │       └── sidecar.rs     # Python process manager
│   ├── src/                   # React app
│   │   ├── App.tsx
│   │   ├── routes/
│   │   │   ├── Home.tsx
│   │   │   └── Insights.tsx
│   │   ├── components/        # Sidebar, KpiCard, TranscriptCard, StreakHeatmap, UsageGauge, RecordingDot
│   │   ├── lib/api.ts         # typed client for the Python sidecar HTTP API
│   │   └── styles/index.css   # Tailwind + Wispr-style tokens
│   ├── index.html
│   ├── package.json
│   └── vite.config.ts
└── say/server/                # NEW: HTTP server inside the Python engine
    ├── __init__.py
    ├── api.py                 # FastAPI app
    └── store.py               # SQLite transcript store
```

## Engine changes (minimal, additive)

1. **Persist transcripts.** Add `say/server/store.py`. SQLite at `~/Library/Application Support/Say/transcripts.db` on macOS, `%APPDATA%/Say/transcripts.db` on Windows. Schema:

   ```sql
   CREATE TABLE transcripts (
     id INTEGER PRIMARY KEY,
     ts_utc TEXT NOT NULL,           -- ISO-8601
     text TEXT NOT NULL,
     word_count INTEGER NOT NULL,
     audio_seconds REAL NOT NULL,
     inference_seconds REAL NOT NULL,
     model TEXT NOT NULL,
     inject_method TEXT NOT NULL,    -- accessibility | clipboard
     target_app TEXT,                -- frontmost-app bundle id at injection time
     success INTEGER NOT NULL
   );
   CREATE INDEX idx_ts ON transcripts(ts_utc);
   ```

   In `controller.py`, after a successful injection (the existing `get_ledger().record(...)` call), also write a row here. Capture `target_app` via `NSWorkspace.sharedWorkspace().frontmostApplication().bundleIdentifier()` on macOS. The metrics JSONL write stays — do not remove it.

2. **HTTP API.** Add `say/server/api.py` using FastAPI + uvicorn (already lightweight, ships in PyInstaller cleanly). Expose:

   ```
   GET  /health                      → {"ok": true, "state": "ready|recording|processing|error"}
   GET  /transcripts?limit=200&before=<iso>  → [{id, ts_utc, text, word_count, target_app}]
   GET  /stats/summary               → {total_words, wpm_30d, streak_days, fixes_count, words_this_month, words_prev_month}
   GET  /stats/desktop_usage?days=30 → [{target_app, count, share}]
   GET  /stats/heatmap?days=180      → [{date, count}]
   POST /transcripts/{id}/copy       → no-op, returns 204; for future telemetry
   GET  /events                      → SSE stream of DictationEvent state changes (for live recording dot in the UI)
   ```

   Bind to `127.0.0.1` on a port written to `~/Library/Application Support/Say/runtime.json` so the Tauri shell can read it. Reject any non-loopback connection at the socket level.

3. **Headless mode.** Add a `--server` flag to `app.py` that boots the rumps menu bar AND the HTTP server in one process. `--no-menubar` runs server only. The Tauri shell launches the engine as `say-engine --server --no-menubar`.

4. **First-name + WPM.** Add `GET /user/profile` returning `{first_name}` from `NSFullUserName` (mac) or `os.getlogin()` (cross-platform). WPM = `sum(word_count) / sum(audio_seconds/60)` over last 30 days.

5. Don't touch: `audio.py`, `transcriber.py`, `injector.py`, `overlay.py`, `listener_factory.py`, `controller.py` core state machine. The only edit to `controller.py` is the post-injection store write.

## UI implementation specifics

### Visual tokens (match Wispr Flow)

Tailwind config:
- Background: `#F5F1EA` (warm off-white)
- Card surface: `#FFFFFF` with `rgba(0,0,0,0.04)` border
- Text primary: `#111111`
- Text secondary: `#6B6B6B`
- Accent (active sidebar, gauge fill, streak max): `#0B5D5A` (dark teal)
- Streak heatmap scale: `#E5E1DA → #BFE3DE → #5FB6AC → #1A8076 → #0B5D5A`
- Sidebar width: 240px. Card radius: 16px. Body font: SF Pro / Inter, 14–15px.

### Sidebar

Logo + plan badge ("Basic") at the top. Items, in order (only Home and Insights are interactive):

```
Home, Insights, Dictionary (disabled), Snippets (disabled), Style (disabled),
Transforms (disabled), Scratchpad (disabled)
```

Bottom-of-sidebar: words-remaining card is **out of scope**. Replace with a small "Local-only · {n} transcripts" status block driven by `/stats/summary`. Below that: "Settings" and "Help" links — both open `~/Library/Logs/Say` and the README respectively, that's all.

### Components to build (functional spec, not pixel spec)

- `<Sidebar />` — static, highlights the active route.
- `<TranscriptCard time="08:52 PM" text="..." />` — the card from the screenshot. Hover reveals a "Copy" button that calls `navigator.clipboard.writeText(text)` and shows a 1.5s "Copied" toast.
- `<KpiCard title value subtitle right={<Pill />} chart={<HalfDonut /> | <DeltaBadge />} />`.
- `<UsageGauge label percent count />` — horizontal bar, label on left, percent fill, count on right.
- `<StreakHeatmap data={[{date, count}]} />` — d3-free implementation: 7 rows, ~26 columns, month labels above. Shade buckets at 0, 1–2, 3–4, 5–7, 8+ per day. Show tooltip on hover.
- `<RecordingDot />` — connected to `/events` SSE. Pulses red while engine state is `recording`. Otherwise hidden.

### Routing

`react-router-dom`. Routes: `/`, `/insights`. Nothing else.

### Data layer

`src/lib/api.ts` — single typed client. Reads the port from a Tauri-exposed command (`get_engine_port`) on first mount, then opens an `EventSource` for `/events`. All fetches go through it. Cache `/transcripts` and `/stats/*` with `@tanstack/react-query`, 30s `staleTime`.

## Tauri shell behavior

- One window, 1100×760, resizable, min 900×620.
- Closing the window does not quit. The menu-bar agent keeps running. Quit only from the menu-bar tray.
- Cmd+1 → Home. Cmd+2 → Insights. Cmd+W closes the window. Cmd+Q triggers full quit (window + sidecar + menu-bar agent).
- App icon: reuse `say/assets/` if present, otherwise generate a placeholder mic glyph.
- Sidecar lifecycle: on shell start, if no engine is detected (`runtime.json` is stale or no `/health` response within 3s), spawn `say-engine --server`. On shell quit, leave the engine running unless the user picked Cmd+Q from the tray.

## Packaging — macOS

- `npm run tauri build` produces `Say.app` and a DMG.
- The Python engine is bundled with PyInstaller via the existing `say/Say.spec` — extend it to include `say/server/`. The bundled binary is copied into `Say.app/Contents/Resources/say-engine` by a Tauri `beforeBundleCommand` hook.
- App is **unsigned** in CI. Document the right-click → Open first-run step in `desktop/README.md`. Provide hooks for `codesign` + `notarytool` reading `DEVELOPER_ID`, `APPLE_ID`, `APPLE_TEAM_ID`, `APPLE_APP_PASSWORD` from env.
- `Info.plist` keys: `NSMicrophoneUsageDescription`, `NSAppleEventsUsageDescription` (only what the engine actually needs). Do not request `com.apple.security.network.server` — the loopback socket is sufficient.
- Output: `desktop/src-tauri/target/release/bundle/dmg/Say_<version>_aarch64.dmg`.

## Packaging — Windows (stub for v2, do not block on it)

Add a `desktop/src-tauri/tauri.windows.conf.json` with the MSI bundle config. Replace mac-only paths in `say/server/store.py` with `platformdirs.user_data_dir('Say')`. Do not actually produce a Windows build in CI yet — just keep the door open.

## Acceptance criteria (the build is done when all of these are true)

1. `npm run tauri dev` from `desktop/` opens the window and shows the Home screen with real transcripts after I dictate twice via the menu-bar app.
2. The recording dot in the title bar pulses while I'm holding Fn.
3. Insights → Your Usage shows non-zero "Total words dictated", a non-zero WPM, the desktop-usage list with my real frontmost-app distribution, and a streak heatmap with at least today shaded.
4. `Cmd+W` closes the window. The menu-bar agent keeps recording. Reopening the window via the tray re-attaches and shows new transcripts.
5. `npm run tauri build` produces a DMG that, when installed on a fresh macOS user account with mic + accessibility granted, runs without any Python or Node installed.
6. Every visible string is real (no Lorem). Every chart is driven by `/stats/*`, not mocked.
7. `pytest` still passes for the engine. Add 5+ new tests for `say/server/store.py` (insert, list, summary stats, heatmap rollup, target-app capture).
8. Bundle size of `Say.app` is under 350 MB including the Whisper model.

## What you must NOT do

- Do not introduce cloud sync, login, or account features.
- Do not add the Dictionary / Snippets / Style / Transforms / Scratchpad screens.
- Do not change the hotkey behavior, the overlay, or the injector.
- Do not store audio.
- Do not use Electron, Flutter, or React Native.
- Do not edit `pyproject.toml` to add heavy deps beyond `fastapi`, `uvicorn`, `platformdirs`. SSE via `sse-starlette` is acceptable.
- Do not write more than the two screens specified.

## Working order (do exactly this sequence; commit at every checkpoint)

1. Add `say/server/store.py` + tests. Wire transcript persistence in `controller.py`. **Verify with a manual dictation that `transcripts.db` gets a row.**
2. Add `say/server/api.py` and the `--server` / `--no-menubar` flags. **Verify `curl 127.0.0.1:<port>/transcripts` returns the row.**
3. Scaffold Tauri + React + Tailwind in `desktop/`. Wire `get_engine_port` and the API client.
4. Build `<Sidebar />`, `<Home />`, `<TranscriptCard />`. Ship the Home screen end-to-end.
5. Build `<Insights />` with KPIs, usage gauges, streak heatmap.
6. Wire `/events` SSE and the recording dot.
7. PyInstaller bundle + Tauri `beforeBundleCommand` to embed the engine. Produce the DMG.
8. Update root `README.md` and `say/README.md` with the new run/build instructions.

Begin with step 1. After each step, print a short status of what changed, what you tested, and what's next, then continue. Do not pause for confirmation between steps.
