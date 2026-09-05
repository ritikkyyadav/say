# Say

On-device macOS voice dictation. Hold Fn, speak, release — text appears in the focused app. No cloud, no API keys. See `README.md` for user-facing docs, screenshots, and privacy details.

> **Ships as "Say"** — repo, code and product name all agree. Bundle IDs `com.ritikyadav.say.dictation` (engine) / `com.ritikyadav.say.desktop` (shell); executables/bundles `Say` / `Say.app` / `SayEngine.app`; UI and notification branding "Say".
>
> **The bundle IDs are deliberately in a `com.ritikyadav.*` namespace, not `com.say.*`.** macOS TCC keys permissions on bundle ID + code signature and never garbage-collects old records. The project previously shipped as `com.say.dictation.desktop`, accumulated broken grants ("perms given but Fn off"), and was rebranded to `com.oceanus.*` purely to escape them. Renaming back to `com.say.*` would re-inherit those dead records, so the rename to Say (2026-08-22) moved to a namespace that has never been used. **Do not "tidy" these identifiers back to `com.say.*`.**
>
> **Internal data dirs stay `~/Library/Application Support/Say` and `~/Library/Logs/Say`** (the Python↔Rust `runtime.json` handshake depends on this path agreeing on both sides — do not rename it). They were already `Say`, which is why the rename needed no data migration.

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
~/.venvs/say/bin/python -m pytest say/tests/     # 36 modules, 600 tests, <12s
SAY_CI=1 pytest say/tests/                       # CI mode: skips pyobjc-tap tests needing Accessibility
# Always pytest, never `unittest discover`: tests/conftest.py redirects every user-data
# path (ledger, settings, store, model caches) into a temp dir, and only pytest runs it.

# ── Measurement (needs SAVE_RECORDINGS history) ──
python say/tools/replay_bench.py --list                  # describe the local corpus
python say/tools/replay_bench.py --stored-only --postprocess   # score history, no inference
python say/tools/replay_bench.py --sample 24             # sweep decoder configs

# ── Corrections (is the pipeline actually getting better?) ──
python say/tools/corrections_report.py           # measured correction rate + the ledger
python say/tools/corrections_report.py --terms   # learnable term pairs from real fixes
python say/tools/corrections_report.py --why     # explain an empty ledger

# ── Overlay (the dictation glyph) ──
python say/tools/waveform_bench.py --stats               # per-band levels over the corpus
python say/tools/waveform_bench.py --strip out.png       # filmstrip of real frames
python say/tools/overlay_preview.py                      # put the real overlay on screen
python say/tools/overlay_preview.py --mic --hold         # live mic, stays armed
python say/tools/overlay_preview.py --report             # frame-interval statistics

# ── Brand assets (needs Pillow — NOT in the say venv) ──
/opt/homebrew/bin/python3 gen_logo.py            # rebuild every icon from the vector mark
/opt/homebrew/bin/python3 gen_logo.py --preview  # logo-preview.png only, writes nothing else

# ── Linting ──
ruff check say/                                  # line-length 100, rules: E/F/W/I/UP/B/C4/SIM
mypy say/                                        # py311, ignore_missing_imports=true, check_untyped_defs=false

