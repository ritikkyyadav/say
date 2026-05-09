# config.py - Say Configuration
# ============================================================
# On-device voice dictation for macOS
# All user-facing settings live here. No hardcoded values elsewhere.
# ============================================================

# -- Model -----------------------------------------------------
# Options:
#   'mlx-community/whisper-large-v3-turbo'     -> fast daily-driver default
#   'mlx-community/whisper-large-v3-mlx'       -> highest accuracy (~2.9 GB)
#   'mlx-community/whisper-medium-mlx'         -> balanced fallback
#   'mlx-community/whisper-small-mlx'          -> low storage / older hardware
MODEL = 'mlx-community/whisper-large-v3-turbo'

VALID_MODELS = {
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
# Apple Silicon currently dispatches the system globe-key action
# ("Press 🌐 to: Show Emoji & Symbols", input-source switch, Start
# Dictation) below every user-space hook we have access to —
# CGEventTap at any level, including HID, can be bypassed, and even
# flipping AppleFnUsageType to 0 doesn't reliably suppress the action
# on every macOS version. Until that is solved, the safe default is
# the legacy ctrl+space hotkey, which has none of those interactions.
PREFER_LEGACY_HOTKEY = True

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
MAX_RECORDING_SECONDS = 60     # Safety cap — stops recording after this duration
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
