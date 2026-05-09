# Say

On-device voice dictation for macOS.

Hold the global hotkey, speak, release. The audio is transcribed locally with Whisper on Apple Silicon and inserted into the focused app via synthetic CGEvents. No cloud, no API keys, no telemetry, no audio files written to disk during normal use.

## Highlights

- **Push-to-talk** (hold Fn) and **hands-free toggle** (Fn+Space).
- **500 ms pre-roll buffer** — the first syllable is no longer clipped.
- **Native CGEvent injection** — fast, Unicode-correct, handles emoji and >1500-char transcripts. Falls back to NSPasteboard + Cmd+V when needed.
- **Auto-fallback hotkey** — if `CGEventTap` cannot be created (e.g. permissions not yet granted), the app drops to a pynput `ctrl+space` listener and notifies you.
- **Live status & overlay** — floating waveform bar follows the screen with the cursor on multi-display setups.
- **Persistent settings** at `~/Library/Application Support/Say/settings.json`.
- **Privacy-respecting**: audio never written to disk; the macOS mic-active LED is your honest indicator while the monitor stream is open.
- **Local logs and metrics** at `~/Library/Logs/Say/` (rotating 10 MB × 5).
- **Single-instance lock** — only one Say can grab the Fn key at a time.
- **Cancels recording on screen-lock and sleep** so audio never bleeds across sessions.

## Requirements

- macOS 13 Ventura or later (Apple Silicon strongly recommended).
- Python 3.11 or newer.
- Homebrew.
- About 2 GB free for the default `whisper-large-v3-turbo` model.

## Install

```bash
chmod +x setup.sh
./setup.sh
```

This installs Homebrew deps (portaudio, ffmpeg), creates `~/.venvs/say`, installs Python deps, and pre-warms the model. It can also build an unsigned `Say.app` via the Makefile.

Manual setup:

```bash
brew install portaudio ffmpeg
python3 -m venv ~/.venvs/say
source ~/.venvs/say/bin/activate
pip install -r requirements.txt
python -c "from transcriber import Transcriber; Transcriber().load()"
```

## Permissions

Grant the running process Microphone, Accessibility, and Input Monitoring in **System Settings → Privacy & Security**.

- If running from Terminal/iTerm/Warp, grant the permissions to that terminal.
- If running a packaged `Say.app`, grant them to `Say.app`.

The first run shows a permissions wizard that walks you through each pane.

## Run

```bash
source ~/.venvs/say/bin/activate
python app.py                 # menu-bar mode
python app.py --server        # menu bar + loopback HTTP API for the desktop UI
python app.py --no-menubar    # headless engine (used by the desktop sidecar)
python main.py                # CLI / debug mode
```

The `--server` flag boots a FastAPI app on `127.0.0.1` at a random port. The port and a per-launch bearer token are written to `~/Library/Application Support/Say/runtime.json` (mode `0600`). The `--no-menubar` mode runs the engine without rumps — used by the Tauri desktop shell as a sidecar.

## Usage

### Push-to-talk

1. Hold `Fn`.
2. Speak.
3. Release `Fn`. Text appears in the focused app.

### Hands-free

1. Press `Fn + Space` to start.
2. Speak — no need to hold anything.
3. Press `Fn` to stop and transcribe.

`Esc` cancels at any point. The overlay's ✕ and ✓ buttons cancel and finish.

## Menu items

| Item | What it does |
| --- | --- |
| Test Injection | Types `Say is working ✓` into the focused app — verifies the CGEvent path. |
| Run Diagnostics | Env checks + 1 s mic capture + model warmup, summarized in an alert. |
| Show Logs | Reveals `~/Library/Logs/Say/` in Finder. |
| Open Microphone / Accessibility / Input Monitoring Settings | Jumps straight to the relevant pane. |

## Configuration

Defaults live in `config.py`. User overrides are persisted to `~/Library/Application Support/Say/settings.json` and applied at startup.

| Setting | Purpose |
| --- | --- |
| `MODEL` | Whisper model id used by mlx-whisper (or a path to a bundled copy). |
| `LANGUAGE` | Language code, or `None` for auto-detect. |
| `INJECT_METHOD` | `auto`, `accessibility` (CGEvent), or `clipboard`. |
| `INITIAL_PROMPT` | Optional vocabulary bias for Whisper. Empty by default. |
| `MIN_AUDIO_SECONDS` / `MAX_RECORDING_SECONDS` | Audio length bounds. |
| `PRE_ROLL_SECONDS` | Pre-roll ring buffer size. `0` disables. |
| `DEVICE_WATCH_INTERVAL_S` | Polling interval for the input-device-change watcher. |
| `SHOW_NOTIFICATIONS` / `SHOW_OVERLAY` / `OVERLAY_POSITION` | UX toggles. |

Recommended models:

- Speed: `mlx-community/whisper-large-v3-turbo` (default)
- Accuracy: `mlx-community/whisper-large-v3-mlx`
- Low storage: `mlx-community/whisper-small-mlx`