# ── Production build ──
cd say && make all                               # PyInstaller + bundled model → Say.app
cd desktop && make all                           # engine + UI + Tauri → embedded desktop app
cd desktop && make dmg                           # → Say_0.1.0_aarch64.dmg (unsigned)
```

---

## Architecture

```
┌─ Tauri Shell (Rust, macOS window) ─────────────────────────┐
│  React UI — Vite + React Query + SSE                       │
│  Cmd: get_engine_endpoint() → reads runtime.json           │
│  HTTP GET /health /transcripts /stats/* /listener /models   │
│       /recordings (+config, /{id}/audio, export) /overlay   │
│  Two surfaces only: Home (insights → transcripts, one       │
│  scroll) and Models, plus the overlay colour customiser     │
│  EventSource GET /events (real-time state stream)           │
└────────────────────────────────────────────────────────────┘
        │  HTTP on 127.0.0.1:<random_port>
        │  Bearer token (per-launch, from runtime.json)
        ▼
┌─ Python Engine ────────────────────────────────────────────┐
│  Fn key (CGEventTap) → AudioRecorder ──┬─► segment @ pause │
│         │                              │   (background     │
│         │                              │    decode)        │
│         │                              └─► tail @ release  │
│         └─► overlay glyph — live spectrum, 60 fps          │
│  → join → postprocess (vocab + format) → Injector          │
│  → focused app → read-back diff → corrections (the metric)  │
│  → focused app                                             │
│  State: IDLE → LOADING → READY → RECORDING → PROCESSING   │
│         ↘ ERROR (any stage) ↗                              │
│  FastAPI server → SQLite store → SSE event bus              │
└────────────────────────────────────────────────────────────┘
```

**Two modes:** Dev: `python say/app.py --server` (menu bar + API). Production: Tauri spawns `SayEngine.app --no-menubar --owner-pid <shell pid>` as sidecar. Quitting kills it from the shell (`shutdown_engine()`), **and** the engine exits on its own when the owner pid disappears — which is the only thing that covers Force Quit. See `owner_watch.py`.

**State machine** (`controller.py` `DictationState` enum): `IDLE` → `LOADING` (model load) → `READY` (waiting for hotkey) → `RECORDING` (mic active) → `PROCESSING` (transcribing) → back to `READY`. Any stage can → `ERROR`.

**Hotkey priority**: Fn key via CGEventTap (preferred) → ctrl+space via pynput (fallback). `listener_factory.py` handles auto-fallback, and `ListenerUpgrader` swaps back up to Fn the moment Accessibility is granted — no restart. Fn override neutralizes macOS globe-key action via `AppleFnUsageType=0` in NSGlobalDomain.

---

## File Map — Python Engine (`say/`)

| File | Purpose | Key Exports |
|------|---------|-------------|
| `app.py` | Both entry points. `SayApp` = menu-bar (rumps). `_run_server_only()` = the **shipped sidecar** mode: also builds the sleep/lock + device watchers **and the overlay** via `_setup_sidecar_ui()` and runs a Cocoa event loop (accessory policy, no Dock icon). The overlay is late-bound through `overlay_slot` because the hotkey is wired long before a run loop exists to hang a window off. | `SayApp(rumps.App)`, `main()`, `_run_server_only()`, `_setup_sidecar_ui()` |
| `main.py` | Terminal/debug entry point (no menu bar). | `main()` |
| `controller.py` | State machine coordinating record → **postprocess** → inject. Thread-safe via RLock. **Persists the transcript BEFORE injecting** and updates the row via `store.set_outcome()`, so a failed insert can never lose the user's words; failed text also goes to the clipboard. Best-effort `_save_recording()` runs either way. | `DictationController` — `.can_start_recording()` (the acceptance rule, shared with `start_recording()`), `DictationState(Enum)`, `DictationEvent(dataclass)` (+ `.progress`), `focus_policy(started_in, now_in) → 'inject' \| 'hold'` |
| `gestures.py` | **The seam between the hotkey listener and everything it drives** — one `GestureBridge` used by both entry points instead of five ad-hoc closures apiece. Key down asks `controller.can_start_recording()` *before* it shows the glyph or opens the mic; a refused `start_recording()` retracts the glyph, closes the mic and `force_idle()`s the listener; state events route to the overlay through the same object. `overlay_do()` swallows every overlay failure and warns once per action. Pure Python, no threads of its own, never raises into a hotkey thread. | `GestureBridge(controller, recorder, overlay=getter, listener=getter)` — `.on_arm()`, `.on_disarm()`, `.on_start()`, `.on_stop()`, `.on_cancel()`, `.on_status(event)`, `.overlay_do(action, *args)` |
| `callback_pump.py` | **One ordered worker per hotkey listener.** A thread per callback let a quick release's `on_stop` overtake its own `on_start` and leave a recording nothing stopped; the pump runs callbacks in the order a human produced them, starts lazily, logs a raising callback and keeps going. | `CallbackPump(name)` — `.fire(cb)`, `.stop(timeout)`, `.running` |
| `audio.py` | 16kHz mono mic stream. `arm()`/`disarm()` open the mic on hotkey-*down* so the pre-roll ring buffer is already filling before the tap/hold window resolves (without them pre-roll contributed 0 samples and the first word was lost). Otherwise **live only during a recording** (opens on `start()`, closes ~0.6s after `stop()` via debounced `_schedule_close` so the macOS mic indicator goes dark when idle). Back-to-back dictations reuse the open stream; reopen retries w/ backoff + PortAudio reset to dodge CoreAudio's -9986 race. **`start()` opens the device BEFORE marking the recording live** — the reopen can block seconds and anything captured in that window is lost. `live_seconds` reports callback-delivered audio only (excludes the pre-roll seed) so the controller can reject a clip the mic never contributed to. `set_monitor_callback()` is a live tap that fires **before** the not-capturing early return, so the overlay is already responding during the armed window. | `AudioRecorder` — `.start()`, `.stop() → np.ndarray`, `.cancel()`, `.shutdown()`, `.live_seconds`, `.set_amplitude_callback()`, `.set_event_callback()`, `.set_segment_callback()`, `.set_monitor_callback()`, `.pop_tail()`, `.arm()`, `.disarm()` |
| `transcriber.py` | **Backend-dispatching** STT engine. Picks runtime by the active model's `backend`: `whisper-mlx` (mlx-whisper, multilingual, HF cache) or `moonshine` (Moonshine ONNX via `moonshine_voice`, English, no torch — the bundled default). Heavy runtimes lazy-imported in the load path. Lazy load, warmup, hallucination filter (applied to both), model swap (closes the moonshine engine on switch-away). | `Transcriber` — `.load()`, `.transcribe(audio) → str`, `.swap_model(id)`, `.predownload(cb)`; `note_realtime(audio_s, elapsed, backend) → rtf` (warns above `SLOW_RTF`) |
| `languages.py` | **The decoder language as a setting, and the *focus* the Models page is built around.** `config.LANGUAGE` is a Whisper code or `None` for automatic detection; `config.LANGUAGE_FOCUS` is what the person dictates in (`english` pins `en` and points at the built-in model; `european`, `south_asian`, `any` detect automatically and point at Whisper turbo). Each focus fixes the picker to a short list and names the one model worth having. Validates codes and focuses; says which backends honour the language (Whisper does, Moonshine is English-only). Pure. | `normalise(value) → code \| None`, `normalise_focus(value)`, `current()`, `current_focus()`, `default_code_for(focus)`, `options_for(focus)`, `recommended_model(focus)`, `groups()`, `applies_to(model_id)`, `FOCUS_GROUPS`, `FOCUS_IDS`, `LANGUAGES`, `ENGLISH_MODEL`, `MULTILINGUAL_MODEL`, `BadLanguage` |
| `owner_watch.py` | **Makes the engine mortal.** The sidecar is launched via `open -n`, so its parent is launchd and nothing ties it to the app — quitting Say used to leave a `--no-menubar` engine running with a live Fn tap, invisible and unstoppable from the UI. The shell passes `--owner-pid`; this watches it via `kqueue`/`NOTE_EXIT` (exact, immune to pid reuse) with a `kill(pid,0)` poll as backstop, and shuts the engine down when the owner goes. **An unreadable process state counts as alive** — guessing "gone" would kill a live dictation. | `OwnerWatchdog(owner_pid, on_owner_exit, poll_interval=)` — `.start() → bool`, `.stop()`, `.owner_pid`, `.watching`; `process_alive(pid)`, `arm_exit_backstop(grace_s=, cleanup=, exit_fn=)`, `DEFAULT_POLL_S`, `EXIT_GRACE_S` |
| `vocabulary.py` | **What the transcripts say about the person who spoke them** — the analysis behind the Home screen. Pure: rows in, report out. Excludes decoder-loop transcripts from the counts (`is_degenerate`), extracts most-said words, 2-/3-word phrases (longest-wins, so a trigram absorbs its bigrams), filler rates, hour/weekday histograms, and composes the narrative `Insight`s in Python so the thresholds and the wording cannot drift apart. Returns `enough_data=False` rather than confident nonsense on a thin corpus. | `analyse(rows) → VocabularyReport`, `is_degenerate()`, `tokenize()`, `WordStat`, `Insight`, `STOPWORDS`, `FILLERS`, `MIN_WORDS`, `DEGENERATE_UNIQUE_RATIO`, `TYPING_WPM` |
| `overlay_theme.py` | **Colour themes and colour *patterns* for the glyph** + the live-overlay registry. A theme is a short colour list cycled across the twelve columns. Pure (no AppKit) so `server/api.py` can serialise it without importing AppKit. `ink` carries **no colours**, which resolves to `None` = "stay adaptive" — the pre-themes behaviour, and the default. The rim is *computed* from the mean luminance of the chosen colours, never picked by the user. | `OverlayTheme`, `PRESETS`, `catalog()`, `get(id)`, `resolve(id, custom) → (colors, rim) \| None`, `parse_hex()`, `normalise_hex()`, `clean_colors()`, `auto_rim()`, `BadColor`, `MAX_COLORS`, `VALID_THEME_IDS`, `column_colors(pattern, n, heights, t)`, `PATTERN_INFO`, `VALID_PATTERNS`, `DEFAULT_PATTERN`, `PULSE_SCATTER`, `SPARK_SHARE`, `FLOW_SPEED`, `register_overlay()`, `live_overlay()` |
| `overlay.py` | **The floating dictation glyph** — a 90×19 pt mark at the bottom of the screen: one continuous base with 12 frequency columns standing on it, redrawn every display frame from live mic audio. Non-activating borderless `NSPanel` that ignores mouse events (it can never take the focus the transcript is about to land in). Built once at launch (`prewarm()`) so showing it is one `orderFrontRegardless()`. Clocked by `CADisplayLink` off the panel's own screen, falling back to a 60 Hz `NSTimer`. Every public method hops to the main thread itself. **Self-healing:** a glyph flagged visible whose display link has not ticked for `CLOCK_STALE_S` is treated as gone (AppKit suspends the link the moment the window leaves every display — measured) and rebuilt on the active screen; a failing frame retracts the mark instead of freezing it. | `DictationOverlay` — `.prewarm()`, `.arm()`, `.listen(mode)`, `.process()`, `.dismiss()`, `.release_arm()`, `.feed_audio(block)`, `.set_theme()`, `.preview(seconds)`, `.close()`; `make_overlay()`, `contrast_scheme()` |
| `waveform.py` | **Pure audio→motion engine behind the overlay** (numpy + math only, no AppKit). `BandAnalyzer` turns mic blocks into 12 log-spaced band levels + a voice gate, on the audio thread, handed over by one atomic assignment. `BarMotion` turns those into bar heights: per-bar asymmetric spring **plus neighbour diffusion**, which is what makes the row read as one membrane instead of twelve meters. `silhouette()` returns the whole mark as one closed polygon. | `BandAnalyzer` — `.feed()`, `.snapshot()`, `.rearm()`, `.reset()`; `BarMotion` — `.tick(dt, levels, gain, state)`, `.reset()`, `.heights`; `Spring`, `GlyphGeometry`, `silhouette()`, `band_edges()`, `clamp01()` |
| `streaming.py` | **Segment audio at silence so it decodes while you speak.** `split_on_silence()` (pure, used by the bench), `SilenceSegmenter` (incremental, for the audio callback), `SegmentedSession` (one ordered worker thread; `push()` mid-recording, `finish(tail)` at release). `carry_over_prompt()` feeds each segment the tail of the previous one's text — without it, splitting the clip loses the cross-window context and sentence density drops. | `split_on_silence()`, `SilenceSegmenter`, `SegmentedSession(transcriber, base_prompt=, short_prompt=, on_progress=)` — `.push()`, `.finish(tail)`, `.complete`, `.attempted`, `.progress`; `carry_over_prompt()`, `SHORT_SEGMENT_SECONDS` |
| `postprocess.py` | **Deterministic clean-up between transcription and injection.** Stage 0 **repetition-loop collapse** (see gotcha below), then vocabulary repair (literal replacements, then guarded fuzzy/phonetic matching via Soundex), then formatting (capitalisation, terminal punctuation, whitespace). Line-wise, so paragraph breaks survive. Pure, no model, never raises. Also builds the decoder bias string, which **scales with clip length** via `asr_prompt(include_style_seed=)`. | `apply(text, target_app=None)`, `asr_prompt(include_style_seed=True)`, `collapse_repetitions()`, `collapse_intra_token_loops()`, `looks_degenerate()`, `apply_replacements()`, `apply_fuzzy_terms()`, `fix_capitalisation()`, `ensure_terminal_punctuation()`, `STYLE_SEED` |
| `corrections.py` | **The scorecard for everything between the mic and the document.** Pure word-level diff (no AppKit, no I/O) that answers "did the user still have to fix it". Two guards carry the whole metric: trailing **continuation is not correction** (`_split_boundary_growth` peels the user's next keystrokes out of the boundary block they fuse into), and a change past `MAX_DRIFT` is a **rewrite, not a correction**. `term_pairs()` extracts misheard-term pairs on demand — deliberately not stored, so better extraction rules can be applied retroactively. | `diff() → Diff`, `looks_like_correction()`, `term_pairs()`, `summarise(rows, insertions=) → RateSummary`, `tokenize()`, `normalise()`, `Edit`, `MAX_DRIFT` |
| `readback.py` | **Observes the correction.** A few seconds after injection, re-reads the focused element via the Accessibility API and diffs it against Say's span. Rides the grant injection already needs — nothing extra is requested — and fails closed and silent without it. Privacy contract is structural: only the element Say just wrote into, only inside a window it opened, document context held **in memory only** to relocate the span and dropped on fire, and only Say's own span + what it became is ever persisted. `delay_s<=0` reads back inline (test determinism). | `CorrectionWatcher(store, delay_s=)` — `.observe()`, `.shutdown()`; `_relocate()`, `DEFAULT_DELAY_S` |
| `recordings.py` | **Audio receipts** (opt-in, `SAVE_RECORDINGS`). Writes per-dictation 16kHz mono WAV to `…/Say/recordings/<id>.wav`, links it via `store.set_audio_path`. Retention sweep (keep_all/days/size), local ZIP export (clips + CSV/JSON manifest) to `…/Say/exports/`. Store-parameterized (not singleton) so the API can inject a test store. Local only — never uploaded. | `save_for_transcript()`, `read_bytes()`, `delete_recording()`, `delete_all()`, `total_bytes()`, `enforce_retention()`, `recordings_config()`, `apply_recordings_config()`, `list_recordings()`, `export_zip()` |
| `model_catalog.py` | **Source of truth** for installable STT models + cache state. Each entry has a `backend` (`whisper-mlx`/`moonshine`); cache-state helpers branch on it (Whisper → HF cache honoring `HF_HUB_CACHE`; Moonshine → `~/.cache/moonshine` honoring `SAY_MOONSHINE_CACHE`). `config.VALID_MODELS` must equal `CATALOG_IDS` (test-guarded). Pure/testable. | `ModelInfo(.. backend)`, `CATALOG`, `CATALOG_IDS`, `catalog()`, `get(id)`, `is_downloaded(id)`, `disk_usage_bytes(id)`, `bundled_dir(id)`, `moonshine_cache_root()`, `moonshine_model_dir(id)`, `to_dict(info)` |
| `model_manager.py` | Runtime model ops, **backend-aware**: Whisper downloads via HF snapshot (real-byte progress + disk pre-check + tqdm-abort cancel); Moonshine via `moonshine_voice.get_model_for_language` (progress polled from on-disk size; cancel best-effort since the fetch is blocking). `_storage_for(id)` centralizes the per-backend dir + cache-root guard for delete/cleanup. Singleton holds the live controller for no-relaunch swaps. | `ModelManager` — `.list_models()`, `.set_active(id)`, `.start_download(id)`, `.cancel_download(id)`, `.delete(id)`, `.register_engine(ctrl)`, `.total_disk_bytes()`; `get_manager()`, `InsufficientStorage` |
| `injector.py` | Routes text to focused app. Checks `IsSecureEventInputEnabled()` first (password fields swallow both paths silently) then CGEvent → clipboard fallback. | `inject(text) → InjectionResult(success, method, error)`, `is_secure_input_enabled()` |
| `text_inject_cgevent.py` | CGEvent Unicode injection. Chunks text into ≤20 **UTF-16 units** per event (never splitting a surrogate pair — Python counts code points, Quartz counts units), 2ms inter-chunk delay. | `inject_text_cgevent(text) → InjectResult`, `chunks(text)`, `utf16_len(text)` |
| `text_inject_clipboard.py` | NSPasteboard + Cmd+V fallback. Snapshots **every item and type** on the pasteboard and restores them all (string-only fallback). 30ms pre-paste, 200ms post-paste settle. | `inject_text_clipboard(text) → InjectResult`, `snapshot_pasteboard(pb)`, `restore_pasteboard(pb, snapshot, AppKit)` |
| `hotkey.py` | pynput-based global hotkey listener. Tap-to-toggle: hold <0.30s → hands-free, hold ≥0.30s → PTT, Esc cancels. | `HotkeyListener` — `.start(timeout)`, `.stop()`, `.force_idle()`, `.mode`, `parse_hotkey(str)` |
| `hotkey_fn.py` | CGEventTap Fn-key listener. **Any key other than Space/Esc while Fn is held cancels the gesture** (Fn+arrows are Home/End/PageUp/Down — they used to start phantom recordings that could inject ambient speech). Fires `on_arm`/`on_disarm` so the recorder can pre-fill pre-roll. Fn hold → PTT; **tap Space anytime while Fn held** (PENDING *or* already in PTT) → latches hands-free (release Fn, talk, press Fn to stop) — not a sub-250ms chord; Esc cancels. Swallows Fn/Space/Esc events. | `FnHotkeyListener` — `.start(timeout)`, `.stop()`, `.force_idle()`, `.mode`; `apply_low_latency_switch_interval()` |
| `listener_factory.py` | Auto-selects best hotkey listener (Fn first, pynput fallback) and watches for a late Accessibility grant to upgrade in place. | `make_listener(...) → ListenerInfo(listener, kind, label, fell_back)`, `try_start_fn_listener(...) → (info, error)`, `ListenerUpgrader(start_fn, on_upgraded)` |
| `fn_override.py` | Temporarily sets AppleFnUsageType=0 to suppress macOS globe-key action. Crash-safe: persists backup to disk before mutation. | `acquire()`, `release()` |
| `config.py` | **Single source of truth** for shipped defaults. No hardcoded values elsewhere. | Constants: `MODEL`, `HOTKEY`, `SAMPLE_RATE`, `MAX_RECORDING_SECONDS`, `PRE_ROLL_SECONDS`, `INJECT_METHOD`, `LANGUAGE`, etc. |
| `settings.py` | User overrides persisted to `settings.json`. Mirrors changes to `config` module at runtime. | `get(key)`, `set_value(key, val)`, `apply_to_config()`, `is_first_run()`, `mark_first_run_complete()` |
| `settings_window.py` | Preferences NSWindow. Live binding — no Save button, each change persists immediately. 540×620pt. | `_SettingsController(NSView)` |
| `permissions.py` | Environment checks (OS, Python, CPU, modules) and deep-links to macOS Privacy Settings. | `check_environment() → [EnvironmentCheck]`, `open_privacy_settings(section)` |
| `validation.py` | Validates config at startup. Checks MODEL, HOTKEY, INJECT_METHOD, SAMPLE_RATE, etc. | `validate_config() → [str]`, `validate_or_raise()` |
| `brand.py` | Design tokens: color palettes (Sky, Brown, Pink, Status, Neutral, Overlay), typography helpers, asset loading. Resolves `ASSETS_DIR` across dev / `sys._MEIPASS` / `Contents/Resources`. `MARK_ASPECT` is the runtime's single mirror of the geometry `gen_logo.py` owns. | `font(size, weight)`, `mono(size)`, `logo_image()`, `menubar_template()`, `MARK_ASPECT`, `MENUBAR_HEIGHT_PT` |
| `wizard.py` | First-run onboarding NSWindow. 3 permission cards (Mic, Accessibility, Input Monitoring) with Settings deep-links. | `run_wizard(on_finish)`, `_MarkPlaceholder` (drawn mark when the asset is missing) |
| `first_run.py` | Onboarding router. Tries branded wizard, falls back to alert chain. | `run_wizard(alert_fn, on_finish)` |
| `menubar_icon.py` | Renders and caches menu-bar icons. Non-idle states recolour the whole mark (a badge dot can't be a template image, so it would vanish in dark mode). Cache filenames carry an asset fingerprint. | `IconState(Enum)`, `icon_for(state) → NSImage`, `icon_path_for(state)`, `icon_point_size()`, `is_template(state)` |
| `device_watcher.py` | Polls default input device every 2s. Notifies on change (e.g., AirPods unplugged). | `DeviceWatcher(on_change)` — `.start()`, `.stop()` |
| `system_events.py` | Subscribes to screen-lock, sleep/wake NSNotifications. Cancels active recordings on lock/sleep. | `SystemEventWatcher(callback)` — `.start()`, `.stop()` |
| `notifications.py` | macOS native notifications via osascript. Reads `config.SHOW_NOTIFICATIONS` **at call time**, so the Preferences toggle works without a restart. | `notify(title, text)`, `notify_transcription_complete(text)`, `notify_error(msg)` |
| `logging_setup.py` | stderr + rotating file log (`~/Library/Logs/Say/say.log`, 10MB × 5). Crash handler for uncaught exceptions. | `configure_logging(level)`, `get_logger(name)`, `install_crash_handler()` |
| `metrics.py` | JSONL ledger at `~/Library/Logs/Say/metrics.jsonl`. Records transcription timing, errors and `held` transcripts. Path resolved at construction (not a default argument), so tests can redirect it. | `get_ledger() → MetricsLedger`, `.record(**fields)` |
| `single_instance.py` | fcntl-based lock at `~/Library/Application Support/Say/say.lock`. Fail-open: if lock can't be created, runs anyway. | `acquire() → bool`, `release()` |

## File Map — Server Layer (`say/server/`)

| File | Purpose | Key Exports |
|------|---------|-------------|
| `api.py` | FastAPI app. 21 endpoints (incl. model mgmt + recordings) + SSE stream. CORS methods GET/POST/DELETE; origins `tauri://localhost`, `http://localhost:1420`, `http://127.0.0.1:1420`. Auth: optional Bearer token. | `create_app(store, auth_token) → FastAPI`, `serve_blocking()`, `serve_in_thread()`, `ActiveModelRequest`, `RecordingsConfigRequest` |
| `store.py` | SQLite at `~/Library/Application Support/Say/transcripts.db`. Transcript CRUD + aggregated stats (WPM, streaks, per-app usage) + the `corrections` ledger. Additive `_migrate()` adds `audio_path` and `observed` to old DBs at startup; `corrections` is `CREATE TABLE IF NOT EXISTS` in `_SCHEMA`, so old DBs pick it up with no migration. | `TranscriptStore` — `.insert() → id`, `.list(limit, before)`, `.summary()`, `.desktop_usage(days)`, `.heatmap(days)`, `.set_audio_path()`, `.clear_audio_path()`, `.audio_path_for()`, `.recordings(limit)`, `.record_correction()`, `.mark_observed()`, `.corrections(limit, since)`, `.correction_summary(days)`, `get_frontmost_app()` |
| `events.py` | In-process pub/sub bus. Fans out DictationEvent to SSE clients. Queue size 64, slow consumers dropped. | `EventBus` — `.publish_threadsafe(payload)`, `.subscribe()`, `.snapshot()`, `get_bus()` |
| `runtime.py` | Writes `runtime.json` handshake (`host`, `port`, `token`, `pid`). File mode 0600. | `write_runtime(port, token, pid)`, `read_runtime()`, `clear_runtime()` |

## File Map — Desktop UI (`desktop/src/`)

| File | Purpose | Key Exports / Props |
|------|---------|---------------------|
| `main.tsx` | React entry. QueryClient with 30s staleTime, 4 retries (exponential backoff 1s→8s). BrowserRouter. | — |
| `App.tsx` | Root layout: floating `Rail` + a bare top bar + Routes (`/` → Home, `/overlay` → Overlay, `/models` → Models). Keyboard: Cmd+1/2/3. The header carries **no status pills and no privacy badge** — repeating the same reassurance on every screen is decoration, not information. | `App()` |
| `routes/Home.tsx` | **The whole insights surface and the transcript feed, on one scroll.** Queries summary + `api.vocabulary()` + transcripts + usage + heatmap + recordings-config. Hero → 4 KPIs → Discovery panel → words/phrases → speaking-vs-typing + heatmap → hours + apps → sticky Transcripts header (search + a single "Keep audio" switch) → day-grouped rows. Insights render only when `enough_data`. | `Home()` |
| `routes/Models.tsx` | **Language-first model picker.** First the focus: four chips (English only / European languages / Hindi, Urdu and South Asian / Anything) from `/language`'s groups; choosing one persists `LANGUAGE_FOCUS`, applies the focus's default language, and a one-line status says whether the model in use understands those languages or which one to download. A filtered Language select sits beneath it. Then **every** model, in one list, no disclosure: sorted in-use → installed → not installed, each with a plain status pill, one sentence on what it is for, languages / size / speed / accuracy in words, and a single action (Use / Download n GB / Cancel / Remove) with inline progress and errors. Polls every 800ms while a download runs. | `Models()` |
| `routes/Overlay.tsx` | **The glyph customiser** (the repurposed second nav slot). Live `GlyphPreview` over a switchable light/dark/busy backdrop, the preset catalogue from `/overlay`, a custom colour row (up to `max_colors`), "Show it on screen" (flashes the real glyph), and the show-the-glyph switch. | `Overlay()` |
| `components/Rail.tsx` | The entire navigation: the mark (→ Home, and it **animates while recording** — the app's only status signal) plus two icon destinations. No labels, no dot, no text. | `Rail()` |
| `components/Mark.tsx` | The Say mark as inline SVG on the 30×12 grid `gen_logo.py` owns. `live` drives a per-column animation. Keep it 5:2. | `Mark({ live? })` |
| `components/Discovery.tsx` | "What we noticed" — the rotating insight panel. Renders the engine's composed copy via `markup()`, plus two viz kinds: a dot grid that makes "1 in every N words" literal (hits shifted per row so they scatter instead of stacking into a stripe) and a small ranked bar row. Auto-advances every 7.4s, pauses on hover. | `Discovery({ insights })` |
| `components/Charts.tsx` | Every small chart: `Spark` (sparkline), `WordList` (ranked words/phrases), `SavedVsTyping`, `Heatmap` (month rail + active-days/longest-run/best-day), `HourBars`, `AppShare`. All animate purely from the `.in` class their `Reveal` wrapper adds. | `Spark`, `WordList`, `SavedVsTyping`, `Heatmap`, `HourBars`, `AppShare` |
| `components/GlyphPreview.tsx` | A faithful browser-side glyph: **one closed polygon** with the colours painted through it as a `clipPath`, plus the contrast rim. Mirrors `overlay_theme.auto_rim`. Twelve separate rects would show gaps the real overlay does not have. | `GlyphPreview({ colors, adaptive, backdrop })`, `autoRim()` |
| `components/TranscriptRow.tsx` | One dictation: time · app · duration · words, the text (2 lines, click to expand), Copy, and playback. The clip is fetched lazily on first play (`blob:` URL, revoked on unmount); the play control is **disabled rather than hidden** when `has_audio` is false. | `TranscriptRow({ row })` |
| `components/PermissionsBanner.tsx` | Shown only while the Fn listener is not live. Three panes with deep links. | `PermissionsBanner()` |
| `lib/motion.tsx` | Entrance motion. `useReveal`/`Reveal` add `.in` (**anything already on screen at mount is revealed outright** — see the gotcha below), `CountUp` animates a number once on mount and respects `prefers-reduced-motion`. | `useReveal()`, `Reveal`, `CountUp` |
| `lib/markup.tsx` | Renders the `*em*` / `**bold**` markup the engine's insight copy carries, as React elements — never `dangerouslySetInnerHTML`. | `markup(text) → ReactNode` |
| `lib/format.ts` | `fmt`, `hm`, `mmss`, `longDuration`, `bytes`, `downloadTime` (a size in minutes, for people who do not think in gigabytes), `greeting`. | — |
| `lib/api.ts` | Typed HTTP client + SSE. Adds `vocabulary()`, `overlay()`, `setOverlay()`, `previewOverlay()`; `Transcript` gains `has_audio`. Endpoint discovery via Tauri `get_engine_endpoint` or the `/__say/runtime` dev middleware. | `api`, `subscribeEvents()`, `openPrivacySettings()`, `revealPath()` |
| `lib/useEngineState.ts` | React hook over the SSE stream. Drives the mark's recording animation. | `useEngineState() → DictationStateName` |
| `lib/grouping.ts` | Groups rows by local-timezone day (TODAY / YESTERDAY / MONTH DAY, YEAR). | `groupByDay<T>(rows, now?)` |
| `lib/appNames.ts` | Bundle ID → display name map (30+ common macOS apps). Falls back to reverse-DNS suffix. | `appLabel(bundleOrName?) → string` |

## File Map — Tauri Shell (`desktop/src-tauri/src/`)

| File | Purpose | Key Exports |
|------|---------|-------------|
| `main.rs` | Window lifecycle. Spawns engine sidecar on setup (terminating any orphan first — **never adopting one**, which is what made the leak permanent), always tears the engine down on ExitRequested via `shutdown_engine()`. Cmd+W hides window (doesn't close). Commands: `get_engine_endpoint`, `open_privacy_settings`, `reveal_logs_dir`, `reveal_path` (reveal+select a file in Finder, used after a recordings ZIP export). | `EngineEndpoint { host, port, token }` |
| `sidecar.rs` | Engine process manager. Launches `SayEngine.app` via **LaunchServices** (`open -n … --args --no-menubar`), NOT by exec'ing the inner binary — a directly-exec'd child inherits the shell's TCC identity so the engine's own Accessibility/Input-Monitoring grants never apply (Fn stays disabled). `open` detaches, so `.kill()` SIGTERMs the engine pid from `runtime.json` (3s wait → SIGKILL). `SAY_ENGINE_BIN` dev override still direct-execs. Passes `--owner-pid` so the engine can outlive-check the shell (Force Quit runs no shell code at all). `engine_status()` classifies the engine in `runtime.json` as `Ours`/`Orphan`/`Foreign`, so a leftover is terminated at launch while a dev's hand-started engine is left alone; `shutdown_engine()` is keyed on the file, not on a handle we hold. | `Sidecar` — `::spawn()`, `.kill()`; `EngineStatus`, `engine_status()`, `terminate_engine(pid)`, `shutdown_engine()`, `read_runtime_info()` |

---

## API Contract

**Base**: `http://127.0.0.1:<port>` (port from `runtime.json`). **Auth**: `Authorization: Bearer <token>` header, or `?token=<token>` query param for SSE.

| Endpoint | Method | Params | Response |
|----------|--------|--------|----------|
| `/health` | GET | — | `{ ok: bool, state: str }` |
| `/listener` | GET | — | `ListenerState` |
| `/user/profile` | GET | — | `{ first_name: str, onboarded: bool }` — `first_name` is `USER_NAME` if set, else the OS account name |
| `/user/profile` | POST | body `{ first_name?, onboarded? }` | Same shape. Name is whitespace-collapsed and capped at 48 chars before it is written |
| `/language` | GET | — | `LanguageConfig` — the decoder language (`null` = automatic), the current **focus**, every focus group with its picker options and recommended model, and whether the active model honours the language |
| `/language` | POST | body `{ code?: string \| null, focus?: string }` | `LanguageConfig` — both validated before anything is written (400 on garbage); a `focus` without a `code` applies the focus's default code (`english` → `en`, the rest → automatic); `code` alone pins a language inside the current focus |
| `/transcripts` | GET | `limit=200`, `before?=<iso>` | `Transcript[]` |
| `/stats/summary` | GET | — | `StatsSummary` |
| `/stats/desktop_usage` | GET | `days=30` | `[{ target_app, count, share }]` |
| `/stats/heatmap` | GET | `days=180` | `[{ date, count }]` |
| `/stats/vocabulary` | GET | `limit=2000` | `VocabularyReport` — most-said words, phrases, fillers, hour/weekday histograms and the composed insights. Computed on demand, never cached (the extraction rules keep improving; a cached report would freeze today's rules over yesterday's transcripts) |
| `/overlay` | GET | — | `OverlayConfig` — current theme, custom colours, the preset catalogue |
| `/overlay` | POST | body `OverlayConfigPatch` | `OverlayConfig` — persists + repaints the running glyph; `preview:true` also flashes it on screen. 400 on an unknown theme, an unknown pattern, a non-hex colour, or `custom` with no colours |
| `/stats/vocabulary` (see above) | — | — | **Declared `def`, not `async def`** — the only CPU-bound route in the app, and on the event loop it would stall `/health` and the SSE stream for its whole duration. Memoised on `(limit, row count, newest id)` |
| `/overlay/preview` | POST | — | `{ shown: bool, reason?: str }` — flash the real glyph so a colour can be judged in place |
| `/transcripts/{id}/copy` | POST | — | `{}` (telemetry stub) |
| `/models` | GET | — | `{ active: str, cache_bytes: int, models: ModelInfo[] }` |
| `/models/active` | POST | body `{ id }` | `{ active: str }` — persists + live-swaps engine; 400 if unknown |
| `/models/{id}/download` | POST | `id` is `:path` (has `/`) | `ModelDownload` — starts bg download; 404 unknown, 507 if disk full |
| `/models/{id}/cancel` | POST | `id` is `:path` | `{ cancelled: bool }` — aborts an in-flight download, cleans partial |
| `/models/{id}` | DELETE | `id` is `:path` | `{}` — 409 if active/bundled/downloading |
| `/corrections` | GET | `limit=200` | `{ summary: CorrectionSummary, corrections: Correction[] }` — the ledger + measured rate |
| `/recordings` | GET | `limit=500` | `Recording[]` — clips with saved audio, newest first |
| `/recordings/config` | GET | — | `RecordingsConfig` |
| `/recordings/config` | POST | body `RecordingsConfigPatch` | `RecordingsConfig` — persists save toggle/retention, prunes; 400 bad mode |
| `/recordings/export` | POST | — | `{ path: str\|null, count: int, bytes: int }` — local ZIP (clips + manifest) |
| `/recordings/{id}/audio` | GET | `id:int` | `audio/wav` bytes; 404 if none |
| `/recordings/{id}` | DELETE | `id:int` | `{ deleted: bool }` |
| `/recordings` | DELETE | — | `{ deleted: int }` — clear all |
| `/events` | GET (SSE) | `token?` | Stream of `DictationStateEvent` |

**Key Types:**
```typescript
type DictationStateName = 'idle' | 'loading' | 'ready' | 'recording' | 'processing' | 'error';
type DictationStateEvent = { state: DictationStateName; message?: string; mode?: 'ptt' | 'hands_free'; error?: string; progress?: { done: number; total: number } | null };   // progress only while PROCESSING
type LanguageGroup = { id: 'english' | 'european' | 'south_asian' | 'any'; name: string; blurb: string; default_code: string | null; recommended_model: string; options: { code: string; name: string }[] };
type LanguageConfig = { code: string | null; auto: boolean; name: string; options: { code: string; name: string }[]; focus: string; groups: LanguageGroup[]; recommended_model: string; applies_to_active_model: boolean };
type Transcript = { id: number; ts_utc: string; text: string; word_count: number; audio_seconds: number; inference_seconds: number; model: string; inject_method: string; target_app: string | null; success: boolean; has_audio: boolean };
type WordStat = { word: string; count: number; per: number; spread: number };
type InsightViz = { kind: 'dots'; every: number; rows: number; caption: string; label: string } | { kind: 'bars'; rows: [string, number][]; unit?: string };
type Insight = { id: string; headline: string; body: string; viz: InsightViz };   // headline/body carry *em* and **bold** markup — render with lib/markup, never dangerouslySetInnerHTML
type VocabularyReport = { total_words: number; unique_words: number; dictations: number; excluded: number; enough_data: boolean; top_words: WordStat[]; phrases: WordStat[]; fillers: WordStat[]; hours: number[]; weekdays: number[]; sentence_avg: number; longest_words: number; longest_seconds: number; speaking_seconds: number; typing_seconds: number; insights: Insight[] };
type OverlayPreset = { id: string; name: string; colors: string[]; blurb: string; adaptive: boolean };
type OverlayPatternInfo = { id: string; name: string; blurb: string };
type OverlayConfig = { theme: string; colors: string[]; pattern: string; patterns: OverlayPatternInfo[]; show_overlay: boolean; contrast: string; max_colors: number; presets: OverlayPreset[] };
type OverlayConfigPatch = Partial<{ theme: string; colors: string[]; pattern: string; show_overlay: boolean; contrast: string; preview: boolean }>;
type ListenerState = { kind: 'fn' | 'legacy' | null; label: string | null; fell_back: boolean; fn_error: string | null; started: boolean; accessibility_trusted: boolean };
type StatsSummary = { total_words: number; wpm_30d: number; streak_days: number; fixes_count: number; insertions_30d: number; correction_rate: number; correction_word_rate: number; words_this_month: number; words_prev_month: number; transcript_count: number };
type ModelDownload = { state: 'idle' | 'downloading' | 'done' | 'error'; percent: number; error: string | null };
type ModelInfo = { id: string; name: string; family: string; params: string; download_bytes: number; speed: number; accuracy: number; languages: string; recommended: boolean; order: number; description: string; backend: 'whisper-mlx' | 'moonshine' | string; downloaded: boolean; bundled: boolean; size_bytes: number; active: boolean; download: ModelDownload };
type ModelsResponse = { active: string; cache_bytes: number; models: ModelInfo[] };
type Correction = { id: number; transcript_id: number; ts_utc: string; source: 'observed' | 'manual'; inserted: string; corrected: string; edit_distance: number; words: number; target_app: string | null };
type CorrectionSummary = { insertions: number; inserted_total: number; corrected: number; words: number; edited_words: number; touch_rate: number; word_rate: number };
type RetentionMode = 'keep_all' | 'days' | 'size';
type Recording = { id: number; ts_utc: string; text: string; word_count: number; audio_seconds: number; model: string; inject_method: string; target_app: string | null; success: boolean; size_bytes: number; has_audio: boolean };
type RecordingsConfig = { save_recordings: boolean; retention_mode: RetentionMode; retention_days: number; max_bytes: number; total_bytes: number; count: number };
type RecordingsConfigPatch = Partial<Pick<RecordingsConfig, 'save_recordings' | 'retention_mode' | 'retention_days' | 'max_bytes'>>;
```

---

## Code Conventions

**Python (`say/`):**
- Python ≥3.11. Ruff: line-length 100, rules E/F/W/I/UP/B/C4/SIM. Ignores: E501, B008, SIM105, SIM117, UP038, B904, C401, UP042. Tests ignore E501.
- mypy: py311, `ignore_missing_imports=true`, `check_untyped_defs=false`.
- `config.py` is the single source of truth — never hardcode values that belong there.
- `settings.py` handles user overrides — reads from `settings.json`, mirrors to `config` at runtime.
- `model_catalog.py` is the source of truth for installable models; `config.VALID_MODELS` must equal `model_catalog.CATALOG_IDS` (test-guarded). Route model changes through `model_manager.set_active()` so the running engine swaps live (no relaunch); the engine registers itself via `register_engine()` at startup.
- **Multiple STT backends** live behind a `backend` field on `ModelInfo` (`whisper-mlx`, `moonshine`). To add another: (1) catalog entry with its `backend`; (2) branch the cache-state helpers in `model_catalog` (downloaded/disk/dir) on it; (3) dispatch in `transcriber.load()/transcribe()`, **lazy-importing** the heavy runtime so it's never pulled in unless that backend is active; (4) handle download/delete in `model_manager` (extend `_storage_for` for its on-disk layout + cache-root guard). Moonshine is the bundled default (`moonshine_voice`, ONNX, English, no torch); Whisper models are optional downloads. Bundled models resolve via `bundled_dir()` (Resources/models/<leaf>/) so first launch is offline.
- Thread safety via `RLock` for state, `Lock` for file I/O. Audio callbacks run on sounddevice thread — never block them.
- Module-level singletons with lock: `get_bus()`, `get_ledger()`, `TranscriptStore()`.
- State changes always emit `DictationEvent` via both `status_callback` and `EventBus.publish_threadsafe()`.
- **Mic stream is recording-scoped**, not always-on. Never reopen the `sd.InputStream` immediately after closing — CoreAudio releases the device asynchronously and a fast reopen throws `PaErrorCode -9986`. `audio.py` handles this via debounced close (`_IDLE_CLOSE_DELAY`) + retry/PortAudio-reset in `_ensure_stream_open`; don't bypass them.
- **Only one thread may ever be inside CoreAudio — the -9986 recovery is what used to kill dictation.**
  Symptom: dictation silently stops transcribing and stays dead until relaunch, with nothing in the
  log but `start_recording called` and no matching `AudioRecorder.start() returned`. `_ensure_stream_open` guarded itself with a bare
  `if self._stream is not None: return` **outside any lock**, and its two callers arrive on
  *different* threads ~250 ms apart — `arm()` from `hotkey_fn._handle_fn_down` (which `_fire`s every
  callback on a fresh thread) and `start()` once the tap-vs-hold window resolves. While `arm()` was
  blocked inside `sd.InputStream(...)` the attribute was still `None`, so `start()` walked straight
  past the guard and opened a **second stream on the same device**. Both then hit -9986, both reached
  the recovery branch, and one called `sd._terminate()` while the other sat inside `Pa_OpenStream` —
  which tears the host API down under a live HAL call and leaves CoreAudio's `HALB_Mutex` held
  forever. After that *every* open blocks: a `sample(1)` of the wedged engine showed **35 threads
  parked in `Pa_OpenStream`**, one per Fn press since the collision, plus one in
  `AudioOutputUnitStop`. Because `controller.start_recording()` calls `recorder.start()` **while
  holding its own RLock**, the first hang stranded the state machine too. **The tell is in the log
  and is unambiguous: a benign single-thread retry prints 3 `attempt N/6` warnings, a collision
  prints 6 with the counter restarting mid-sequence.** By that signature it fired **seven times in the
  two days** before it was found, against five benign single-thread retries in the two months before
  that — the collision window only opens when an open is slow enough to overlap the next caller,
  which is why it clustered once a Bluetooth mic became the default input.
  Fixed with `_device_lock` — a plain `Lock`, deliberately **not** `self._lock`, which the audio
  callback takes and which would deadlock if held across a device call — serialising open, start,
  stop, close *and* the PortAudio reset, plus a bounded wait (`_OPEN_TIMEOUT` / `_DEVICE_BUSY_TIMEOUT`)
  so a wedged driver costs one dictation instead of the session. The open runs on a worker that owns
  the lock for its whole life, so a wedge parks exactly **one** thread in CoreAudio rather than one
  per keypress, and the app heals itself when the driver recovers. `arm()` swallows-and-logs because
  it is only pre-roll — `start()` is the call worth an error. **Diagnose this class of bug with
  `sample <pid>`, not by reading the code**: the Python-level symptom (silence) says nothing, while
  the native stack names the mutex. `DeviceSerialisationTests` in `test_audio.py` pins all of it,
  verified to fail against the pre-fix module.
- **Hallucination filter is punctuation-insensitive.** Whisper emits silence-hallucinations with trailing punctuation (`"Thank you."`). Compare via `transcriber._normalize_for_filter()` (strips surrounding `.!?,…"'\`-`), and keep `_HALLUCINATION_FILTER` phrases punctuation-free.
- **Whisper style-drift is why punctuation vanished — and the fix is a seed, not a switch.**
  Whisper decodes in 30s windows; with `condition_on_previous_text=True` (the library default)
  each window is prompted with the previous window's output, inheriting *style* as well as
  terminology. With no `initial_prompt`, window 1 picks an arbitrary register and conditioning
  propagates it — measured baseline: 58% of transcripts had no terminal punctuation, and the
  unpunctuated ones were 2x longer (66s vs 34s median). Measured on 24 real clips via
  `say/tools/replay_bench.py`:

  Full corpus, 74 clips / 90.5 min of real dictation:

  | config | unpunctuated | capital | sentences/100w (prose = 5-7) | dict errors | RTF |
  |---|---|---|---|---|---|
  | baseline (no seed) | 54.1% | 52.7% | 1.48 | 28 | 0.117 |
  | baseline + postprocess | 0.0% | 100% | 1.83 | 0 | 0.117 |
  | seed only | 0.0% | 100% | 6.27 | 27 | 0.163 |
  | **seed + postprocess — SHIPPED** | **0.0%** | **100%** | **6.32** | **0** | **0.163** |

  On a 24-clip sample, `condition_on_previous_text=False` scored 45.8% / 1.23 alone and
  0.0% / 2.44 with the seed — i.e. worse than the seed alone on sentence density.

  **Do not "fix" this by setting `CONDITION_ON_PREVIOUS_TEXT = False`.** It is the intuitive
  move and it is worse: it halves sentence density by discarding cross-window context.
  Conditioning was never the problem; the missing seed was. `postprocess.asr_prompt()` supplies
  `initial_prompt` = style seed + `DICTIONARY` terms + the user's `INITIAL_PROMPT`.
  **Cost is real: RTF 0.117 -> 0.163, ~39% slower inference**, because correct output has more
  tokens to decode. `STYLE_SEED_ENABLED = False` keeps postprocess-only behaviour (0% unpunctuated,
  0 dictionary errors, no latency cost) at the price of run-on sentences (1.83 vs 6.32).
- **The punctuation fix does NOT apply to the bundled default model.** `_transcribe_moonshine()`
  takes no prompt at all, so `STYLE_SEED`, `DICTIONARY` biasing and
  `streaming.carry_over_prompt()` are all inert whenever the Moonshine backend is active — which is
  the shipped default (`config.MODEL = 'moonshine/medium-streaming-en'`). A fresh install therefore
  gets only the deterministic `postprocess` layer: 0% unpunctuated and 0 dictionary errors, but
  sentence density ~1.8 rather than ~6.3. Full quality currently requires switching to a Whisper
  model in the Models tab. Fixing this properly means either upgrading `moonshine-voice` to a
  release exposing `set_keyterms()`/`set_context()` (0.1.x; the pin is version-coupled to the
  on-disk cache layout) or making a Whisper model the bundled default and paying the download on
  first launch. Do not "fix" it by silently changing the default — offline-capable first launch is
  the reason Moonshine is bundled.
- **Streaming transcription is what makes the wait constant.** The controller opens a
  `streaming.SegmentedSession` on `start_recording()`; `audio.py` closes a segment at each natural
  pause (>=8 s of audio then 0.45 s of quiet, or 30 s hard cap) and dispatches it off the audio
  thread. At release only the tail is left, so the wait stops scaling with how long you talked.
  Measured two ways. Bench, 16 real clips: perceived RTF **0.147 -> 0.024 (6.1x)** with sentence
  density *rising* 5.88 -> 7.67. End-to-end through the real `SegmentedSession`, segments fed at
  true speaking speed on a 165 s / 12-segment clip: **29.2 s -> 2.5 s (11.7x)**, density 4.9 -> 8.56,
  `complete=True`. **Segments must carry the previous segment's text into their prompt** — without it
  density falls to 6.59. Note the worker always keeps up in practice because decoding runs ~6x
  faster than real time, so the queue cannot build while the user is speaking. Every failure path (no `set_segment_callback`, session error, empty result)
  degrades to whole-clip transcription; streaming must never be able to lose a dictation.
- **The engine must not be able to outlive the app — and only the engine can enforce that.**
  Quitting Say left the dictation engine running: `/health` still `ready`, the Fn CGEventTap still
  `started`, and it went on transcribing into whatever was focused. With no window, no Dock icon and
  `LSUIElement=true`, the user had nothing to look at and nothing to quit. Nothing recorded without a
  keypress, but "you can't see it and can't stop it" is a trust problem regardless, and it was
  **self-perpetuating**: `ensure_sidecar()` returned early when an engine was already alive, leaving
  `state.sidecar` at `None`, so `ExitRequested`'s `g.take()` returned `None` and killed *nothing* —
  and the engine that survived the quit was the one adopted at the next launch, which then survived
  that quit too. One orphan made every subsequent quit leave another. Two more holes sat behind it:
  Force Quit is **SIGKILL**, so no shell-side handler can ever run, and `open -n` reparents the engine
  to launchd, so PPID tells it nothing about who it serves. **The shell's teardown is therefore only
  the fast path; the guarantee has to be on the engine side.** The shell passes `--owner-pid <its own
  pid>`, `owner_watch.OwnerWatchdog` watches it, and the engine runs its normal `_shutdown()` when the
  owner disappears — measured **0.36 s** from SIGKILL to a fully released engine (Fn override
  restored, listener stopped, recorder closed, `runtime.json` cleared). The watchdog is armed **before
  the model load**, and `_owner_left_during_boot()` aborts the boot *before the hotkey tap is ever
  registered* — otherwise a quit during the multi-second load would finish arming an engine for an app
  that no longer exists. **`stop` only asks** — `arm_exit_backstop()` makes sure: the graceful path runs
  through `recorder.shutdown()`, which is exactly what a wedged CoreAudio device blocks, and one hung
  device call would reproduce the very symptom this prevents, so after `EXIT_GRACE_S` (5 s) the process
  leaves regardless. Its cleanup is deliberately just the two things that outlive the process if
  skipped — `fn_override.release()` (else the user's globe key stays stranded on "Do Nothing") and
  `clear_runtime()` — and a failure in either cannot block the exit. On the shell side
  `shutdown_engine()` is keyed on `runtime.json`, never on a handle we happen to hold, and `engine_status()` sorts engines into `Ours`/`Orphan`/`Foreign` so a
  developer's hand-started engine is never killed while a leftover one always is. **Don't "fix" a
  lingering engine by adopting it** — adoption is what made this permanent; terminate it and spawn a
  fresh one, which is also what the user expects ("closing the app turns dictation off").
- **A pid from a file is not proof of identity.** `runtime.json` can name a process that exited long
  ago and had its number recycled, so `terminate_engine()` signalling it blind would be someone else's
  crash. `is_say_engine()` confirms via `ps -o command=` that the pid is a `--no-menubar` Say process
  before any signal is sent. That check does double duty: `--no-menubar` exists *only* for the sidecar
  path (nobody launches it by hand), so a sidecar with no `owner_pid` recorded is provably an orphan
  from a pre-fix build rather than a dev engine — which is what lets a first launch of the new build
  clean up the one the old build left behind.
- **Post-processing is a seam, not a step.** `controller._postprocess()` sits between `transcribe()` and
  `injector()`. Anything that edits the transcript belongs there, behind `config.AUTO_CORRECT`, and must be
  best-effort (a failure returns the raw transcript — never nothing). Deterministic rules run before any
  future model tier so numbers and proper nouns are never at the mercy of a generative pass.
- **Correction rate is the north star, and the naive version of it lies in two specific ways.**
  Every layer past the decoder — vocabulary repair, formatting, per-app profiles, any future semantic
  tier — is a bet about what the user meant, and the only honest score is how often they still had to
  fix the text. `corrections.py` computes it, `readback.py` observes it, `store.correction_summary()`
  aggregates it. Two guards do all the work, both because the obvious implementation is wrong:
  (1) **Continuation is not correction.** The cursor is left flush against Say's last word, so the
  user's next keystrokes land in the *same* aligner block as our final token — "cafe." →
  "café today and it was good." scores as a total rewrite of the span when we actually got it right
  and they simply kept writing. `_split_boundary_growth()` peels the overhang off the trailing block.
  It is **trailing-only on purpose**: splitting the leading block too turns the ordinary expansion
  "lets" → "let us" into the nonsense pair `("lets", "us")`. (2) **A rewrite is not correction** —
  past `MAX_DRIFT` (0.5) the user changed their mind or the anchors relocated onto the wrong region,
  and recording it would teach the system something they never asked for. Also: **the denominator is
  insertions, not correction rows.** The dictations nobody had to touch leave no row and are the
  entire point, which is why `summarise()` takes `insertions=` rather than counting what it is given.
- **Unmeasured is not clean — the denominator is what the read-back actually compared.** The first
  run of `tools/corrections_report.py` against a real database reported **"100% left untouched, 0 of
  109 insertions edited"** from a feature that had never once executed: those dictations predated it,
  nobody had looked at them, and "we did not look" was rendering as "we got it right". Hence the
  additive `transcripts.observed` column, set by `readback._compare()` only once the span has been
  relocated and diffed (clean *or* corrected), and never when the field vanished or the app exposes
  no text to AX. `correction_summary()` counts `observed = 1` rows only, and returns `inserted_total`
  alongside so the gap between them reads as coverage. Every pre-existing row migrates to `0`, which
  is the correct history: unmeasured. `test_insertions_we_never_read_back_are_not_counted_as_clean`
  pins the 109-row case. **Any future quality tier inherits this rule** — a metric that flatters an
  unmeasured pipeline is worse than no metric, because it ends arguments that should still be open.
- **The read-back may only ever see text Say itself produced.** Reading the focused field means
  touching a document the user did not hand us, so the contract is structural, not a promise: only the
  element Say just injected into, only inside a window it opened itself (no polling, no scanning),
  document context held **in memory** purely to relocate our span and dropped the moment the timer
  fires, and only our span + what it became persisted. `_relocate()` returns None rather than guess —
  a wrong region fabricates a correction, which is worse than no data because everything downstream
  then learns from it. It requests **no new permission**, riding the Accessibility grant injection
  already needs, and fails closed and silent without it (`AXIsProcessTrusted()` false → `-25204`).
  `test_document_context_is_dropped_once_it_has_been_used` pins the persistence half.
- **Measure before tuning the maturity layer — this repo has never done it any other way.** The style
  seed, `TILT_DB_PER_BAND`, `NOISE_RISE_DB_PER_S` and the repetition thresholds were all set by
  replaying the stored corpus (`replay_bench.py`, `waveform_bench.py`), never by guessing. A semantic
  tier is far easier to fool yourself about than a DSP constant, so it gets the same discipline: the
  correction ledger exists first, and any refiner is scored against it on real stored dictations
  before it is allowed near the injection path.
- **A strong text prompt with almost no audio makes the decoder loop.** The single worst failure this
  engine has shipped: a 0.63 s clip returned 222 words of "available available available…", which were
  typed straight into the user's document. Whisper's own guards (`compression_ratio_threshold`, the
  temperature ladder) only *re-roll* the window — when every temperature degenerates the last attempt is
  returned anyway, so the loop reaches the app. Three independent defences, all required: (1) never decode
  a clip the mic did not contribute to (`recorder.live_seconds` < `controller._MIN_LIVE_SECONDS` → error,
  not silence); (2) drop `STYLE_SEED` from the prompt below `transcriber._STYLE_SEED_MIN_SECONDS` (2 s) —
  vocabulary terms still go through, they are a short comma list, not two sentences of prose; (3)
  `postprocess.collapse_repetitions()` as the last line of defence, including intra-token loops
  ("addholdholdhold…"). Thresholds (4 identical words/phrases in a row) were tuned against all 108 real
  stored dictations: at 4 only genuine loops collapse, at 3 natural emphasis ("and again, and again") was
  being flattened. **Validate any threshold change the same way — replay the store, don't guess.**
- **Our own injected keystrokes come back through our own CGEventTap.** `text_inject_cgevent` stamps
  `INJECTED_EVENT_MARKER` into `kCGEventSourceUserData` and `hotkey_fn._event_callback` returns those
  events untouched. Without it each injected chunk reads as the user typing, and a keystroke arriving while
  the trigger key is held abandons the gesture — so starting a dictation while the previous one is still
  being typed out would silently cancel it.
- **A key press the engine refuses must not look like a recording — this is what "the overlay disappears" was.**
  Both entry points used to show the glyph and open the microphone on key-*down*, unconditionally,
  and only then ask the controller for a recording. With a Whisper model active a long dictation can
  spend a minute in PROCESSING (streaming segments still decoding at 35–44 s each on a loaded
  machine, then injection), and someone who keeps dictating presses Fn inside that window. The
  controller refuses — the shipped engine's own log holds **58 refusals, every one during
  PROCESSING**, 56 of them directly after an `Fn down` — but nothing told the glyph or the mic:
  the mark switched from the processing sweep to live spectrum and followed the voice, the old
  job's READY event then `dismiss()`ed it mid-sentence, the words were never captured, and
  because the refused path never called `disarm()` the mic stream stayed open with the indicator
  lit. Replayed with the real listener, controller and overlay (`legacy` vs `bridge` wiring): the
  old path ends with `mic=open` and the glyph gone under a held key; the new one keeps the
  sweep up honestly and ends `mic=closed`. Fixed structurally in `gestures.py`: the arm is gated
  on `controller.can_start_recording()` (the same predicate `start_recording()` uses, so they
  cannot drift), a refused start retracts glyph + mic + listener, and both `SayApp` and
  `_run_server_only` share the one object. `test_gestures.py` pins it. **Do not re-inline these
  callbacks into app.py** — that duplication is precisely how the two paths diverged before.
- **AppKit-vended display links stop the instant their window leaves every display, and the
  overlay waits for frames to finish hiding.** Measured on this machine: 35 frames on screen,
  **0** while the panel was moved off every screen or ordered out, 42 once it returned. So a glyph
  caught mid-fade by a display going away (lid closed, external monitor unplugged) never reaches
  `_finish_hide`: `_visible` stays True, the dead link stays registered, and every later `_show`
  skips repositioning and ordering front because it believes the mark is already up — the overlay
  is gone until the engine restarts, with nothing in the log. `CLOCK_STALE_S` (1 s; a healthy link
  ticks every 16 ms) is the tell: `_show`/`_hide` on a visible glyph with a silent clock log a
  warning, drop the panel and rebuild it on the active screen. `test_overlay_window.py` pins the
  recovery and that a live clock is never touched. **Diagnose a missing glyph with
  `tools/overlay_doctor.py` and `grep 'clock stalled' say.log`** before reading the drawing code.
- **Re-enabling a disabled event tap is only half the recovery.** macOS disables a CGEventTap when the
  callback runs long (`kCGEventTapDisabledByTimeout`) or on certain user input
  (`kCGEventTapDisabledByUserInput`), and delivers **nothing** until it is re-enabled — 19 such outages
  appear in a single day of `say.log`. `hotkey_fn` used to just call `CGEventTapEnable` and return, which
  loses any Fn *release* that happened inside the dead window: `_fn_down` stays True, the mode never
  leaves PENDING/PUSH_TO_TALK, the in-flight clip is never stopped, and every later press is refused with
  `start_recording rejected — already in DictationState.RECORDING`. Dictation is then dead until relaunch
  — the "nothing works any more" report. `_resync_after_tap_outage()` now reads the live modifier state
  (`CGEventSourceFlagsState` — the hardware, not our cache) after each re-enable and runs the normal
  release path when Fn is no longer held, so a push-to-talk clip is *transcribed* rather than discarded.
  An unreadable flag state is deliberately left alone: guessing there would cancel live recordings on a
  spurious read. `MAX_RECORDING_SECONDS` stays the backstop for an outage so total that even the disable
  notification never arrives.
- **The overlay's "instant" is bought at arm, not at record.** The glyph goes up on
  `on_arm` (trigger key *down*), not on `RECORDING` — the hotkey spends up to 250 ms deciding
  tap-vs-hold and `recorder.arm()` can then block for seconds re-opening the device, so showing on
  the state change would put the mark ~300 ms behind the key. Both wiring sites call the overlay
  **before** `recorder.arm()` for the same reason. `arm()` and `listen()` render identically on
  purpose: there is deliberately no visual event at the moment a gesture becomes a recording,
  because that is a transition the user did not ask for and would only read as a glitch.
- **The glyph is one path, not twelve layers.** `waveform.silhouette()` returns a single closed
  polygon for the whole mark. Twelve `CALayer`s would disagree about sub-pixel column boundaries
  (visible seams) and would read as a row of separate meters; the reference mark is one stepped
  silhouette standing on one base. Column x is snapped to a device pixel so the vertical steps stay
  crisp; bar tops are deliberately **not** snapped, or heights stair-step as they move.
- **Neighbour coupling is what makes it feel like one system.** Each bar is its own asymmetric
  spring (fast slightly-under-damped attack, slow critically-damped release), then a diffusion pass
  pulls each bar toward its neighbours. Drop the coupling and you get twelve independent VU meters;
  raise it and the spectrum smears into a blob. A consequence worth knowing before "fixing" it: an
  isolated full-scale band settles at ~0.71, not 1.0, because the membrane bleeds it sideways
  (`test_bars_rise_toward_their_band_and_settle` pins this).
- **Tune the overlay against the stored corpus, not against your own voice.**
  `say/tools/waveform_bench.py --stats` replays every saved dictation and reports per-band medians;
  if they slope, the spectral tilt is wrong and one end of the glyph is dead. That is how
  `TILT_DB_PER_BAND` was set (2.0 → 3.0 took the residual from -0.037 to -0.003 level/band, i.e.
  every band from 94 Hz to 5.2 kHz equally active). The same loop caught the gate bug below.
- **A noise floor that rises quickly will chase the speaker and shut the gate mid-sentence.**
  `NOISE_RISE_DB_PER_S` was 1.5, which meant a long sentence lifted the floor ~15 dB into the
  speaker's own level; measured over the corpus the gate was fully open for only 37% of voiced
  blocks. At 0.4 dB/s (plus a `NOISE_CEIL_DB` clamp) it is 78%. Falling is fast, rising is slow —
  a room gets quiet suddenly and loud gradually. `test_noise_floor_cannot_chase_a_long_utterance`
  guards it.
- **`setFloatingPanel_` assigns the window level, so it must come *before* `setLevel_`.** The glyph
  asked for `NSStatusWindowLevel` (25) on the line it was created and then spent its entire life at
  `NSFloatingWindowLevel` (3), because `setFloatingPanel_(True)` six lines later is implemented by
  *setting the level* and silently won. Floating level still clears ordinary windows, so it looked
  correct in casual use and disappeared exactly where dictation happens — under full-screen apps and
  other apps' floating panels. Two things made it survivable for so long and both are now closed:
  `overlay_do` swallowed every overlay failure at **debug**, invisible at the engine's INFO level (now
  a once-per-action **warning**), and the overlay tests were all pure because "the drawing needs a
  window server" — so nothing ever asked the panel what level it was actually at.
  `test_overlay_window.py` builds a real panel and asks; verified to fail (`3 != 25`) against the old
  ordering. `_build()` also re-checks the level itself and logs an error if it ever fails to stick.
  **Diagnose the glyph by interrogating the window, not by reading the drawing code** —
  `CGWindowListCopyWindowInfo` shows its real bounds/alpha/level in the *running* engine, and
  `tools/overlay_preview.py --report` proves the frame clock independently.
- **A multi-colour glyph is still one path — the colours are clipped through it.** The
  invariant that the mark is a single closed polygon (see above) is what stops it reading as
  twelve separate meters, so themes do **not** get twelve layers. `overlay.py` keeps a
  `_paint` `CALayer` holding one solid colour layer per column, masked by a `CAShapeLayer`
  carrying *the same silhouette path* the single-colour `_shape` layer uses. Columns
  therefore still share exact edges, each colour runs down through the continuous base, and
  exactly one of `_shape` / `_paint` is ever visible. Twelve sibling rects would disagree at
  sub-pixel boundaries and put visible seams between every colour.
- **The default overlay theme carries no colours, and that is load-bearing.**
  `overlay_theme.resolve('ink')` returns `None`, which is the signal to keep doing exactly
  what shipped before themes existed — ink follows the system appearance and
  `OVERLAY_CONTRAST` decides the policy. That is why every existing user's glyph is
  unchanged on upgrade and why the contrast tests still test what they were written for.
  A theme *with* colours overrides `OVERLAY_CONTRAST` outright: someone who picked
  red-on-black has said they do not want it adapted away.
- **Colour by column index is a stripe; colour by audio is a pattern.** The first version
  of themes assigned `colors[i % n]` — column 0 red, column 1 ink, column 2 red. Index never
  changes, so that draws the *identical* barber-pole in every frame of every dictation; it
  reads as a fixed stripe that happens to wobble, not as something responding to you.
  `overlay_theme.column_colors()` now takes the bar **heights** and a clock, and
  `overlay.py::_paint_columns` re-evaluates it every frame. Because `BarMotion` already
  diffuses neighbouring bars into one membrane, a colour derived from height inherits that
  coupling for free: runs of colour form, travel and break apart together — tightly
  synchronised, because it is all one spectrum, and never repeating, because speech does not.
  Verified on real layers: `alternate` gives the byte-identical field in every frame,
  `pulse` gives six distinct fields in six frames. `alternate` is kept only so the old look
  is still reachable, and it is deliberately **not** the default.
- **Repainting every frame has to be cheap, so cache the index.** `_palette` holds the
  theme's CGColors once (never allocate a CGColor per frame) and `_col_idx` remembers what
  each column currently shows, so a frame writes only the columns whose colour actually
  changed — typically one or two of twelve. Allocating twelve CGColors at 60 Hz to set
  colours that mostly did not change is the obvious version and it is pure waste.
- **`PULSE` needs a scatter term, and 0.35 was not enough.** Colouring purely on loudness
  collapses the entire row to one colour the moment every band reads the same level, which
  looks like the theme broke. Mixing in a fixed golden-ratio per-column offset fixes it —
  but measured on a flat spectrum, 0.35 still went uniform everywhere below 0.35 and above
  0.65, i.e. across most of the range anyone speaks in. At **0.5** the only levels that go
  uniform are true silence and true saturation, which *should* be uniform: every band really
  is the same then. `test_pulse_does_not_collapse_across_the_speaking_range` pins it.
- **`SPARK` ranks, it does not threshold.** A fixed loudness cutoff would light nothing at
  all when someone speaks quietly. Taking the top `SPARK_SHARE` of bars by rank means a
  whisper lights its own peaks exactly like a shout does.
- **`desktop/src/lib/glyphPattern.ts` mirrors `column_colors` and must stay exact.** The
  customiser's whole job is showing what the engine will actually do; a preview that colours
  by a different rule means tuning against a picture of something that does not exist. There
  is no shared implementation to lean on, so verify parity directly after touching either
  side: strip the types off the `.ts` with `sed`, drive both with the same random cases
  through `node` and `column_colors`, and diff. 93 cases across every pattern, colour count
  and the flat-spectrum edges agreed exactly when this was written.
- **The overlay rim is computed, never chosen.** The rim exists so one of the two tones
  always has contrast against an unknown backdrop — that is a legibility guarantee, not a
  taste. Offering it as a colour picker is offering people a way to make the glyph
  invisible, so `auto_rim()` takes the mean luminance of the chosen colours and returns the
  opposite tone. A multi-colour theme is also always rimmed, because a two-tone mark is
  guaranteed to have one colour that vanishes against *some* background.
- **Decoder repetition loops must be excluded from any vocabulary statistic.** A single
  degenerate transcript can carry 200+ copies of one word (see the repetition-loop gotcha
  above), and against the real corpus that put `drag` (218×, all from one dictation) and
  `available` (226× across nine) straight into the user's "most-said words" — inventing a
  vocabulary they do not have. `vocabulary.is_degenerate()` drops any transcript of ≥12
  words whose unique-word ratio is below 0.25; measured on the corpus, genuine dictations
  sit at 0.30–0.85 even when repetitive while the degenerate rows sat at 0.02–0.14. The
  transcripts are kept — only their *vocabulary* is untrustworthy. **Write test fixtures for
  this module carefully:** `'x ' * 40` is a decoder loop by the module's own definition, and
  so is `f'word{i} …'`, because the tokeniser keeps letters only and every numeric suffix
  collapses back onto the same word. Both mistakes were made writing these tests.
- **Nothing is claimed about a speaker until there is enough to claim it.** Below
  `MIN_WORDS` / `MIN_DICTATIONS` the report comes back with `enough_data=False` and *no*
  insights, and the UI says so. "You say 'the' once every 18 words" over four dictations is
  noise wearing a lab coat — the same failure as scoring an unmeasured pipeline as clean
  (see the read-back gotcha). Counts are still reported, because those are things we
  measured; only the narrative waits.
- **A phrase is rarer than the words in it, so it cannot share their threshold.** At the
  word-level spread threshold, `just go ahead` (51× across 39 of 336 dictations —
  unmistakably a signature) was dropped while the fragments `just go` and `go ahead`
  survived, which is exactly backwards. Phrases get their own `PHRASE_MIN_SPREAD`, and a
  trigram absorbs the bigrams inside it so one habit is not reported three times.
- **The overlay must never be able to take focus.** It is an `NSPanel` with
  `NSWindowStyleMaskNonactivatingPanel`, `canBecomeKeyWindow`/`canBecomeMainWindow` overridden to
  False, and `ignoresMouseEvents`. Focus is where the transcript is about to be typed; a window that
  can steal it turns feedback into a bug. There are no buttons on it for the same reason.
- **`feed_audio()` runs on the audio thread and must not retain the block.** sounddevice hands back
  a view onto a buffer PortAudio reuses, so `BandAnalyzer.feed()` copies anything it keeps
  (`test_feed_does_not_retain_the_callers_buffer`). It also swallows every exception: visualisation
  can never be allowed to break capture.
- **Frame timing is measured, not assumed.** `overlay_preview.py --report` prints the real
  frame-interval distribution; the shipped build measures mean 16.70 ms / p99 16.88 ms / 0 late
  frames over a 430-frame run. Every value in `waveform.py` is integrated against that measured dt
  and sub-stepped at 1/120 s, so a dropped frame changes nothing about where the motion ends up —
  which is why a stall cannot make the glyph jump.
- **The mark is 5:2 — never square it off.** The logo is a frozen frame of the dictation overlay: a base with six stepped columns on a 30x12 grid, stored as `(width, height)` pairs because the reference's column widths are genuinely uneven (nearly 1.7:1 widest-to-narrowest) — there is no uniform pitch to divide by. It was 5:1 on a 60x12 grid until Aug 2026; the re-proportioned mark is twice as tall for its width, which is what finally made it legible at 16 px. Anything that sets a square size on it turns it into a smear. Two places actively fight this: `brand.menubar_template()` scales by *height* (not `setSize_((18, 18))`), and `app.py::_unsquash_status_icon()` undoes the `setSize_((20, 20))` that rumps hard-codes in `_nsimage_from_file` for every status icon. The status item is `statusItemWithLength_(-1)`, so a wide image is fine once the size is right. `test_brand_assets.py` fails if either regresses.
- **A badged menu-bar icon cannot be a template image.** Adding a coloured dot means `setTemplate_(False)`, which means the black mark is drawn literally — and disappears against a dark menu bar. That is why non-idle states recolour *the whole mark* (`_STATE_COLOR`) instead of badging it. The colours are mid-tone on purpose: full-strength ink or white fails on one bar or the other.
- **Menu-bar icon caches need a fingerprint.** Rendered PNGs live in `~/Library/Caches/Say/menubar/` indefinitely. Cache on `idle.png` and every existing user keeps the previous release's glyph forever. Filenames carry `_asset_fingerprint()` (a hash of the asset bytes), and stale entries are pruned on write.
- **Generated icons: `Image.BOX`, never `LANCZOS`.** `gen_logo.py` supersamples 8x and reduces. At an exact integer ratio BOX is plain area-averaging; LANCZOS has negative lobes that ring on the mark's long horizontal edges and leave a white seam between the base and the columns. Icons ≤128 px skip supersampling entirely and snap to the destination pixel grid (`snapped_mark_rects`, one-pixel floor per column) — at 16 px the mark is four pixels tall and a supersampled fill is a grey smudge. `snapped_mark_rects` also has to repair its own rounding: uneven widths at 10 px total can round a column away entirely, so edges are pushed forward to leave one pixel each and then the tail is pulled back onto the right border.
- **`say/assets/` must be in `Say.spec` datas.** It was not, so the packaged app had *no logo at all* — `brand.py` found nothing and every surface silently fell back to a runtime-drawn placeholder. `brand._find_assets_dir()` now resolves dev / `sys._MEIPASS` / `Contents/Resources` the same way `transcriber.py` and `model_catalog.py` find models.
- **The system appearance does not tell you what is behind the overlay.** People run macOS dark and read white pages all day; `effectiveAppearance()` then says "dark", the glyph draws in near-white ink, and it vanishes into the page. A soft shadow does not rescue it — at 0.22 alpha under near-white ink on white there is nothing to see. The mark is now drawn twice: the silhouette in ink, and beneath it the same path fattened by `RIM_PT` in the opposite tone (the subtitle trick — white glyph, dark outline). One of the two always has contrast, on light, dark, mid-grey and photographic backdrops alike. `OVERLAY_CONTRAST` picks the policy (`auto`/`system`/`dark`/`light`); note `dark` and `light` name the **ink**, not the theme. The alternatives were considered and rejected: `differenceBlendMode` goes invisible against mid-grey and yields odd hues on saturated colour, and sampling the backdrop with `CGWindowListCreateImage` needs a **Screen Recording** grant, which a local-first dictation app should not be asking for.
- **An auto-ranging level meter runs backwards.** The overlay originally normalised each band against the loudest band seen recently, with instant attack. That reference rises exactly as fast as your voice does, so it cancels itself out — measured across the corpus, whisper and shout drew the *same* glyph (`[0.544, 0.556, 0.559, 0.559]` for 4 amplitudes spanning 27 dB) — and when you stopped talking the reference decayed while the room did not, so the mark bloomed into the silence. Over one 262 s dictation the quietest 30% of blocks drew taller bars (0.56) than the loudest (0.21). The window is now anchored *down* to the room (`DISPLAY_FLOOR_OVER_NOISE_DB` above the noise floor, which moves the right way — a louder room pushes bars down) and *up* to a speech-level estimate that adapts over seconds and only while the gate is open. Result on the same corpus: loud 0.72 / quiet 0.06.
- **Estimate the noise floor with minimum statistics, not rise/fall.** A tracker that rises when the input exceeds it cannot tell a louder *room* from a louder *speaker*, so it climbs into the speech: −79 dB → −42 dB over one dictation, which lifted the display floor above the speaker and collapsed the glyph after ~90 s. Suppressing the rise while a voice is present just moves the failure — the floor still falls during inter-syllable gaps but can never recover, so it ratchets to the clamp and the gate stops closing at all. The floor is a low order statistic of the RMS over `NOISE_WINDOW_S` (8 s): speech has gaps, so the quiet tail of a few seconds finds the room even mid-sentence, and it needs no feedback from the gate it feeds. **It must be a quantile (`NOISE_QUANTILE` = P10), never the raw minimum** — see the next bullet.
- **A raw minimum latches, and that is what made the glyph "hang" for the first few seconds.** The single most visible overlay defect shipped: for roughly the first 8 s of every dictation the mark stood at full height and barely moved, then abruptly became responsive. It was not the springs — it was `min()`. A minimum is the *extreme* order statistic, so it locks onto the single quietest block in the window, and two things guarantee such a block exists at the start of every dictation: (1) the mark goes up on trigger-key *down*, so the analyzer is fed from the moment the mic stream opens, and an opening or glitching CoreAudio stream returns buffers of **exact digital silence** (−240 dBFS) — 141 of 148 stored dictations contain at least one; (2) the room before the first word genuinely *is* the quietest thing in the clip. Either one drags the estimate 20–40 dB below the room the rest of the dictation is spoken in, and since the display window is anchored to it, every band maps near the top. Measured over the corpus: the room estimate sat at **−83 dB for exactly `NOISE_WINDOW_S`** and then jumped to −42 dB when the offending block aged out of the deque — that step *is* the moment the user sees it "come alive". Bar height ran 0.85 / **46% of bands pinned at 1.0** across the first two seconds and 71% by the fourth, against 0.29 / 5.7% settled. Fixed with three changes, all needed: `NOISE_IGNORE_BELOW_DB` (a mic always delivers *some* noise, so an all-zero block is a dropout that teaches nothing about the room), `NOISE_QUANTILE` = 0.10 instead of `min()`, and seeding the estimate from the first real measurement rather than easing away from `NOISE_INIT_DB` (a placeholder, not a prior). `NOISE_MIN_BIAS_DB` went 3.0 → **0.0** as part of the same calibration — a raw minimum under-reads and needed lifting, a P10 does not — which lands the *settled* floor exactly where it was, so only the onset changed: settled height 0.286 vs 0.292, bands pinned 4.6% vs 5.7%, gate open on 55.6% vs 56.6% of speech blocks, while the first two seconds went 0.77 → 0.33 and 46% → 6% pinned. Corpus-wide the frame p95 fell from **0.992 (i.e. pinned) to 0.658** and the tilt residual improved (+0.0062 → −0.0019). Cost is one `sorted()` of ~125 floats per block: 3.1 µs against a 64 ms budget.
- **Test audio DSP with realistic stimulus.** The first version of these regression tests used a steady sine and passed happily against the fully inverted build — a constant tone has no pauses (so the noise window never sees the room) and no dynamics (so nothing loudness-dependent is exercised). Use bursts-with-gaps over a room-tone bed (`speech_like()`), and measure the **mean** across bands, not the max: the tallest bar saturates at 1.0 the moment the reference resembles the signal, which reads a broken build as fine.
- **A TCC grant applies to the running process — only the startup check is stale.** `AXIsProcessTrusted()` flips to True inside the already-running engine the moment the user toggles the switch, and a CGEventTap created after that point is accepted. The universal "quit and reopen" advice is about apps that only ever check once at launch. This bit hard in practice: the user grants Accessibility, restarts the *window*, and stays on ctrl+space — because the engine is a separate process that outlives the window (`/listener` then reads `accessibility_trusted: true` **and** `kind: legacy`, which is the tell). `ListenerUpgrader` polls the cheap non-prompting check and then tries to build a *real* tap, swapping only if that succeeds — so if a future macOS does require a restart, it simply never fires. It backs off exponentially when trusted-but-refused, which means Input Monitoring is the one still missing.
- **App identity ≠ data path.** Built app is "Say" (`com.ritikyadav.say.*`) but on-disk state lives under `…/Say/`. Changing one without the other breaks either TCC identity or the `runtime.json` handshake.
- **Audio on disk is opt-in.** Capture is in-memory by default; only when `SAVE_RECORDINGS` is on does `recordings.save_for_transcript()` write a WAV (still local, never uploaded). Saving is **best-effort and must never break dictation** — it runs after `store.insert()` inside the controller's guarded block. Route recording settings through `recordings.apply_recordings_config()` (persists via `settings` + prunes); don't poke `config` directly. Recordings dir is derived from the active store's path, so injecting a test store redirects it automatically.

- **The September 2026 production pass, one line each — the tells, so the next reader knows what was measured.**
  (B1) Quartz counts UTF-16 units, Python counts code points: chunk by units or every chunk with an
  emoji loses its tail (`"hello world 😀 test!"` came back without the `!`). (B2) Keystrokes go
  wherever focus is when transcription *ends*, seconds after the key was released — `focus_policy`
  holds the text (clipboard, `held` row, notification) when the frontmost app changed; unknown on
  either side injects. (B3) Streaming bypassed the short-clip prompt guard; a 0.4 s tail with the
  seed + carry-over looped for 19 s — pieces under `SHORT_SEGMENT_SECONDS` get the terms-only
  prompt. (B4) `metrics.get_ledger()` bound its path as a default argument, so it could not be
  redirected and tests wrote 290 fake failures into the real ledger — `tests/conftest.py` is the
  floor now, and it only runs under pytest. (B5) `LANGUAGE` was `'en'` with no UI; `None` is
  automatic and `/language` owns it — Moonshine ignores it and the UI says so. (B6) 56 of 64 tap
  outage warnings were our own `stop()`; the 8 real ones are GIL waits, mitigated by a 1 ms switch
  interval, fixed only by a helper process (PRODUCTION.md). (B7) A thread per hotkey callback let
  `on_stop` overtake `on_start`; `callback_pump` orders them. (B8) A decode slower than real time
  now warns (`note_realtime`), and PROCESSING events carry `progress` once the user is waiting.
  (B9) The clipboard fallback restores every pasteboard item and type, not just text. (B10) An
  empty result from a complete session that attempted a decode is silence, not failure — do not
  decode it again. (B11) Read `config.SHOW_NOTIFICATIONS` at call time. (B12) Kept audio is capped
  at 2 GiB by default. (B13) ruff and mypy are gates; CI runs pytest.

**TypeScript (`desktop/src/`):**
- TypeScript strict mode, target ES2021, path alias `@/*` → `src/*`.
- React Query for all data fetching (30s staleTime, 4 retries with exponential backoff).
- SSE for real-time state — `useEngineState()` hook, auto-reconnect with exponential backoff capped at 15s.
- Tailwind for styling. Brand color: dusty rose (`accent` in tailwind.config.js). Background: `canvas` (#F4EFE6). Font: Inter/SF Pro.
- Tauri commands called via lazy-loaded `@tauri-apps/api`. Dev fallback reads `VITE_DEV_ENGINE_PORT` from `runtime.json` in vite.config.ts.
- **Recordings playback needs `media-src` in the CSP.** `TranscriptRow` plays clips via a `blob:` URL in `<audio>`. The `tauri.conf.json` CSP **must** include `media-src 'self' blob: http://127.0.0.1:* http://localhost:*` — without an explicit `media-src` it falls back to `default-src 'self'`, WKWebView blocks the `blob:` media, and playback fails with a misleading "format not supported" (`MEDIA_ERR_SRC_NOT_SUPPORTED`). The WAV itself is fine; the fetch uses `connect-src`, the playback uses `media-src`.
- **Above-the-fold content must not depend on IntersectionObserver.** Every card enters
  through a `.rv` → `.in` class swap, and the first version of `useReveal` waited for the
  observer to report *every* element. On the overlay page — where all five cards are on
  screen at mount — only the first one ever got `.in` and the rest sat at `opacity: 0`
  forever: a page that rendered a heading and then nothing. The observer is a
  scroll-position signal, not a "is this element visible right now" oracle, and treating it
  as the latter turns a timing quirk into a blank screen. `useReveal` now measures
  `getBoundingClientRect()` one frame after mount and reveals anything within 1.15 viewport
  heights outright, leaving the observer responsible only for what genuinely needs scrolling
  to. A card that fades in a beat early is a non-event; a blank page is not.
- **`transform` and `width` do nothing on a non-replaced inline element.** The insight bars
  were `<i class="f">` inside a `<span>`, and `<i>` is inline: `width: var(--w)` and
  `transform: scaleX()` were both silently ignored, so every bar rendered as an empty
  track. Nothing errors, nothing warns — the bar is simply never there. Sibling bars built
  from `<div>`, and from `<i>` inside a flex container (which blockifies its children),
  worked fine, which is what made it look like a data problem rather than a CSS one. Any
  element you intend to size or transform needs `display: block` unless a flex/grid parent
  is already giving it one.
- **The insight copy lives in Python, not TypeScript.** `vocabulary.py` emits fully composed
  `headline`/`body` strings carrying `*em*` / `**bold**` markup, and `lib/markup.tsx` turns
  that into React elements. The alternative — shipping raw numbers and assembling sentences
  in the UI — puts the threshold that decides whether a claim is *true* in one language and
  the words that make the claim in another, and they drift. Render the markup as elements;
  never `dangerouslySetInnerHTML`.
- **Webfonts must be self-hosted — the CSP blocks remote ones, silently.** `index.html` linked
  `fonts.googleapis.com` for Fraunces / Geist / Geist Mono, but the `tauri.conf.json` CSP is
  `style-src 'self' 'unsafe-inline'` and `font-src 'self' data:`. Neither lists the Google hosts, so the
  stylesheet *and* the `.woff2` files it points at were blocked in the packaged app and every surface
  fell back to Georgia / system-ui — no error, just the wrong typography. The files now live in
  `desktop/public/fonts/` with `@font-face` rules at the top of `src/styles/index.css` (latin +
  latin-ext only, ~206 KB; all three families are variable, so the 500/600 rules reuse the 400 file).
  It is also the right answer on principle: an app that promises audio never leaves the Mac should not
  phone Google on every launch. **Don't "fix" a missing font by widening the CSP** — ship the file.

**Rust (`desktop/src-tauri/`):**
- Edition 2021, MSRV 1.77. Tauri 2 with `macos-private-api` and `tray-icon` features.
- Unix-specific: `libc` for SIGTERM/SIGKILL sidecar management.

---

## Key Constants (`config.py`)

```python
MODEL = 'moonshine/medium-streaming-en'         # bundled default (Moonshine ONNX, English)
HOTKEY = 'fn'                                     # prefer Fn listener
HOTKEY_LEGACY = 'ctrl+space'                      # pynput fallback
SAMPLE_RATE = 16_000                              # Whisper requirement
MAX_RECORDING_SECONDS = 900                       # 15-min safety cap (auto-stops + transcribes); user-overridable
MIN_AUDIO_SECONDS = 0.4                           # reject too-short clips
PRE_ROLL_SECONDS = 0.5                            # ring buffer before hotkey
DEVICE_WATCH_INTERVAL_S = 2.0                     # input device polling
LANGUAGE = 'en'                                   # or None for auto-detect
LANGUAGE_FOCUS = 'english'                        # what the person dictates in: english | european | south_asian | any (languages.FOCUS_GROUPS)
INJECT_METHOD = 'auto'                            # 'auto' | 'accessibility' | 'clipboard'
INITIAL_PROMPT = ''                               # Whisper vocabulary bias (<100 tokens)
SHOW_OVERLAY = True                               # floating dictation glyph (overlay.py)
OVERLAY_CONTRAST = 'auto'                         # 'auto' adds a contrast rim; 'system' is the old theme-only behaviour
OVERLAY_THEME = 'ink'                             # glyph colours; 'ink' = adaptive (pre-themes behaviour). See overlay_theme.py
OVERLAY_CUSTOM_COLORS: list[str] = []             # hex colours used across the 12 columns when OVERLAY_THEME == 'custom'
OVERLAY_PATTERN = 'pulse'                         # how those colours land: pulse/spark/flow/drift/alternate
USER_NAME = ''                                    # what the app calls you; blank = OS account name. Local only.
ONBOARDED = False                                 # desktop first-run flow completed
SHOW_NOTIFICATIONS = True
OVERRIDE_FN_KEY_ACTION = True                     # neutralize globe-key
PREFER_LEGACY_HOTKEY = False
STREAMING_TRANSCRIPTION = True                    # decode segments while the user is still talking
AUTO_CORRECT = True                               # deterministic post-processing master switch
AUTO_CAPITALISE = True                            # sentence capitals + standalone "I"
AUTO_TERMINAL_PUNCTUATION = True                  # add a full stop when the last sentence lacks one
DICTIONARY: list[str] = []                        # vocabulary: biases the decoder AND repairs near-misses
DICTIONARY_REPLACEMENTS: dict[str, str] = {}      # literal whole-word substitutions
STYLE_SEED_ENABLED = True                         # prepend a punctuated sample to the decoder prompt
CONDITION_ON_PREVIOUS_TEXT = True                 # keep ON — see the style-drift table in Code Conventions
LEARN_FROM_CORRECTIONS = True                     # read the field back after inserting; measures correction rate
CORRECTION_READBACK_DELAY_S = 8.0                 # how long to wait before the read-back
SAVE_RECORDINGS = False                           # opt-in: write per-dictation WAV to disk
RECORDINGS_RETENTION_MODE = 'size'               # 'keep_all' | 'days' | 'size' — a cap by default (587 MB in 3 weeks happened)
RECORDINGS_RETENTION_DAYS = 30                    # used when mode == 'days'
RECORDINGS_MAX_BYTES = 2 * 1024**3               # 2 GiB; used when mode == 'size'
```

User overrides persist to `~/Library/Application Support/Say/settings.json`. Whitelisted keys: MODEL, LANGUAGE, LANGUAGE_FOCUS, INJECT_METHOD, INITIAL_PROMPT, SHOW_NOTIFICATIONS, SHOW_OVERLAY, OVERLAY_CONTRAST, OVERLAY_THEME, OVERLAY_CUSTOM_COLORS, PRE_ROLL_SECONDS, MAX_RECORDING_SECONDS, MIN_AUDIO_SECONDS, DEVICE_WATCH_INTERVAL_S, SAVE_RECORDINGS, RECORDINGS_RETENTION_MODE, RECORDINGS_RETENTION_DAYS, RECORDINGS_MAX_BYTES, LEARN_FROM_CORRECTIONS, CORRECTION_READBACK_DELAY_S.

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
  success         INTEGER NOT NULL,       -- 0 or 1
  audio_path      TEXT,                   -- path to saved WAV, or NULL (added by _migrate)
  observed        INTEGER NOT NULL DEFAULT 0  -- read-back actually compared this one (added by _migrate)
);
CREATE INDEX idx_ts ON transcripts(ts_utc);

-- What the user changed after Say typed. One row per *observed* correction:
-- insertions nobody touched leave no row, so any rate needs the insertion
-- count as its denominator (store.correction_summary()). Only Say's own span
-- and what it became are stored — never the surrounding document.
CREATE TABLE corrections (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  transcript_id   INTEGER NOT NULL,
  ts_utc          TEXT NOT NULL,
  source          TEXT NOT NULL,      -- 'observed' (read-back) | 'manual' (in-app edit)
  inserted        TEXT NOT NULL,      -- what Say typed
  corrected       TEXT NOT NULL,      -- what it became
  edit_distance   INTEGER NOT NULL,   -- word-level distance
  words           INTEGER NOT NULL,   -- word count of `inserted`
  target_app      TEXT
);
CREATE INDEX idx_corr_ts ON corrections(ts_utc);
CREATE INDEX idx_corr_transcript ON corrections(transcript_id);
```

**Other files:**
| Path | Contents | Mode |
|------|----------|------|
| `~/Library/Application Support/Say/runtime.json` | `{ host, port, token, pid, owner_pid? }` — per-launch handshake. `owner_pid` is the shell that launched the engine; **absent** means hand-started (dev/menu-bar) and nobody's to kill | 0600 |
| `~/Library/Application Support/Say/settings.json` | User config overrides | default |
| `~/Library/Application Support/Say/say.lock` | Single-instance advisory lock (fcntl) | default |
| `~/Library/Application Support/Say/fn_usage_backup.json` | Original AppleFnUsageType (crash-safe restore) | default |
| `~/Library/Application Support/Say/first_run_complete` | Marker file (onboarding done) | default |
| `~/Library/Application Support/Say/recordings/<id>.wav` | Per-dictation audio receipt — **only when `SAVE_RECORDINGS` is on** | default |
| `~/Library/Application Support/Say/exports/Say-Recordings-*.zip` | Local export bundles (clips + manifest); revealed in Finder | default |
| `~/Library/Logs/Say/say.log` | Rotating log (10MB × 5 files) | default |
| `~/Library/Logs/Say/metrics.jsonl` | Transcription timing + error records (JSON per line) | default |
| `~/.cache/moonshine/download.moonshine.ai/model/<leaf>/quantized/` | Downloaded Moonshine ONNX model (`.ort`). Bundled default also lives in `Say.app/Contents/Resources/models/<leaf>/`. | default |
| `~/.cache/huggingface/hub/models--*/` | Cached Whisper models (downloaded on demand from the Models tab) | default |

Audio is **in-memory only by default** — the ring buffer is never written to disk. **Opt-in exception:** when `SAVE_RECORDINGS` is enabled, each dictation's clip is saved as a WAV under `recordings/` (still 100% local — never uploaded). Retention (keep-all / N-days / size-cap) and a local ZIP export are user-controlled from the Recordings tab.

---

## Permissions & Security

| Permission | Required By | Why |
|-----------|-------------|-----|
| Microphone | Engine | Audio capture via sounddevice |
| Accessibility | Engine | CGEvent keystroke injection + Fn CGEventTap listener |
| Input Monitoring | Engine (Fn mode only) | CGEventTap requires it for Fn key detection |

Security model: loopback-only API (127.0.0.1), per-launch random Bearer token (`secrets.token_urlsafe(24)`), CORS restricted to `tauri://localhost` + dev origins, audio never persisted by default, clipboard restored after injection, `runtime.json` and `settings.json` mode 0600.

