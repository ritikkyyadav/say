# config.py - Say Configuration
# ============================================================
# On-device voice dictation for macOS
# All user-facing settings live here. No hardcoded values elsewhere.
# ============================================================

# -- Model -----------------------------------------------------
# The default ships bundled inside the app so first launch is fully offline.
# Moonshine Medium (ONNX, English, ~450 MB) is the small, ultra-fast default;
# the larger multilingual Whisper models are optional downloads from the
# Models tab. Each catalog entry declares a `backend` (moonshine | whisper-mlx)
# so the transcriber dispatches to the right runtime.
#   'moonshine/medium-streaming-en'            -> bundled default (English, ONNX)
#   'mlx-community/whisper-large-v3-turbo'      -> multilingual daily-driver (download)
#   'mlx-community/whisper-large-v3-mlx'        -> highest accuracy (download)
#   'mlx-community/whisper-small-mlx'           -> low storage / older hardware (download)
MODEL = 'moonshine/medium-streaming-en'

VALID_MODELS = {
    'moonshine/medium-streaming-en',
    'mlx-community/whisper-tiny-mlx',
    'mlx-community/whisper-base-mlx',
    'mlx-community/whisper-small-mlx',
    'mlx-community/whisper-medium-mlx',
    'mlx-community/whisper-large-v3-mlx',
    'mlx-community/whisper-large-v3-turbo',
}

# -- Hotkey ----------------------------------------------------
# Global push-to-talk trigger key.
#
# Two listeners are available:
#   - Fn (Quartz CGEventTap):  Hold Fn → PTT,  Fn+Space → hands-free
#   - Legacy (pynput):         Hold Ctrl+Space → PTT (no hands-free)
#
# Fn is the default trigger (Say's signature interaction); while running, the
# engine neutralizes the macOS globe-key action via AppleFnUsageType. On some
# macOS versions that suppression is imperfect, so `listener_factory` auto-falls
# back to the legacy ctrl+space hotkey whenever the Fn CGEventTap can't start
# (e.g. Input Monitoring not granted). Set this True to force the legacy hotkey
# from the start and skip the Fn path entirely.
PREFER_LEGACY_HOTKEY = False

HOTKEY = 'fn'
HOTKEY_LEGACY = 'ctrl+space'  # Used by the pynput listener

# Temporarily set NSGlobalDomain `AppleFnUsageType` to "Do Nothing"
# for the duration of Say's run, restoring the prior value on quit.
# Only takes effect when the Fn listener is actually in use; with the
# legacy ctrl+space hotkey the override is a no-op. Set False to
# leave the user default untouched even when running on Fn.
OVERRIDE_FN_KEY_ACTION = True

# -- Audio -----------------------------------------------------
SAMPLE_RATE = 16000            # Whisper expects 16 kHz mono audio
# Safety cap — the recording auto-stops and transcribes once it reaches this
# length, so a forgotten hands-free session can't grow unbounded. Generous by
# default (15 min) so normal long-form dictation is never clipped mid-sentence;
# raise it (user-overridable via settings) if you routinely dictate longer.
MAX_RECORDING_SECONDS = 900
# Shorter clips are ignored. 0.4 until Oct 2026: of the 20 stored dictations
# under one second, 19 were decoder inventions ("Monica Monica.", "CALD C.")
# and one was a single word. A tap that short is an accident, not a dictation.
MIN_AUDIO_SECONDS = 1.0

# Pre-roll: amount of audio captured *before* the hotkey-down moment that
# is prepended to the recording. Eliminates first-syllable clipping
# caused by hotkey debounce + reaction time.
#
# When > 0, the hotkey listener calls `recorder.arm()` the instant the trigger
# key goes down, which opens the mic and starts filling the ring buffer *before*
# the tap-vs-hold window resolves. The mic stays closed while idle, so the macOS
# mic-active LED remains an honest indicator. No audio is written to disk and the
# buffer is in-memory only. Set to 0 to disable pre-roll (and arming).
PRE_ROLL_SECONDS = 0.5

# Polling interval for the input-device-change watcher (e.g. AirPods
# unplugged mid-recording). 0 disables the watcher.
DEVICE_WATCH_INTERVAL_S = 2.0

# -- Language --------------------------------------------------
# Set to a language code (e.g., 'en', 'es', 'fr', 'hi') for forced language.
# Set to None for auto-detect (slightly slower, but handles multilingual input).
LANGUAGE = 'en'

