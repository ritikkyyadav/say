# Production Readiness — Say

Living status of how close Say is to a product people pay for, and the plan
for the thirteen defects the September 2026 audit ranked. The audit itself
(evidence, the overlay root cause, the model landscape, the monetisation
roadmap) is published separately; this file is the part that gets executed.

Last full audit: **2026-09-05**. Suite at that point: 32 modules, 520 tests.

## Where it stands

- **Usable daily on the author's Mac:** yes.
- **Fixed by the audit:** the overlay vanishing mid-dictation (two measured
  root causes; see the two gotchas in `CLAUDE.md` and `say/gestures.py`).
- **Blocking a paid release:** B1, B2, B4 and B5 below, then the signing and
  notarisation gates at the bottom of this file.
- **Not yet verified by automation:** the core loop on a clean macOS account
  (Mic + Accessibility + Input Monitoring grants need a human), a real model
  download over the network, the Tauri app launching outside the dev machine.

## The thirteen — status board

| # | Severity | Problem | Where | Status |
|---|----------|---------|-------|--------|
| B4 | High | Tests write into the user's real metrics ledger | `say/tests/conftest.py` (new) | planned |
| B11 | Medium | Notifications toggle needs a restart | `say/notifications.py` | planned |
| B1 | Critical | Emoji and other astral characters corrupt typed text | `say/text_inject_cgevent.py` | planned |
| B7 | High | A fast press-and-release can start a recording nobody stops | `say/hotkey_fn.py`, `say/hotkey.py` | planned |
| B10 | Medium | Silent clips are decoded twice | `say/controller.py`, `say/streaming.py` | planned |
| B3 | High | A short streaming tail gets the full prompt and loops | `say/streaming.py`, `say/controller.py` | planned |
| B9 | Medium | Clipboard fallback destroys a non-text clipboard | `say/text_inject_clipboard.py` | planned |
| B2 | Critical | Text is typed into whichever app has focus when transcription finishes | `say/controller.py` | planned |
| B5 | High | Multilingual models offered, language hard-wired to English | `say/languages.py` (new), `server/api.py`, `desktop/src/routes/Models.tsx` | planned |
| B8 | Medium | Slow inference is invisible: no progress, no warning | `say/streaming.py`, `say/controller.py`, `say/transcriber.py`, desktop SSE | planned |
| B12 | Medium | Kept audio grows without limit; retention has no UI | `say/config.py`, `desktop/src/routes/Home.tsx` | planned |
| B6 | High | The key tap shares a process with inference; macOS disables it under load | `say/hotkey_fn.py`, `say/app.py`; helper process designed | planned |
| B13 | Medium | ruff and mypy are red; mypy is advisory in CI | whole tree, `.github/workflows/ci.yml` | planned |

Order is execution order: the cheapest data-protecting fixes first (B4 makes
every later test run safe), then the ones with the sharpest evidence, then the
ones that touch the UI, then the tooling gate that keeps it all green.

## Working rules

1. **One problem, one commit, its tests in the same commit.** A reviewer can
   revert any single item without touching the others.
2. **The evidence that ranked the item is the acceptance test.** The engine log
   (`~/Library/Logs/Say/say.log`), the metrics ledger and the stored corpus
   decide, not opinion. Where a change touches decoding behaviour, replay the
   stored clips before calling it done — the repo has never tuned any other way.
3. **Nothing widens.** Fix the named defect and leave the neighbours alone; a
   surprising refactor in a fix commit is a bug in the plan.
4. **Every commit leaves ruff and the suite green; from B13 on, mypy too.**
5. **`CLAUDE.md` is updated in the same commit** when a file, constant,
   endpoint or gotcha changes — the self-improvement protocol is not optional.

---

## B4 — Tests write into the user's real metrics ledger