**Findings from the pre-ship audit, and why each fix is the way it is** (all pinned by `test_security.py`):

- **The token comparison was `!=`.** Now `secrets.compare_digest`. The attacker has to be a local process running as this user before it matters, so it is defence in depth rather than the load-bearing control — but it costs one import.
- **FastAPI mounted `/docs`, `/redoc` and `/openapi.json` unauthenticated, and nobody chose that.** Two reasons they are now `None`: an unauthenticated machine-readable index of every route is free reconnaissance, and **Swagger UI fetches its own JavaScript from a CDN** — so merely opening `/docs` would make an app that promises never to phone home do exactly that. `test_the_set_of_unauthenticated_routes_is_exactly_these_two` pins the open set to `/health` (the shell probes it before it has the token) and `/listener` (permission booleans only, polled to decide whether to show the banner).
- **`notify()` interpolated the transcript into an AppleScript string.** The escaping was actually correct — backslash before quote, which is the right order — but an AppleScript literal cannot contain a raw newline, so **every multi-line transcript produced a syntax error and silently showed no notification at all**, invisible because osascript fails with a non-zero exit rather than an exception. Text is now passed through `argv` (`on run argv` / `item 1 of argv`), which has no quoting to get wrong, and whitespace is collapsed first.
- **`realpath(target).startswith(cache_root)` is not a containment check.** It also authorises a sibling that merely shares the prefix (`~/.cache/moonshine` vs `~/.cache/moonshine-evil`). Now `_inside()` via `os.path.commonpath`, resolving **both** sides — comparing a resolved path against an unresolved root reports "outside" for things plainly inside, because `/var` is a symlink to `/private/var` on macOS. Not reachable either way: `is_known()` allowlists every id against the catalogue before any path work, which is the control that actually holds.
- **`reveal_path` was dead IPC surface** once the Recordings tab went away — a Tauri command taking an arbitrary path from the webview, called by nothing. Removed from both sides.
- **`settings.json` was 0644.** The containing directory is 0700 so nothing else could read it, but the file now holds a name the user typed, and a file's mode should not depend on where it happens to live.
- **`react-router-dom` carried three moderate advisories** that `npm audit fix` could only clear with a v7 major bump. None were reachable (no SSR, every navigation target a hardcoded literal, strict CSP with nowhere to redirect *to*) — but the app used four symbols across three static routes, so the dependency was replaced by `lib/router.tsx`, ~40 lines. `npm audit` is now clean and the bundle is 16 KB smaller.

