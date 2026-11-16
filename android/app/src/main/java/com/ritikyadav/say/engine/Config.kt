package com.ritikyadav.say.engine

/**
 * Shipped defaults — the single source of truth, as `config.py` is on the Mac.
 * User overrides live in [com.ritikyadav.say.Settings] and are read at call time.
 */
object Config {
    const val SAMPLE_RATE = 16_000

    /** One capture block: 64 ms, the FFT size the glyph analyses. */
    const val BLOCK_SAMPLES = 1024

    /** Safety cap: a dictation this long is stopped and transcribed. */
    const val MAX_RECORDING_SECONDS = 900

    /** Shorter clips are ignored. */
    const val MIN_AUDIO_SECONDS = 0.4

    /** Below this much mic-delivered audio the clip is refused rather than decoded — see the loop gotcha. */
    const val MIN_LIVE_SECONDS = 0.15

    /** Bundled default: Moonshine's smallest streaming English model, offline out of the box. */
    const val MODEL = "tiny-streaming-en"

    const val AUTO_CORRECT = true
    const val AUTO_CAPITALISE = true
    const val AUTO_TERMINAL_PUNCTUATION = true

    /** Android-only: hand the field back to the user's keyboard once text lands. */
    const val RETURN_TO_KEYBOARD = true

    /** How long a completed dictation's outcome stays on the panel before it returns to READY. */
    const val PANEL_NOTICE_MS = 1400L
}