**Problem.** `metrics.get_ledger()` is a module singleton bound to
`~/Library/Logs/Say/metrics.jsonl`. `test_controller.py` redirects the
transcript store per test but not the ledger, so every injection-failure test
appends a real row. The ledger holds 288 rows of `stage: injection,
message: "boom"` — first on 21 August, the newest from the audit's own test
runs. Any error-rate metric built on the file counts 288 phantom failures.

**Surgical change.**
- New `say/tests/conftest.py` with one session-scoped, autouse fixture that
  points every user-data path at a temporary directory before any test runs:
  `metrics.LEDGER_PATH` (and resets `metrics._ledger`), `settings.SETTINGS_DIR`
  / `SETTINGS_FILE` / `FIRST_RUN_MARKER`, the transcript store singleton via
  `server.store.reset_store_for_tests`, and the model caches
  (`HF_HUB_CACHE`, `SAY_MOONSHINE_CACHE`). Per-test fixtures that already do
  this keep working; the session fixture is the floor beneath them.
- A one-off prune of the real ledger: back it up as `metrics.jsonl.bak`, drop
  rows whose `message` is exactly `"boom"`, keep everything else byte-for-byte.

**Tests.** `test_conftest_isolation.py`: the ledger path, the settings file
and the store path all live under the temp root during the run.

**Verification.** Row count of the real ledger before and after a full
`pytest say/tests` run is identical.

**Risk.** Low. The fixture only redirects paths; nothing in the engine reads
them at import time except `fn_override.BACKUP_PATH` (derived from
`SETTINGS_DIR` at import), which no test exercises against disk.

## B11 — Notifications toggle needs a restart

**Problem.** `notifications.py` does `from config import SHOW_NOTIFICATIONS`,
copying the value at import. `settings.set_value` mirrors onto the `config`
module, which this module never re-reads.

**Surgical change.** `import config` and read `config.SHOW_NOTIFICATIONS` at
call time. Two lines.

**Tests.** `test_notifications.py`: with the flag False, `subprocess.run` is
never called; flipping it True at runtime makes the next call go through.

## B1 — Emoji and other astral characters corrupt typed text

**Problem.** `text_inject_cgevent.py` chunks the transcript by Python code
points (`text[i:i+20]`) and passes `len(chunk)` to
`CGEventKeyboardSetUnicodeString`, which takes a UTF-16 unit count. A chunk
holding N characters outside the Basic Multilingual Plane (emoji, some CJK
extensions) is 20+N units long, so its last N units are silently dropped, and
a pair can be torn in half. Verified by round trip: `"hello world 😀 test!"`
came back without its final `!`, and five emoji produced an undecodable
surrogate.

**Surgical change.**
- `_chunks(text) -> list[str]`: walk the string accumulating UTF-16 units
  (`2` for `ord(ch) >= 0x10000`, else `1`), close a chunk before it would
  exceed `_CHUNK_LEN` units, so a pair is never split.
- `_utf16_len(chunk)` passed as the length argument, for both key-down and
  key-up events.
- `_CHUNK_LEN` keeps its meaning ("UTF-16 units per event"), which is what
  the comment and `CLAUDE.md` already claimed.

**Tests.** `test_text_inject.py`: every chunk is at most 20 units and never
starts with a low surrogate; the length handed to Quartz equals the chunk's
UTF-16 length; the existing ASCII chunking test still holds; and a real
Quartz round trip (`CGEventKeyboardGetUnicodeString`) returns the emoji text
intact — skipped where Quartz is absent, so it runs on every Mac and never in
the Linux job.

**Verification.** The round trip that failed in the audit passes.

## B7 — A fast press-and-release can start a recording nobody stops

**Problem.** `FnHotkeyListener._fire` (and `HotkeyListener._fire`) starts a
fresh thread per callback. A push-to-talk hold released a few milliseconds
after the 250 ms threshold fires `on_start` and `on_stop` on two threads with
no ordering. If stop wins the controller has nothing to stop; start then
runs, and the recording continues until the 15-minute cap or the next
gesture.