**macOS TCC gotchas (hard-won — packaged app permission failures almost always trace here):**
- **Engine must be launched via LaunchServices** (`open`), not exec'd as a subprocess — else its own Accessibility/Input-Monitoring grants don't apply (see `sidecar.rs`). Verify with `/listener` → `accessibility_trusted` and engine PPID==1 (launchd).
- **Engine bundle must keep a VALID code-signature seal.** macOS silently refuses every TCC grant to an app whose seal is broken (`codesign --verify --strict` fails). A broken seal is worse than unsigned. Three things that break it, all fixed: (1) editing `Info.plist` after signing — bake plist keys into `Say.spec`'s `BUNDLE(info_plist=…)`, never `cp` over it; (2) adding the model to `Resources/` after signing — `bundle-model` re-seals (non-deep) afterward; (3) `rsync -aL` when embedding — `-L` flattens `Python.framework`'s `Versions/Current` symlink and destroys the seal, so `embed-engine` uses `ditto`.
- **Strip `com.apple.provenance` BEFORE editing a freshly-built bundle, not after.** On macOS 15+ a
  newly-built `.app` carries that xattr, and while it is present every write into `Contents/` fails
  with `Operation not permitted`. `PlistBuddy` still exits 0, so the build happily reported `[OK]`
  while shipping an app with **no usage strings and `CFBundleExecutable=say-desktop`**. `patch-plist`
  now strips xattrs first and *verifies* each key and the binary name afterwards, fatally.
