# Say

Local-first voice dictation for macOS, with a desktop window.

Hold the global hotkey, speak, release. Audio is transcribed locally on Apple Silicon — the bundled **Moonshine** model by default (small, fast, English, ONNX) with multilingual **Whisper** models a click away in the Models tab — the text is injected into the focused app, and the transcript is logged to a SQLite database in your home directory. No cloud, no API keys, no telemetry; audio stays in memory unless you opt into Recordings (still local).

## What's in this repo

```
├── say/         — Python dictation engine (menu-bar agent + HTTP API)
├── desktop/     — Tauri 2 + React desktop window (Wispr Flow-style UI)
└── pyproject.toml
```

The engine is the source of truth for hotkeys, recording, transcription, and text injection. The desktop window is a viewer that talks to it over a loopback HTTP socket.

| Surface | Tech | Path |
|---|---|---|
| Menu-bar agent | Python (rumps) | `say/app.py` |
| Dictation pipeline | Python (mlx-whisper) | `say/audio.py`, `say/transcriber.py`, `say/controller.py`, `say/injector.py` |
| HTTP API + SQLite store | FastAPI + sqlite3 | `say/server/` |
| Desktop window | Tauri 2 + React + Tailwind | `desktop/` |

## Quick start (development)

Engine (menu-bar + HTTP API):

```bash
brew install portaudio ffmpeg
python3 -m venv ~/.venvs/say
source ~/.venvs/say/bin/activate
pip install -r say/requirements.txt
python say/app.py --server          # menu bar + loopback API
```

Desktop window in another terminal:

```bash
cd desktop
npm install
npm run tauri:dev
```

The window opens at 1100×760, reads the engine's `runtime.json` handshake file, and streams state from `/events`. Press Fn (or your fallback hotkey) anywhere — the recording dot in the window's title bar pulses, and a new transcript card appears in **Home** when you release.

## Production build (DMG)

```bash
cd desktop
make all      # PyInstaller engine (with model) + Tauri release build + embed
make dmg      # produces Say_0.1.0_aarch64.dmg
```

The DMG is **unsigned** unless `DEVELOPER_ID` is set. On a fresh Mac, right-click → Open the first time to bypass Gatekeeper. To sign + notarize see `desktop/README.md`.

The bundled `Say.app` contains both the desktop shell and a nested `SayEngine.app` (the PyInstaller-bundled Python engine + the bundled Moonshine model). The shell spawns the engine on launch with `--no-menubar` and tears it down on Cmd+Q.

## Two screens, by design

**Home** — Reverse-chronological transcript history grouped by day, with a copy affordance on hover. Right-rail tiles show lifetime word total, 30-day median WPM, and current streak.

**Insights → Your Usage** — Three KPI cards (WPM with half-donut + percentile pill, Whisper fixes, total words with monthly delta), plus per-app usage bars and a 6-month streak heatmap. **Your Voice** is a placeholder for v2.

The sidebar has Dictionary, Snippets, Style, Transforms, and Scratchpad rendered as disabled rows so the layout matches the target design — but those screens are intentionally out of scope for v1.

## Privacy

- Audio stays in memory by default — never written to disk.
- **Recordings (opt-in):** you can turn on *Save audio receipts* in the Recordings tab to keep a local WAV of each dictation for replay/verify/export. Recordings stay on this Mac and are never uploaded; you control retention (keep all / auto-delete after N days / cap total size) and can export everything to a local `.zip` or delete it at any time.
- Models run locally on Apple Silicon: Moonshine via OnnxRuntime (bundled default, no torch) or Whisper via mlx-whisper. Switching models never sends a byte off-device.
- Transcripts live in `~/Library/Application Support/Say/transcripts.db`.
- Logs (rotated 10 MB × 5) live in `~/Library/Logs/Say/`.
- The HTTP API binds to `127.0.0.1` on a random port, with a per-launch bearer token in `~/Library/Application Support/Say/runtime.json` (mode `0600`).

## Permissions

- **Microphone** — for audio capture (engine).
- **Accessibility** — for keystroke injection (engine).
- **Input Monitoring** — only if the Fn-key listener is enabled.

## Tests

```bash
~/.venvs/say/bin/python -m pytest say/tests/
```

63 tests, < 1 s.

## License

MIT — see `LICENSE`.