**Surgical change.** Both listeners get one daemon worker fed by a
`queue.Queue`; `_fire(cb)` enqueues, the worker runs callbacks in order and
logs (never raises) exceptions. The worker starts lazily on the first
`_fire` so tests that never call `start()` keep working; `stop()` enqueues a
sentinel and joins briefly. Ordering is the only semantic change: a callback
that blocks (a wedged mic open, at most `_OPEN_TIMEOUT`) delays the ones
behind it, which is exactly the order a human produced them in.

**Tests.** `test_hotkey_fn.py` and `test_hotkey.py`: a start callback that
sleeps 50 ms followed immediately by a stop completes in `[start, stop]`
order; an exception in one callback does not stop later callbacks; `stop()`
ends the worker.

## B10 — Silent clips are decoded twice

**Problem.** When every streaming segment legitimately returns empty text
(a silent recording), `controller._transcribe` sees no text and re-decodes
the whole clip. "Streaming result unusable (text=False complete=True);
re-decoding the whole clip" appears 22 times in the log; each is a wait
doubled for a clip that contained nothing.

**Surgical change.** `SegmentedSession` counts decode *attempts*
(`attempted`). The controller trusts an empty result when the session is
`complete` and `attempted >= 1`; it re-decodes only when a segment failed
or nothing was ever attempted (a tail shorter than `MIN_AUDIO_SECONDS` with
no closed segment, where the whole clip is also below the minimum anyway).

**Tests.** `test_controller.py`: an all-empty, complete session returns
without a second `transcribe`; an incomplete session still re-decodes.

## B3 — A short streaming tail gets the full prompt and loops

**Problem.** Whole-clip mode drops the two-sentence style seed for clips
under `_STYLE_SEED_MIN_SECONDS` (2 s) because a strong text prior with almost
no audio drives the decoder into a repetition loop. The streaming session
bypasses that guard: every segment, including the tail, is decoded with the
seed plus up to 200 characters of carry-over text. On 5 September a 0.4 s tail
took 19.2 s to decode and produced one word repeated 663 times (1,413 words
collapsed to 534). The log holds 62 degenerate transcripts of this shape.

**Surgical change.**
- `SegmentedSession(transcriber, base_prompt=, short_prompt=)`: the
  controller passes `postprocess.asr_prompt()` and
  `postprocess.asr_prompt(include_style_seed=False)`.
- `_transcribe(segment)`: a segment shorter than `SHORT_SEGMENT_SECONDS`
  (2.0, mirroring `transcriber._STYLE_SEED_MIN_SECONDS`) is decoded with the
  short prompt and **no carry-over**; everything else is unchanged. Only the
  tail can ever be that short (mid segments are at least 8 s), so the change
  cannot affect a segment that carries real sentence context.

**Tests.** `test_streaming.py`: a short tail is decoded with the short prompt
and no carry-over; a long tail still gets the carry-over; a controller-level
test that the two prompts reach the session.

**Verification.** Unit tests now; `replay_bench.py --sample 24` on the stored
corpus before the next release to confirm no density regression.

## B9 — Clipboard fallback destroys a non-text clipboard

**Problem.** `text_inject_clipboard.py` saves only `NSPasteboardTypeString`
before pasting. If the clipboard held an image, a file or rich text, it is
replaced by the transcript and never restored.

**Surgical change.** Snapshot `pb.pasteboardItems()` as
`[{type: data}]` for every type on every item; after the paste settles,
`clearContents()` and `writeObjects_` a fresh `NSPasteboardItem` per
snapshot. If the snapshot fails or is empty, fall back to the existing
string-only path; if the restore fails, log and move on — the paste already
landed.

**Tests.** `test_text_inject.py` with a fake AppKit: two items with three
types are restored as two objects with all three types; a string-only
clipboard still restores; a snapshot that raises does not stop the paste.

## B2 — Text is typed into whichever app has focus when transcription finishes