# What the person dictates in — the question the Models page is built around.
# One of languages.FOCUS_IDS: 'english' (fixed to 'en', the built-in model),
# 'european', 'south_asian' or 'any' (automatic detection, a Whisper model).
# Choosing a focus sets LANGUAGE to the focus's default; the two are persisted
# separately so a specific language can still be pinned inside a focus.
LANGUAGE_FOCUS = 'english'

# -- Text Injection -------------------------------------------
# How transcribed text is inserted into the active app.
#   'auto'           -> Try Accessibility first, fall back to clipboard paste
#   'accessibility'  -> Direct keystroke injection (requires Accessibility permission)
#   'clipboard'      -> Copy, Cmd+V paste (works in most apps)
INJECT_METHOD = 'auto'
VALID_INJECT_METHODS = {'auto', 'accessibility', 'clipboard'}

# -- Custom Vocabulary / Prompt -------------------------------
# Extra text prepended to the decoder's context. Combined with the built-in
# style seed and DICTIONARY terms by `postprocess.asr_prompt()`; this constant
# holds only what the *user* added. Keep the total under ~100 tokens.
INITIAL_PROMPT = ''

# -- Transcript auto-correction -------------------------------
# Deterministic clean-up applied between transcription and injection
# (see postprocess.py). Rule-based and effectively free — no model runs.
AUTO_CORRECT = True                 # master switch for the whole stage
AUTO_CAPITALISE = True              # sentence-start capitals, standalone "I"
AUTO_TERMINAL_PUNCTUATION = True    # add a full stop when the last sentence lacks one

# Vocabulary the acoustic model tends to mishear. Whisper receives these as
# decoder bias before transcription; every backend gets near-miss repair after
# transcription ("cloud code" -> "Claude Code"). Moonshine's current runtime
# has no decoder-prompt API. Multi-word entries are safer to fuzzy-match than
# single words — prefer them.
DICTIONARY: list[str] = []

# Literal, whole-word substitutions applied before fuzzy matching. Use this
# when a term is mangled in a way phonetic matching won't catch.
DICTIONARY_REPLACEMENTS: dict[str, str] = {}

# -- Learning from corrections --------------------------------
# Every layer between the microphone and the document is a bet about what you
# meant. This is the scorecard: a few seconds after Say types, it reads the
# field back and diffs it against what it inserted, so "how often did the user
# still have to fix it" becomes a measured number instead of a guess.
#
# It rides the Accessibility grant injection already requires — nothing extra
# is requested — and it only ever looks at the element Say just wrote into,
# inside a window it opened itself. Only Say's own span and what that span
# became are stored; the surrounding document is used to locate the span and
# is then dropped. See readback.py for the full contract.
LEARN_FROM_CORRECTIONS = True

# How long to wait before reading back. Long enough for a fix to have happened,
# short enough that the field usually still exists.
CORRECTION_READBACK_DELAY_S = 8.0

# -- Streaming transcription ----------------------------------
# Decode the recording in segments *while* the user is still speaking, cutting
# only inside natural pauses, so at hotkey-release just the final segment is
# outstanding. Without it the wait scales with how long you talked (measured
# p90: 17.7 s); with it the wait is roughly constant regardless of length.
#
# Segments carry the tail of the previous segment's text into the next one's
# prompt (see streaming.carry_over_prompt) — separate decoder calls would
# otherwise lose the cross-window context that produces sentence structure.
#
# Falls back to whole-clip transcription automatically if anything goes wrong.
STREAMING_TRANSCRIPTION = True

# -- Decoder behaviour (Whisper backends) ---------------------
# Whisper transcribes in 30-second windows and, with conditioning ON, feeds
# each window's output into the next as context. That propagates *style* as
# well as terminology — which is why an unpunctuated first window used to make
# the whole dictation unpunctuated.
#
# The fix is NOT to disable conditioning. Measured on a 24-clip sample of real
# dictations (say/tools/replay_bench.py):
#
#   config        unpunctuated   sentences/100w   (prose is 5-7)
#   baseline           58.3%          0.85
#   seed only           0.0%          5.75   <- shipped
#   nocond only        45.8%          1.23
#   seed + nocond       0.0%          2.44
#
# Conditioning was never the problem; the *absence of a style seed* was. Given
# a well-punctuated seed, conditioning faithfully propagates the good style and
# produces normal sentence structure. Disabling it also throws away the
# cross-window context, which more than halves sentence density.
STYLE_SEED_ENABLED = True
CONDITION_ON_PREVIOUS_TEXT = True