- **Ad-hoc signing is what makes "permissions granted but Fn off" recur.** An ad-hoc signature gives a bundle a Designated Requirement of `cdhash H"…"` and *nothing else* — no bundle id, no anchor (check with `codesign -d -r- <app>`). TCC captures that string when the user grants Accessibility/Input Monitoring and re-validates it on every launch, so **every rebuild silently orphans the grant**: System Settings still shows "Say" switched on (that pane keys its row on bundle id + path) while `AXIsProcessTrusted()` returns False and the CGEventTap is refused. The tell in `/listener` is `accessibility_trusted: false` right after a build. Fixed by signing with a stable local identity (`make -C say signing-identity`), which yields `identifier "com.ritikyadav.say.dictation" and certificate root = H"…"` — invariant across rebuilds, verified by rebuilding and relaunching without re-granting. `SAY_SIGN_ID` overrides the identity name; both Makefiles and `Say.spec` fall back to ad-hoc when it isn't in the keychain, so a fresh clone still builds (and inherits the old breakage). **Don't revert the signing back to `--sign -`.**
- **`export SAY_SIGN_ID` with no value exports an *empty string*, and empty is not absent.** This is why
  the ad-hoc regression above kept coming back. Both Makefiles resolved
  `SIGN_ID := $(if $(SAY_SIGN_ID),$(SAY_SIGN_ID),Say Local Signing)` and then did a bare
  `export SAY_SIGN_ID` — exporting the *input* variable, empty, not the resolved one. `Say.spec` read it
  with `os.environ.get('SAY_SIGN_ID', 'Say Local Signing')`, whose default only applies when the key is
  **absent**; a present-but-empty value sailed through, `security find-certificate -c ''` failed, and the
  spec fell back to `-`. Net effect: **every build by anyone who had not manually exported `SAY_SIGN_ID`
  ad-hoc signed the engine**, certificate installed or not — producing `designated => cdhash H"…"` and
  orphaning the user's grants on every rebuild. Fixed on both sides: the Makefiles now
  `export SAY_SIGN_ID := $(SIGN_ID)`, and the spec uses
  `os.environ.get('SAY_SIGN_ID') or 'Say Local Signing'`. `make build` also prints the resulting
  designated requirement and warns on a bare cdhash, so it can never fail silently again.
  `test_build_signing.py` pins all three.