## Architecture

```text
make_listener() ──► FnHotkeyListener  (or HotkeyListener fallback)
                          │
                          ▼
                   DictationController ── audio events ──┐
                          │                              │
                  ┌───────┼─────────┐                    │
                  ▼       ▼         ▼                    │
              recorder transcriber injector              │
              (audio.py) (mlx-whisper)  (CGEvent / NSPasteboard)
                                                         │
                  ▲                                      │
                  │       DeviceWatcher ─────────────────┤
                  │       SystemEventWatcher ────────────┘
              DictationOverlay (waveform bar, follows cursor screen)
```

`DictationController` states: `loading → ready → recording → processing → ready` (or `error`). The audio recorder's `_capturing` flag separates the always-on monitor stream from the active recording capture, so pre-roll fills even between recordings.

## Persistence

| File | Purpose |
| --- | --- |
| `~/Library/Application Support/Say/transcripts.db` | SQLite store of every successful dictation: text, word count, timing, model, injection method, frontmost-app bundle id. |
| `~/Library/Application Support/Say/runtime.json` | Loopback HTTP port + bearer token (only present when `--server` is running, mode `0600`). |
| `~/Library/Application Support/Say/settings.json` | User overrides for `config.py` defaults. |
| `~/Library/Logs/Say/say.log` | Rotating logs (10 MB × 5). |
| `~/Library/Logs/Say/metrics.jsonl` | One-line-per-event metrics ledger (audio seconds, inference seconds, success). |

## HTTP API (when `--server` is on)

The desktop window in `desktop/` consumes this. All endpoints (except `/health`) require `Authorization: Bearer <token>` or `?token=<token>`.

| Endpoint | Returns |
| --- | --- |
| `GET /health` | `{ok, state}` |
| `GET /user/profile` | `{first_name}` |
| `GET /transcripts?limit&before` | reverse-chronological list |
| `GET /stats/summary` | totals, WPM, streak, monthly delta |
| `GET /stats/desktop_usage?days` | per-app rollup |
| `GET /stats/heatmap?days` | per-day count grid |
| `POST /transcripts/{id}/copy` | telemetry stub (204) |
| `GET /events` | SSE stream of `DictationEvent` state changes |

## Privacy

- No cloud calls after the initial model download.
- Audio buffer is in-memory only — never written to disk.
- The clipboard fallback method does momentarily put your transcript into the system pasteboard. Clipboard managers (Raycast, Maccy, Paste, Alfred, etc.) **will** capture it. Prefer `accessibility` (CGEvent) when Accessibility permission is granted.
- Logs at `~/Library/Logs/Say/say.log` and metrics at `~/Library/Logs/Say/metrics.jsonl` are local only and never uploaded.
- The HTTP API is bound to `127.0.0.1` and gated by a per-launch bearer token; it is not reachable from the network.

## Packaging

```bash
make all     # install deps, predownload model, build .app, create unsigned DMG
```

The default model is bundled inside `Say.app/Contents/Resources/models/<name>/`, so the app works offline from the first launch. The build is **unsigned** — users must right-click → Open the first time. To codesign and notarize, set `DEVELOPER_ID`, `APPLE_ID`, `APPLE_TEAM_ID`, `APPLE_APP_PASSWORD` in your environment and run `make sign notarize`.

## Tests

```bash
cd say
python -m unittest discover -s tests -t .
```

63 unit tests covering controller lifecycle, audio event channel, hotkey state machine, listener auto-fallback, injector dispatch, settings persistence, single-instance lock, transcriber filter, validation, the SQLite transcript store (insert / list / summary / heatmap / desktop usage / streak / word counting), and the FastAPI surface (auth, all endpoints).

## Troubleshooting

Hotkey doesn't respond:

- Check **Run Diagnostics** in the menu — it tells you what is missing.
- Grant Input Monitoring + Accessibility to the running app and restart it.
- If `CGEventTap` cannot be created, Say automatically falls back to `ctrl+space`. The menu-bar status reflects whichever hotkey is active.

Mic doesn't capture anything:

- Run **Run Diagnostics** — the 1 s capture RMS reveals dead channels.
- Use **List Microphone Devices** to confirm the default input.
- Unplug/replug your mic; the device watcher resets the stream automatically.

Text doesn't appear:

- Use **Test Injection** to isolate the issue.
- If `accessibility` (CGEvent) fails, set `INJECT_METHOD=clipboard` (or leave `auto` to fall back automatically).
- Some sandboxed apps refuse synthetic events; clipboard usually works there.

Slow transcription:

- Use `whisper-large-v3-turbo` (the default).
- Keep Say running so the model stays loaded; the menu-bar app does this for you.

Offline verification:

1. Run once online to cache the model (or build the bundled `.app`).
2. Disconnect from the internet.
3. Start Say and dictate into TextEdit.