# -- Overlay ---------------------------------------------------
# The floating dictation glyph: a small mark at the bottom of the screen that
# appears the instant the trigger key goes down and draws the live spectrum of
# what the microphone is hearing (see overlay.py / waveform.py).
#
# It is feedback only — it never takes focus, never accepts a click, and is
# built once at launch so showing it costs nothing. Set False to dictate with
# no on-screen indicator at all.
SHOW_OVERLAY = True

# How the glyph keeps itself legible. The system appearance is a poor proxy for
# what is actually behind the overlay — macOS in dark mode over a white web
# page puts near-white ink on white — so by default the mark is drawn with a
# thin rim in the opposite tone, the way subtitles are.
#   'auto'    ink follows the system appearance, plus the contrast rim
#   'system'  ink follows the system appearance, no rim
#   'dark'    always dark ink with a light rim
#   'light'   always light ink with a dark rim
OVERLAY_CONTRAST = 'auto'
VALID_OVERLAY_CONTRAST = {'auto', 'system', 'dark', 'light'}

# The glyph's colours. A theme is a short list of colours cycled across the
# twelve columns, so the mark can read as alternating slashes instead of one
# solid silhouette. See `overlay_theme.py` for the catalogue.
#
# 'ink' is deliberately colourless: it means "follow the system appearance",
# i.e. exactly the behaviour that shipped before themes existed, and it stays
# the default so nobody's glyph changes underneath them. Any other preset, or
# 'custom' with OVERLAY_CUSTOM_COLORS, paints the columns explicitly and
# OVERLAY_CONTRAST no longer applies — a chosen colour is not adapted away.
OVERLAY_THEME = 'ink'
OVERLAY_CUSTOM_COLORS: list[str] = []

# How those colours are distributed across the twelve columns. 'alternate' is
# the plain stripe (column 0 colour A, column 1 colour B, …), which is a fixed
# pattern that never changes; every other option derives the colour from the
# live audio instead, so the row stays tightly synchronised — it is all one
# spectrum — while never repeating. See overlay_theme.column_colors.
#   'pulse'      colour follows each bar's own loudness  (default)
#   'spark'      only the loudest bars take the second colour
#   'flow'       a colour wave travelling the row
#   'drift'      a fixed scatter that never lines up
#   'alternate'  every other column
OVERLAY_PATTERN = 'pulse'

# -- Who you are ----------------------------------------------
# What the app calls you, and whether the desktop onboarding has been
# completed. Both live in the local settings file and are read by nothing but
# this machine — there is no account, no telemetry and no network call
# anywhere in the app. The one email this product asks for is collected on the
# website at download time and never reaches the binary.
USER_NAME = ''                 # blank = fall back to the OS account name
ONBOARDED = False              # desktop first-run flow completed

# -- Notifications --------------------------------------------
SHOW_NOTIFICATIONS = True      # Show macOS notification on transcription complete

# -- Menu Bar --------------------------------------------------
MENU_BAR_ICON = '🎙'           # Emoji shown in the macOS menu bar

# -- Recordings (audio receipts) -------------------------------
# When True, the raw audio of every successful dictation is saved as a WAV
# under ~/Library/Application Support/Say/recordings/ and surfaced in the
# Recordings tab so you can replay/verify/export what you said.
#
# OFF by default: turning this on means audio is written to disk. It is
# still 100% local — never uploaded — but it is no longer in-memory-only.
# The macOS mic indicator and pre-roll behaviour are unchanged.
SAVE_RECORDINGS = False

# Retention policy for saved recordings:
#   'keep_all' -> never auto-delete
#   'days'     -> delete clips older than RECORDINGS_RETENTION_DAYS
#   'size'     -> keep newest clips within RECORDINGS_MAX_BYTES, prune oldest (default)
#
# 'size' by default: with 'keep_all' one user reached 406 clips and 587 MB in
# three weeks with nothing on the Home screen to say so. Anyone who persisted
# a mode keeps it; the cap only applies where nothing was chosen.
RECORDINGS_RETENTION_MODE = 'size'
VALID_RETENTION_MODES = {'keep_all', 'days', 'size'}
RECORDINGS_RETENTION_DAYS = 30                 # used when mode == 'days'
RECORDINGS_MAX_BYTES = 2 * 1024 * 1024 * 1024  # 2 GiB; used when mode == 'size'