- **Stale LaunchServices registrations put duplicate "Say" rows in the Privacy panes.** Old DMG mounts, `Say.app.old`, and build outputs under other checkouts all stay registered forever, each able to show its own row — so the switch you flip may not be the record `tccd` evaluates (the tell: `tccutil reset <service> <id>` prints "Successfully reset" more than once). Purge with `lsregister -u <path>` for paths that no longer exist before debugging a grant.
- **Two different bundles are called `Say.app`, and installing the wrong one looks like "the app is
  broken".** The desktop shell (`com.ritikyadav.say.desktop`) owns the window; the engine
  (`com.ritikyadav.say.dictation`) is `LSUIElement=true` and has **no UI at all** — it is a component the
  shell embeds at `Contents/Resources/SayEngine.app`. `say/make dmg` used to emit
  `Say-v0.1.0-unsigned.dmg` containing an app called `Say.app` **with an /Applications drop-link**,
  indistinguishable from the product DMG. Dragging that one over `/Applications/Say.app` yields an app
  that launches, shows nothing, and reads as "the interface won't load" — while `/health`, `/listener`
  and the Fn key are all perfectly fine, because the *engine* is exactly what got installed. Diagnose
  with `PlistBuddy -c "Print :CFBundleIdentifier" /Applications/Say.app/Contents/Info.plist` and by
  checking whether `Contents/Resources/SayEngine.app` exists. The engine image is now
  `SayEngine-v…-internal.dmg` with no drop-link and is no longer built by `make all`; install with
  `cd desktop && make install`, which refuses anything that isn't the shell.
