# Say — Desktop UI

Local-first dictation desktop window. Tauri 2 shell over the existing
Python `say/` engine. Two screens: **Home** (transcript history grouped
by day) and **Insights** (WPM, total words, desktop usage, streak
heatmap).

The desktop window is a viewer over the menu-bar agent. The agent owns
hotkeys, recording, transcription, and text injection. The window opens
a loopback HTTP socket against the agent and reads transcripts back.

## Architecture

```
┌─ Tauri shell (Rust) ─────────────────────────────────────────┐
│   - one window, hides on Cmd+W, kills sidecar on Cmd+Q       │
│   - exposes `get_engine_endpoint` to the React app           │
│   - spawns `SayEngine.app/Contents/MacOS/Say --no-menubar`   │
└──────────────────────────────────────────────────────────────┘
                       │  HTTP on 127.0.0.1:<random>
                       ▼
┌─ Python engine (PyInstaller bundled) ────────────────────────┐
│   say.app + audio.py + transcriber.py + controller.py        │
│   say/server/api.py    — FastAPI app                         │
│   say/server/store.py  — SQLite at ~/Library/Application     │
│                          Support/Say/transcripts.db          │
└──────────────────────────────────────────────────────────────┘
```

## Dev loop

```sh
# 1. Engine in --server mode (talks to the same DB the desktop will read)
~/.venvs/say/bin/python say/app.py --server

# 2. Desktop UI (in another shell)
cd desktop
npm install
npm run tauri:dev
```

`npm run tauri:dev` will boot the Rust shell + Vite dev server (port
1420) and open a window. The shell reuses the running engine's
`runtime.json` if it's fresh; otherwise it spawns a sidecar (only
useful in the bundled build).

## Production build

The full pipeline produces a `Say.app` containing both the desktop
shell and the bundled Python engine + Whisper model.

```sh
cd desktop
make all
make dmg     # optional — needs `brew install create-dmg`
```

`make all` does:

1. `make engine` — runs `make all` in `say/` to PyInstaller-bundle the
   engine and copy the model into the resulting `Say.app`.
2. `make ui-build` — `vite build` the React app.
3. `make app` — `tauri build` produces `desktop/src-tauri/target/release/bundle/macos/Say.app`.
4. `make embed-engine` — rsyncs the engine's `Say.app` into the desktop
   `Say.app`'s `Contents/Resources/SayEngine.app`.

The Rust sidecar manager looks for `SayEngine.app/Contents/MacOS/Say`
inside its own resource directory and spawns it with `--no-menubar`.

## First-run on macOS (unsigned)

The DMG produced above is **unsigned** unless you set the codesign env
vars. Without a Developer ID, the user must right-click → Open the
first time to bypass Gatekeeper. To sign + notarize, set:

```sh
export DEVELOPER_ID="Developer ID Application: Your Name (TEAMID)"
export APPLE_ID="..."
export APPLE_TEAM_ID="..."
export APPLE_APP_PASSWORD="..."
```

Codesign and notarization happen against the embedded engine .app as
well — both must be signed before stapling.

## Permissions

Granted at the OS level on first use:

- **Microphone** — for audio capture (engine).
- **Accessibility** — for keystroke injection (engine).
- **Input Monitoring** — only if the Fn-key listener is enabled.

The desktop window itself does not request any permissions.

## Files

```
desktop/
├── Makefile                — engine + tauri pipeline
├── package.json            — vite + tauri scripts
├── src/
│   ├── App.tsx             — router + Cmd+1/Cmd+2 nav
│   ├── components/         — Sidebar, KpiCard, TranscriptCard, etc.
│   ├── lib/
│   │   ├── api.ts          — typed HTTP client + SSE subscriber
│   │   ├── grouping.ts     — day-bucketing for the Home feed
│   │   └── useEngineState.ts — Recording-dot wiring
│   ├── routes/Home.tsx
│   └── routes/Insights.tsx
└── src-tauri/
    ├── Cargo.toml
    ├── tauri.conf.json
    └── src/
        ├── main.rs         — window + lifecycle + commands
        └── sidecar.rs      — engine process manager
```
