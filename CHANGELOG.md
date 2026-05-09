# Changelog

## Unreleased

### Phase 1 — Correctness foundations
- Fix race conditions in `start_recording`; recorder.start() runs inside the controller lock.
- `FnHotkeyListener.start()` blocks until the run loop is live (no missed first events).
- `FnHotkeyListener.stop()` invalidates `CFMachPort` and removes the run-loop source.
- New public API: `force_idle()`, `current_state()` on both listeners.
- New audio→controller event channel for `max_duration` and `stream_error`.
- Auto-finish on `MAX_RECORDING_SECONDS` instead of silent sample truncation.
- Empty `INITIAL_PROMPT` default (no more shipped personal jargon).

### Phase 2 — Text injection
- Replace AppleScript-keystroke with `CGEvent` + Unicode payload (fast, lossless, supports emoji and >1500-char transcripts).
- Replace pyperclip with `NSPasteboard` + Cmd+V via CGEvent for the clipboard fallback.
- Add **Test Injection** menu item.

### Phase 3 — Audio robustness
- 500 ms pre-roll ring buffer (configurable via `PRE_ROLL_SECONDS`); first-syllable clipping eliminated.
- `DeviceWatcher` polls for default-input-device changes and resets the stream on disconnect.
- Stream stays open between recordings; `recorder.shutdown()` tears down on quit.

### Phase 4 — Transcription
- Pin `mlx-whisper==0.4.3` to keep the model-loading contract stable.
- Per-language hallucination filter expanded (English, Spanish, French, German, Portuguese, Japanese, Hindi).
- Runtime `Transcriber.swap_model(...)` switches models without restarting the app.
- New `Transcriber.predownload(...)` surfaces download status to the menu bar.

### Phase 5 — Observability
- Centralized `logging` module with rotating file handler at `~/Library/Logs/Say/say.log` (10 MB × 5).
- Local-only metrics ledger at `~/Library/Logs/Say/metrics.jsonl`.
- Top-level crash handler captures uncaught exceptions on main thread and worker threads.
- New menu items: **Run Diagnostics**, **Show Logs**.

### Phase 6 — Settings & first-run
- Persistent settings at `~/Library/Application Support/Say/settings.json`.
- `settings.apply_to_config()` mirrors persisted overrides onto the live `config` module at startup.
- First-run wizard walks Microphone → Accessibility → Input Monitoring with "Open Settings" buttons.

### Phase 7 — Packaging
- `Info.plist` with `LSUIElement=true`, `NSMicrophoneUsageDescription`, `NSAppleEventsUsageDescription`, `NSAccessibilityUsageDescription`.
- New `Say.spec` PyInstaller spec, new `Makefile` with `build`/`dmg`/`sign`/`notarize` targets.
- The default Whisper model is bundled into `Say.app/Contents/Resources/models/<name>/` so first launch is offline.
- Sign + notarize targets are stubs that activate when `DEVELOPER_ID` and friends are set.

### Phase 8 — Hygiene
- New `pyproject.toml`, `LICENSE` (MIT), `CONTRIBUTING.md`, `CHANGELOG.md`.
- Ruff + mypy configuration; GitHub Actions workflow on macOS arm64 runners.
- Test coverage expanded.

### Phase 9 — Edge cases
- Pause/cancel on screen-lock and sleep notifications.
- Single-instance lock at `~/Library/Application Support/Say/say.lock`.
- Multi-display overlay (renders on the screen with the focused window).
