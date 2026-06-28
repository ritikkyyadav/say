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
MIN_AUDIO_SECONDS = 0.4        # Shorter clips are ignored

# Pre-roll: amount of audio captured *before* the hotkey-down moment that
# is prepended to the recording. Eliminates first-syllable clipping
# caused by hotkey debounce + reaction time.
#
# When > 0, the microphone stream stays open between recordings so the
# ring buffer can keep filling. The macOS mic-active LED is your honest
# indicator that the stream is live — no audio is written to disk and
# the buffer is in-memory only. Set to 0 to disable pre-roll.
PRE_ROLL_SECONDS = 0.5

# Polling interval for the input-device-change watcher (e.g. AirPods
# unplugged mid-recording). 0 disables the watcher.
DEVICE_WATCH_INTERVAL_S = 2.0

# -- Language --------------------------------------------------
# Set to a language code (e.g., 'en', 'es', 'fr', 'hi') for forced language.
# Set to None for auto-detect (slightly slower, but handles multilingual input).
LANGUAGE = 'en'

# -- Text Injection -------------------------------------------
# How transcribed text is inserted into the active app.
#   'auto'           -> Try Accessibility first, fall back to clipboard paste
#   'accessibility'  -> Direct keystroke injection (requires Accessibility permission)
#   'clipboard'      -> Copy, Cmd+V paste (works in most apps)
INJECT_METHOD = 'auto'
VALID_INJECT_METHODS = {'auto', 'accessibility', 'clipboard'}

# -- Custom Vocabulary / Prompt -------------------------------
# Whisper's initial_prompt biases transcription toward these terms.
# Useful for domain-specific jargon. Keep under 100 tokens.
# Empty / None = no bias. Users can override via Settings (Phase 6) or
# by editing this file directly.
INITIAL_PROMPT = ''

# -- Notifications --------------------------------------------
SHOW_NOTIFICATIONS = True      # Show macOS notification on transcription complete

# -- Menu Bar --------------------------------------------------
MENU_BAR_ICON = '🎙'           # Emoji shown in the macOS menu bar

# -- Overlay ---------------------------------------------------
SHOW_OVERLAY = True            # Show floating waveform overlay during dictation
OVERLAY_POSITION = 'bottom'    # 'bottom' or 'top' — screen position

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
#   'keep_all' -> never auto-delete (default)
#   'days'     -> delete clips older than RECORDINGS_RETENTION_DAYS
#   'size'     -> keep newest clips within RECORDINGS_MAX_BYTES, prune oldest
RECORDINGS_RETENTION_MODE = 'keep_all'
VALID_RETENTION_MODES = {'keep_all', 'days', 'size'}
RECORDINGS_RETENTION_DAYS = 30                 # used when mode == 'days'
RECORDINGS_MAX_BYTES = 2 * 1024 * 1024 * 1024  # 2 GiB; used when mode == 'size'