**Problem.** `get_frontmost_app()` is read after transcription and the
keystrokes go to whatever is focused at that instant. With Whisper large-v3
the wait is 2 to 44 seconds (p90 25.7 s on the ledger), long enough to switch
to Slack or a browser. The transcript lands in the wrong window, the row
records the wrong app, and the read-back measures the wrong document.

**Surgical change.**
- `start_recording()` captures `self._target_app = get_frontmost_app()`
  under the lock. That app is what the row records: it is where the user
  meant the words to go.
- `stop_recording()` compares it with the frontmost app at injection time
  through one small policy function, `focus_policy(started_in, now_in)`,
  returning `'inject'` or `'hold'`. Default: hold when both are known and
  differ; inject when either is unknown (no AppKit, no bundle id).
- `'hold'`: no keystrokes; transcript to the clipboard via the existing
  `_copy_to_clipboard`; `store.set_outcome(id, success=False,
  inject_method='held')`; READY event and notification "Focus moved to
  <app> — transcript copied to the clipboard". No schema change:
  `inject_method` already carries free text.

**Tests.** `test_controller.py`: unchanged focus injects; moved focus holds,
copies, records `held`, does not arm the read-back; unknown focus injects;
`focus_policy` is a pure function with its own table test.

**Risk.** The one behavioural change a user can notice: dictating into app A
and switching to B before the text lands now leaves the text on the
clipboard. That is the point.

## B5 — Multilingual models offered, language hard-wired to English

**Problem.** `config.LANGUAGE = 'en'` is passed to every Whisper call and no
desktop screen changes it, while the Models page promises "99 languages,
switch freely". Whisper forced to English on Hindi speech translates or
transliterates. The bundled Moonshine model ignores the setting entirely.

**Surgical change.**
- New `say/languages.py`: a curated `LANGUAGES` list of Whisper codes and
  names (the two dozen a dictation user is likely to pick, Hindi included),
  `is_valid(code)` for any two- or three-letter ISO code, and `AUTO = None`.
- `GET /language` → `{ code, auto, options, applies_to_active_model }`;
  `POST /language` body `{ code: string | null }` (null = automatic) →
  validates, `settings.set_value('LANGUAGE', …)`, returns the same shape.
  `LANGUAGE` is already in `settings.PERSISTABLE_KEYS` and
  `transcriber._transcribe_whisper` already omits the argument when it is
  falsy, so auto-detect needs no engine change.
- Desktop: `api.language()` / `api.setLanguage()`; on the Models page a
  "Language" select (Automatic first) with a one-line note when the active
  model ignores it. The "I don't only speak English" intent shows the hint
  "set Language to Automatic to switch mid-sentence" rather than changing the
  setting silently.

**Tests.** `test_api.py`: get, set, null for auto, bad code refused, persisted
into settings and mirrored onto `config`. `test_languages.py`: validation.

**Verification.** `tsc` and `vite build` green; a Whisper call with
`LANGUAGE=None` passes no `language` kwarg (asserted through a fake).

## B8 — Slow inference is invisible

**Problem.** Ledger p90 inference for large-v3 is 25.7 s; the log shows
35.7 s, 43.2 s and 41.3 s decodes on 5 September with 27 segments queued.
`finish()` waits up to 180 s. The window shows "processing" with no progress
and the log has no warning that decoding fell below real time.

**Surgical change.**
- `SegmentedSession(on_progress=)` reports `(done, queued)` after every
  segment; the controller forwards it as `progress` on PROCESSING events
  (`DictationEvent.progress`, passed through the bus untouched).
- `transcriber` logs a warning whenever a decode's real-time factor exceeds
  1.0 — the tell for thermal throttling or memory pressure.
- Desktop: `DictationStateEvent.progress`; `useEngineState` exposes it and
  the Rail's mark carries it as a tooltip and accessible label. The visible
  treatment is a design decision for the next pass; the data is there now.

**Tests.** `test_streaming.py` progress callback counts; `test_controller.py`
PROCESSING events carry progress; `test_transcriber.py` warning at RTF > 1.

## B12 — Kept audio grows without limit

