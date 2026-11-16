# Say for Android

The same dictation engine as the Mac, as an Android keyboard. Tap the mic key on
your keyboard (or pick Say in the keyboard switcher), speak, tap the mark. The
words land in the field you were in and your keyboard comes back. Everything
runs on the phone: the model is inside the APK, and nothing is ever uploaded.

## How it fits

| Mac | Android |
|---|---|
| Hold Fn, speak, release | Mic key → speak → tap the mark (or the tick) |
| CGEvent keystroke injection | `InputConnection.commitText()` from an input method |
| Floating overlay glyph | The same glyph, in the keyboard panel, same audio→motion engine |
| `moonshine_voice` (Python, 0.0.62) | `ai.moonshine:moonshine-voice` (the official AAR, 0.1.5) |
| `medium-streaming-en` bundled | `tiny-streaming-en` bundled; Small / Medium downloadable |
| Whisper models | not offered — MLX is Apple-only |
| `postprocess.py`, `streaming.py`, `waveform.py` | ported line for line; their test suites run on the JVM |
| `focus_policy()` | same rule: text is committed only if the field it was dictated into still has focus, else it is held on the clipboard |

Package `com.ritikyadav.say` (application id `com.ritikyadav.say.android`, the
same namespace as the Mac bundles). One process holds the engine, the keyboard
service and the Home screen — no loopback HTTP needed.

## Build

```bash
brew install --cask android-commandlinetools openjdk@17   # once
make models        # 51 MB bundled model into app/src/main/assets (gitignored)
make test          # 124 JVM tests: the ported engine modules
make build         # debug APK
make signing-key && make release   # release APK (64 MB) with a stable local key

`make release-small` builds the same app with the model left out — 29 MiB instead of 61, fetched
once on first launch — for a phone whose package installer will not take the big one, or any
transfer with a size limit.

`make release` mints a strictly increasing `versionCode` for every build and checks its own
signature, because both mistakes — a repeated version code, or a build signed with the debug
key — surface on the phone as the same wordless "App not installed." Pin a code for a real
release with `make release SAY_VERSION_CODE=42`.
```

## Run it

```bash
make avd && make emulator          # Android 16 arm64, headless
make install                       # installs, grants the mic, enables the keyboard
```

Then open Say and tap **Dictate** — it records into the field on that screen with no
keyboard switching, and the status line says what the engine is doing. To use Say in
other apps: **Switch keyboard** (or the globe key on Gboard, or the keyboard icon at the
bottom of the screen) and pick Say; speak; tap the mark; your keyboard comes back.

To push a real dictation through the whole pipeline on the device (debug builds
only — the Android `replay_bench`):

```bash
make replay CLIP=~/Library/Application\ Support/Say/recordings/399.wav
```

## Say anywhere: the floating mark

Turn on **Say** under *Settings → Accessibility* (the Home screen has a button). From then
on a small floating mark appears just above the keyboard in every app. Tap it, speak, tap
it again: the words land at your cursor, whichever keyboard is up. Hold it to cancel, drag
it to move it. If no text field has focus the words go to the clipboard instead.

This is the Android equivalent of the Mac's overlay + keystroke injection, and it is why
the accessibility permission is asked for: it is the only way an app can type into another
app on Android. Say reads only the focused field, only at the moment a transcript lands in
it, and nothing else on screen.

## Layout

```
app/src/main/java/com/ritikyadav/say/
  SayApp.kt              process singletons
  Settings.kt            user overrides (SharedPreferences), read at call time
  engine/
    Config.kt            shipped defaults
    Postprocess.kt       repetition collapse, vocabulary repair, formatting
    Streaming.kt         SilenceSegmenter + SegmentedSession
    Hallucinations.kt    the silence-hallucination filter
    Recorder.kt          AudioRecord capture, 64 ms blocks, segment callbacks
    MoonshineTranscriber.kt   the model; setKeyterms() carries the dictionary into the decoder
    ModelCatalog.kt / ModelManager.kt   the three models, copy-from-assets, resumable downloads
    DictationController.kt    IDLE → LOADING → READY → RECORDING → PROCESSING → READY
    EndOfSpeech.kt       when a dictation ends on its own (see the note inside)
  glyph/
    Waveform.kt          BandAnalyzer, BarMotion, silhouette — the port
    GlyphView.kt         draws it, Choreographer-clocked
  ime/SayInputMethodService.kt   the keyboard
  store/TranscriptStore.kt       SQLite, the Mac's schema
  ui/HomeActivity.kt, MicPermissionActivity.kt
  overlay/SayAccessibilityService.kt   the floating mark's host + typing into the focused field
  overlay/Bubble.kt, TextSplice.kt     the mark's window and gestures; where the words go
app/src/debug/…/ReplayReceiver.kt, WavFileSource.kt   the replay hook
```