- PyInstaller `.app`s are only post-hoc signable when built with `codesign_identity='-'` (it relocates data out of `Contents/Frameworks/` into `Resources/`; raw `codesign` can't seal data-in-Frameworks).
- Both shell (`com.ritikyadav.say.desktop`) and engine (`com.ritikyadav.say.dictation`) display as "Say" in Privacy panes — the **engine** is the one that needs the grants. After a rebuild, ad-hoc cdhash changes; if a grant misbehaves, `tccutil reset Accessibility com.ritikyadav.say.dictation` (+ `ListenEvent`, `Microphone`) and re-grant. Don't reset `Microphone` casually: the engine blocks on a mic prompt at startup, so a reset makes the next launch hang until the (possibly hidden) prompt is answered.

---

## Platforms

macOS arm64 ships today. The engine **core is already platform-neutral** — 26 modules
(`config`, `settings`, `validation`, `vocabulary`, `overlay_theme`, `waveform`,
`postprocess`, `corrections`, `streaming`, `model_catalog`, `model_manager`,
`transcriber`, `audio`, `device_watcher`, `injector`, `hotkey`, `recordings`, `metrics`,
`logging_setup`, `owner_watch`, `notifications`, `single_instance`, and all four
`server/*`) import with every Apple module blocked, enforced by `test_portability.py`
and a CI job. `requirements.txt` carries platform markers, so `pip install -r` resolves
on Windows and simply omits MLX, PyObjC and rumps.

**What a Windows build still needs**, and it is not small:

| Layer | macOS today | Windows equivalent |
|---|---|---|
| Global hotkey | `hotkey_fn.py` — CGEventTap on Fn | low-level keyboard hook (`SetWindowsHookEx`); **there is no Fn key to tap** — needs a different default chord |
| Text injection | `text_inject_cgevent.py` — CGEvent Unicode | `SendInput` with `KEYEVENTF_UNICODE` |
| Clipboard fallback | `text_inject_clipboard.py` — NSPasteboard | Win32 clipboard |
| Overlay glyph | `overlay.py` — non-activating `NSPanel` + `CADisplayLink` | layered click-through `WS_EX_NOACTIVATE` window, or a second Tauri window |
| Read-back metric | `readback.py` — Accessibility API | UI Automation |
| Sleep/lock events | `system_events.py` — NSNotification | `WM_WTSSESSION_CHANGE` |
| Menu bar / tray | `app.py` (rumps), `menubar_icon.py` | tray icon |
| Single instance | `single_instance.py` — `fcntl` | named mutex |
| Fn override | `fn_override.py` | not applicable |
| Whisper models | `mlx-whisper` (Metal, Apple Silicon only) | faster-whisper / whisper.cpp. **Moonshine (the bundled default) is ONNX and already cross-platform**, so the shipped experience ports; the optional Whisper catalogue entries do not |

**Neither toolchain cross-compiles.** PyInstaller must run on the target OS to emit a
binary for it, and Tauri needs Windows for MSI/NSIS and Linux for .deb/.AppImage — no
artifact for either can be produced from a Mac, only from a runner or machine of that OS.

### Linux is the easier second platform, and the CI job proves most of it

Measured, not assumed: **411 tests across 25 of the 30 test modules pass with every Apple
module blocked** (`linux-core` job). The five excluded are genuinely macOS-only — the icon
renderer, the CGEventTap hotkey, the two overlay-drawing suites, and CGEvent/NSPasteboard
injection. Everything else — the API, store, controller, audio, streaming, model
management, corrections, vocabulary, the overlay *theme and pattern* logic — is already
platform-neutral.

What Linux has that Windows does not:

| | Linux | Windows |
|---|---|---|
| Dependency wheels | all present, x86_64 + aarch64 (verified against PyPI, incl. `onnxruntime`) | MLX absent; rest fine |
| `single_instance` (`fcntl`) | works as-is | needs a named mutex |
| `owner_watch` | already falls back to polling where `kqueue` is absent — **verified by simulating its removal** | same fallback works |
| Data dir | `~/.local/share/Say` branch already in `store._app_data_dir` | `%APPDATA%` branch already there |
| Bundled model | Moonshine/ONNX runs | Moonshine/ONNX runs |

**The real Linux blocker is Wayland, not Linux.** On X11 the remaining work is ordinary:
`XGrabKey` for the hotkey, `XTest` for injection, an override-redirect window for the
glyph, AT-SPI for the read-back, `notify-send` for notifications. On Wayland, synthetic
input injection is *deliberately* unavailable — that is the compositor's security model,
and it is the exact capability this app is built on. The escape hatches are `ydotool`
(needs `uinput`, i.e. root or a udev rule) or the `RemoteDesktop`/`GlobalShortcuts`
portals, which are per-session, consent-gated and unevenly implemented. Since Wayland is
now the default on Ubuntu 22.04+, Fedora and GNOME generally, "Linux support" means
choosing between an X11-only build and a portal-based one with a materially different
permission story.

## Testing

36 test modules, 600 tests, <12s runtime. Run: `pytest say/tests/` — **pytest, never `unittest discover`** (the isolation fixture in `tests/conftest.py` and the fixture-based modules only run under pytest).
(19 skip under `SAY_CI=1`: the ones that need a window server or Accessibility.)

| Module | Tests | Covers |
|--------|-------|--------|
| `test_api.py` | 28 | FastAPI endpoints, auth, CORS, model mgmt routes (incl. 507/cancel), recordings (audio roundtrip/config/export/delete), **`/language` get/set/auto/400 + persistence**, the focus and its groups in GET, a focus applying its default language, a focus with a pinned language, a bad focus refused with nothing written |
| `test_conftest_isolation.py` | 5 | **The suite never touches real user files**: ledger, settings, store and model caches all resolve under the session temp root; the real paths are named so a regression says whose data it would have written |
| `test_callback_pump.py` | 8 | Ordered hotkey callbacks: a slow start followed by an instant stop completes `[start, stop]`, a raising callback does not stop later ones, stop is idempotent and safe from inside a callback, both listeners route through it |
| `test_languages.py` | 13 | Code normalisation (`en-US` → `en`, `auto` → None, garbage refused), Hindi offered, a bad persisted value tolerated, Whisper honours the setting and Moonshine does not; **the four focuses**, every focus code is a known language, English is pinned and the rest detect, Hindi and Urdu sit in the South Asian focus, recommended models are in the catalogue, an unknown focus is refused and `current_focus()` falls back from the language |
| `test_notifications.py` | 2 | The switch is read at call time; text still travels as argv |
| `test_vocabulary.py` | 21 | The insights engine: **a decoder loop cannot invent a vocabulary** (the `drag`/`available` regression), a short repeat is not a loop, a thin corpus yields counts but **no insights**, a word confined to one dictation is not a habit, **a trigram absorbs the bigrams inside it**, phrases never begin or end on a bare function word, `per` is one-in-N, hour/weekday buckets sum to the totals, malformed rows never raise, every insight carries a drawable viz |
| `test_overlay_theme.py` | 27 | Glyph colour themes: hex parsing (3- and 6-digit, canonical output), **`ink` stays adaptive** — the compatibility guarantee that nobody's glyph changes on upgrade — every fail-safe path (unknown id, empty custom, all-bad colours) falls back to adaptive rather than raising, the colour cap holds, every preset colour parses, and **the rim is always one of the two tones**, dark for a bright pair and light for a dark one |
| `test_security.py` | 16 | **The pre-ship audit, as regressions.** Several assert on the *shape* of the code rather than behaviour, because these failures are invisible at runtime — a blocked event loop just looks slow, a broken AppleScript literal just looks like notifications were switched off. Covers: constant-time token compare, the exact set of unauthenticated routes (`/health` + `/listener`, with `/docs` `/redoc` `/openapi.json` off), **`/stats/vocabulary` is not a coroutine** and is memoised + invalidated by a new dictation, transcript text reaches `osascript` as *argv* and a multi-line transcript still notifies, `_inside()` rejects a prefix-sharing sibling, unknown model ids never reach the filesystem, `settings.json`/`runtime.json` are 0600, no engine module opens an outbound connection, and the profile endpoint caps the name |
| `test_portability.py` | 2 | **Can this still run somewhere that is not a Mac.** Imports the 26-module portable core in a subprocess with AppKit/Foundation/Quartz/objc/rumps/PyObjCTools all blocked — unanswerable on macOS by any other means, since PyObjC is always importable and a module that quietly grows an AppKit dependency imports fine until someone tries to build for Windows. Also guards the Windows/Linux branches of the data-directory resolver |
| `test_overlay_api.py` | 17 | `/overlay` GET/POST/preview and `/stats/vocabulary`: a preset persists, an unknown theme or a non-hex colour is **refused with nothing written**, colours are stored canonically, `custom` with no colours is refused, saving succeeds with no glyph running, a live glyph is repainted-then-previewed, **a failing overlay cannot fail the request**, and `has_audio` is false when the recorded WAV is gone (a play button that 404s is worse than none) |
| `test_waveform.py` | 38 | Overlay engine: band edges, gate vs silence/speech, tone→band mapping, level bounds, quiet-and-loud speakers both fill the glyph, **caller's buffer is never retained**, **noise floor cannot chase a long utterance**, spring/frame-rate independence, dropped-frame safety, neighbour coupling, sweep travel, silhouette geometry, **louder speech draws taller bars**, **going quiet does not bloom**, **a dropout block cannot define the room**, **the glyph does not start saturated** (armed-and-silent draws nothing; the first seconds match the tenth) and **the room is measured, not faded in from a constant** (all verified to fail against the pre-fix DSP) |
| `test_overlay_window.py` | 15 | The overlay's **window and its layers** (not its decision tables): **sits at `NSStatusWindowLevel`** — the regression that had it at `NSFloatingWindowLevel` for its whole life — can never become key/main, ignores mouse, does not hide on deactivate. Plus the themed paint path, which nothing pure can reach: one column layer per band, **`ink` still paints nothing** (the upgrade-compatibility guarantee), a colour theme swaps which layer is visible, **adjacent columns differ and the list cycles**, a themed glyph is always rimmed, switching back to `ink` restores the plain glyph, a frame sets the mask path, and a corrupt custom theme falls back instead of raising. **Self-healing clock:** a visible glyph whose display link has gone silent is rebuilt on the next show, hiding it finishes immediately instead of waiting for frames that never come, a live clock is never touched, and recovery preserves `_recording`. Needs a window server; skips under `SAY_CI`. (Compare CGColors with `CGColorEqualToColor` — `str()` includes the pointer, and `CGColorGetComponents` bus-errors through the bridge.) |
| `test_overlay_contrast.py` | 30 | Glyph legibility: ink and rim are **always opposite tones** (the invariant), `auto`/`system` follow the appearance, `dark`/`light` name the *ink* not the theme and ignore it, rim on everywhere but `system`, unknown/empty mode falls back to `auto`, validation rejects a bad value |
| `test_brand_assets.py` | 18 | Brand assets + menu-bar icon: **the mark keeps its 5:2 aspect** (nothing squares it off), **the asset is authored at the size it is displayed at** (`gen_logo.MENUBAR_PT` vs `brand.MENUBAR_HEIGHT_PT` — drift there keeps the aspect but halves the glyph, which no aspect assertion can see), tile is square, only idle is a template image, every non-idle state has a mid-tone colour that survives both menu bars, **cache fingerprint invalidates on asset change**, stale entries pruned, PNGs are @3x |
| `test_streaming.py` | 32 | Silence splitting (lossless, pause/min/max rules), carry-over prompt, incremental segmenter, session join + failure isolation, **decode attempts counted (silence vs failure)**, **a short tail gets the terms-only prompt with no carry-over**, progress reported per decode and a raising progress callback never breaks the session |
| `test_postprocess.py` | 49 | Soundex, fuzzy vocabulary repair (incl. split/joined words + over-match guards), formatting rules, switches, `asr_prompt()`, **repetition-loop collapse (word/phrase/intra-token, natural-repetition guards) + style-seed scaling** |
| `test_postprocess.py` (see above) / `test_injector.py` | +4 | Secure-input detection: probe never raises, injection refused, neither backend called |
| `test_corrections.py` | 31 | Correction capture: **continuation is not correction** (both the pure-trailing case and the one fused into the boundary block), **a rewrite is not correction**, leading expansion is not mangled by the trailing-only split, case/punctuation alone is the formatter's business, term-pair extraction + size guards, store roundtrip + rate denominators, `_relocate` gives up rather than guessing, watcher records/ignores/fails-closed, **document context never reaches the database**, **unmeasured insertions are never scored as clean** (the 109-row regression), a clean read-back still counts as measured, a vanished field does not, timers bounded and cancellable |
| `test_owner_watch.py` | 21 | **The engine must not outlive the app.** A real child process is spawned and killed: the watchdog fires on a clean exit *and* on SIGKILL (Force Quit, which the shell cannot catch), the poll backstop still fires with kqueue disabled, an owner already gone at startup fires inline (so a quit during model load can't finish arming the engine), pid 0/1 is never watched (launchd never exits — watching it is watching nothing), `stop()` ends the watch without shutting down, the shutdown runs exactly once, a failing callback can't escape, **an unreadable process state counts as alive**, `runtime.json` records an owner for a sidecar but none for a hand-started engine, and the **exit backstop** fires when the clean shutdown stalls, runs its cleanup first, survives a failing cleanup and is cancellable. All seven mutations of the fix were verified to break these |
| `test_recordings.py` | 12 | WAV save+link, list/totals, read bytes, delete one/all, retention (days/size), ZIP export, config apply, **the shipped default is a 2 GiB size cap** |
| `test_audio.py` | 21 | AudioRecorder pre-roll, event dispatch, **`live_seconds` accounting + open-before-capture ordering**, and `DeviceSerialisationTests`: **arm() and start() never open the device concurrently**, **the PortAudio reset cannot run while another thread is in `Pa_OpenStream`**, a close cannot race an open, a wedged device is bounded rather than infinite and parks **one** thread not one per press, arm() never raises into the hotkey thread while start() still reports a dead mic (6 of the 7 verified to fail against the pre-fix module) |
| `test_model_catalog.py` | 10 | Catalog data, cache state (HF + Moonshine), disk usage, config sync, backend field |
| `test_model_manager.py` | 13 | Model swap, download progress/cancel, disk pre-check, delete guards (whisper + moonshine) |
| `test_controller.py` | 29 | DictationController state transitions, transcript durability (persist-before-inject), streaming session wiring (both prompts reach the session), **live-audio guard**, `can_start_recording()` agreeing with `start_recording()` in every state, **a silent complete session is not decoded twice** while an incomplete one is, **the focus guard** (unchanged focus types, moved focus holds to clipboard + `held` row + notification, unknown focus types, the policy table), and PROCESSING events carrying `progress` only while the user waits |
| `test_gestures.py` | 17 | **The hotkey → engine → glyph wiring.** Key down while READY shows the glyph *before* opening the mic; while LOADING/RECORDING/PROCESSING it touches neither; after ERROR it still arms; **a refused start retracts the glyph, closes the mic and resets the listener** (the 58-refusal regression); an accepted start leaves the armed glyph up; a start that raises is treated as refused and never escapes; state events route to listen/process/dismiss; no overlay is fine; a broken overlay warns once and never blocks the mic; plus two end-to-end runs against the real `DictationController` |
| `test_hotkey.py` | 4 | HotkeyListener tap-to-toggle, hotkey parsing |
| `test_hotkey_fn.py` | 18 | FnHotkeyListener CGEventTap behavior; **a disable notice during `stop()` is ignored while a real outage still reconciles; `start()` lowers the GIL switch interval**; **tap-outage recovery** — a release missed while the tap was disabled stops the clip, a still-held key keeps recording, an unreadable flag state changes nothing, PENDING disarms, and the disabled-event branch actually calls the resync |
| `test_injector.py` | 9 | Injection routing (auto/accessibility/clipboard) |
| `test_listener_factory.py` | 9 | Auto-fallback from Fn to pynput; **live upgrade back to Fn once Accessibility lands** — no build without the grant, retry when the tap is refused (Input Monitoring still off), no leaked listener when the swap raises, stop/start reusable |
| `test_settings.py` | 5 | Persistence, apply_to_config, first-run marker |
| `test_single_instance.py` | 3 | fcntl lock acquisition/release |
| `test_store.py` | 7 | SQLite CRUD, stats aggregation |
| `test_text_inject.py` | 14 | CGEvent + clipboard injection; **UTF-16 chunking** (units not code points, no split pair, lossless, the length handed to Quartz, a real Quartz round trip with emoji); **clipboard restore of every item and type**, string fallback, a failed restore never fails the paste |
| `test_transcriber.py` | 16 | Model loading, hallucination filter (incl. punctuation-normalized variants), swap, **below-real-time decodes warn** (fast ones do not, tiny clips never do) |
| `test_validation.py` | 5 | Config validation rules (incl. retention mode) |
| `test_build_signing.py` | 4 | **The build must never silently ad-hoc sign**: `Say.spec` treats an empty `SAY_SIGN_ID` as unset, both Makefiles export the *resolved* `$(SIGN_ID)`, and a real `make` run exports a non-empty identity |

CI: GitHub Actions on `macos-14` (arm64), Python 3.12, running `ruff check`, `mypy say` (a gate, not advisory) and `pytest say/tests`. `SAY_CI=1` skips tests needing Accessibility/Input Monitoring grants.

---

## Build Pipeline

### Python Engine (`say/Makefile`)

| Target | What it does |
|--------|-------------|
| `make all` | install → predownload → build → bundle-model. **Deliberately stops before `dmg`** — see the engine-DMG gotcha below |
| `make install` | pip install requirements.txt + pyinstaller + create-dmg-cli |
| `make predownload` | Download the bundled Moonshine model (via `moonshine_voice`) into `~/.cache/moonshine` |
| `make build` | PyInstaller via `Say.spec` → `dist/Say.app` (arm64, LSUIElement=true), signed at build with `CODESIGN_IDENTITY` (the stable local identity, ad-hoc only as fallback). Does NOT `cp` Info.plist (would break the seal — plist keys live in the spec). **Temporarily hides `moonshine_voice/libmoonshine.so`** (Linux ELF) during the build — PyInstaller's ctypes scan finds the `"libmoonshine.so"` literal and crashes Mach-O-parsing the ELF; restored (failure-safe) after. Verifies seal, fatal on failure. |
| `make bundle-model` | Copy the Moonshine `.ort` model into `Say.app/Contents/Resources/models/<leaf>/`, then re-seal the bundle (non-deep `codesign` with `SIGN_ID`) since adding to `Resources/` invalidates the seal. (.ort is plain data, not Mach-O, so non-deep re-sign is valid.) Prints the resulting designated requirement and warns if it is a bare cdhash. |
| `make dmg` | create-dmg → `SayEngine-v0.1.0-internal.dmg`, **no `--app-drop-link`**. A component image, not the product; users install the desktop DMG |
| `make signing-identity` | Idempotently mint the self-signed **`Say Local Signing`** cert into the login keychain (openssl → `pkcs12 -legacy` → `security import -A -T /usr/bin/codesign`). Needed so TCC grants survive rebuilds — see the gotcha above. `-legacy` is required: Security.framework can't read AES-encrypted PKCS12, and an empty password fails MAC verification. |
| `make sign` | Codesign with `$DEVELOPER_ID` (optional, exits cleanly if unset) |
| `make notarize` | Submit to Apple notary with `$APPLE_ID`, `$APPLE_TEAM_ID`, `$APPLE_APP_PASSWORD` |
| `make clean` | Remove build/ and dist/ |

Env vars: `SAY_PYTHON` (default `~/.venvs/say/bin/python`), `SAY_MODEL` (default `moonshine/medium-streaming-en`), `SAY_SIGN_ID` (default `Say Local Signing`; set to `-` to force ad-hoc).

### Desktop Shell (`desktop/Makefile`)

| Target | What it does |
|--------|-------------|
| `make all` | engine → ui-build → app → embed-engine → patch-plist |
| `make engine` | Builds Python engine via `make -C ../say all` |
| `make ui-build` | `npm run build` (Vite) |
| `make app` | `npm run tauri:build` |
| `make embed-engine` | **`ditto`** (NOT `rsync -aL`) the engine `Say.app` into shell's `Contents/Resources/SayEngine.app`, preserving framework symlinks + seal; verifies seal |
| `make patch-plist` | **Strips xattrs FIRST** (see below), adds usage strings to shell Info.plist, renames shell exec to "Say", **verifies both landed (fatal)**, signs SHELL only with `SIGN_ID` (engine already validly signed — re-signing it would break it), verifies both seals, then prints both designated requirements and warns if the engine's is a bare cdhash |
| `make dmg` | `mkdir -p` the bundle/dmg dir (Tauri only emits `bundle/macos`, and create-dmg silently writes to the CWD if its target dir is missing), then create-dmg → `Say_0.1.0_aarch64.dmg` |
| `make install` | Install the **shell** to `/Applications/Say.app`. Refuses any bundle whose id isn't `com.ritikyadav.say.desktop` or that has no embedded `SayEngine.app`; stages to `Say.app.staging`, verifies both seals, `lsregister -u`s the outgoing copy, then swaps — so a bad copy never replaces a working install |
| `make clean` | Remove node_modules, dist, src-tauri/target, src-tauri/gen |

**Two builds at once corrupt each other — the PyInstaller bincache is global, not per-worktree.**
`~/Library/Application Support/pyinstaller/bincache*` is shared by every checkout *and every
`.claude/worktrees/*` copy*, and PyInstaller neither locks it nor writes its index atomically. Two
concurrent `make build`s (easy to cause: one agent per worktree) produce failures that look like
anything but a race — `codesign … : No such file or directory` on a binary that was there a moment
ago, or `IndentationError: unexpected indent` from `load_py_data_struct` `eval()`ing a half-written
index. Deleting the cache "to fix it" only widens the window for the other build. Set
**`PYINSTALLER_CONFIG_DIR`** to a private directory when a build might overlap another one; that is
the only supported override and it isolates the whole cache. Check `pgrep -f PyInstaller` before
blaming the spec.

### PyInstaller Spec (`say/Say.spec`)

Target arch: arm64. Bundle ID: `com.ritikyadav.say.dictation`. App/binary name: `Say` (`name='Say'` in `Say.spec`; spec filename unchanged). `codesign_identity=CODESIGN_IDENTITY` so PyInstaller signs at build (required for a valid, TCC-honored seal — post-hoc signing can't seal the data-in-Frameworks layout). `_codesign_identity()` resolves `SAY_SIGN_ID` (default `Say Local Signing`) and falls back to `-` when it isn't in the keychain. `info_plist` in `BUNDLE()` carries the authoritative usage strings (branded "Say") — do not overwrite the bundle's Info.plist post-build. LSUIElement: true (no Dock icon). Hidden imports include: mlx.*, tiktoken_ext.*, moonshine_voice.*, rumps, pynput._darwin, fastapi, uvicorn, sse_starlette, requests, filelock, server.*. Collects data from mlx_whisper + tiktoken_ext + moonshine_voice, plus `say/assets/` (the brand mark — without it the shipped app has no logo), binaries from mlx (Metal libs). `icon=assets/Say.icns` sets the bundle icon; PyInstaller writes it *before* signing, so it does not break the seal. **Moonshine dylibs** (`libmoonshine.dylib` + `libonnxruntime*.dylib`) are added explicitly (not via `collect_dynamic_libs`, which also grabs the Linux `.so`) into the bundled `moonshine_voice/` dir so the package-relative `ctypes` loader (`Path(__file__).parent / lib`) resolves and PyInstaller signs them.

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