**Problem.** 406 clips, 587 MB after three weeks with "Keep audio" on. The
engine supports day and size caps, but the tab that exposed them is gone and
the Home switch only toggles saving.

**Surgical change.**
- `config.RECORDINGS_RETENTION_MODE = 'size'` (the 2 GiB
  `RECORDINGS_MAX_BYTES` already exists). Existing users with a persisted
  mode keep it; everyone else gets the cap.
- Home: next to the switch, a small select — "Keep newest 500 MB / 2 GB /
  5 GB / Keep everything" — writing `retention_mode` + `max_bytes` through
  the existing `POST /recordings/config`.

**Tests.** `test_validation.py` / `test_api.py` defaults updated; a retention
test that the default prunes to the budget.

## B6 — The key tap shares a process with inference

**Problem.** The CGEventTap callback runs in the Python engine. When the
interpreter lock is held by the decoder, macOS disables the tap
(`kCGEventTapDisabledByTimeout`, 8 in the log) and every key press until the
re-enable is lost; the resync recovers a missed release, not a missed press.
A further 56 disable notices at shutdown are logged as warnings and drown the
real ones.

**Surgical change now.**
- `stop()` sets `_stopping` first; the callback returns the event without
  the warning or the resync while it is set.
- `sys.setswitchinterval(0.001)` at engine start (both entry points): the
  interpreter hands the lock over five times as often, which is what lets
  the tap thread run between the decoder's Python steps. Measurable, cheap,
  reversible.

**Design for the real fix (next milestone).** A helper executable,
`SayHotkey`, owns the tap and nothing else: ~150 lines of Swift, spawned by
the engine, emitting one JSON line per event (`fn_down`, `fn_up`, `space`,
`escape`) on stdout and consuming `swallow`/`pass` decisions on stdin.
`FnHotkeyListener` becomes a client of that pipe with the same state
machine. The helper needs its own Input Monitoring grant, so it ships inside
`SayEngine.app` with the engine's bundle identity and is signed with the same
identity; the Makefile gains a `hotkey-helper` target and `Say.spec` bundles
the binary. Test plan: the state machine tests stay as they are; a new
integration test drives the helper with synthetic events.

**Tests.** `test_hotkey_fn.py`: no outage warning during `stop()`.

## B13 — ruff and mypy are red

**Problem.** ruff: 10 errors, all import ordering. mypy: 35 errors in 17
files — `Optional` runtime handles typed as `None`, missing set annotations
in `validation.py`, unused `type: ignore` comments, `store.list` shadowing
`list` in the class body's annotations, callbacks typed as returning `None`
where `bool` is returned. CI runs mypy with `|| true`.

**Surgical change.** `ruff check --fix`; annotate the handles as `Any`;
type the callback aliases as `Callable[[], object]`; alias `list` at module
top in `store.py`; delete stale ignores; annotate the tool scripts' dicts.
Then drop `|| true` from the CI step so it gates.

**Tests.** ruff and mypy exit 0 locally; CI runs both as gates.

---

## Release gates that still need a human

| Gate | Why it needs you | Where |
|------|------------------|-------|
| Code signing | An Apple Developer ID certificate | `make sign` (`DEVELOPER_ID`) |
| Notarisation | `APPLE_ID` / `APPLE_TEAM_ID` / `APPLE_APP_PASSWORD` | `make notarize` |
| Gatekeeper | An unsigned `.dmg` is blocked on other Macs | follows from the two above |
| Permission grants | TCC prompts cannot be scripted | manual, first launch |
| Clean-account smoke | Mic + Accessibility + Input Monitoring on a fresh macOS user | see below |

### The manual smoke

```bash
python say/app.py --server          # engine
cd desktop && npm run tauri:dev     # window
# Models (Cmd+3): download a model, "Use this one".
# Focus a text field, hold Fn, speak, release: text appears.
# Press Fn again while the last one is still typing: the sweep stays, the mic
# indicator stays dark, nothing is lost.
```
