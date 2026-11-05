package com.ritikyadav.say.engine

/**
 * When does a dictation end on its own?
 *
 * On the Mac the answer is physical: you release Fn. A phone has no key to
 * hold, so Say has an explicit "done" tap — and, optionally, this rule, which
 * the controller consults after every 64 ms block while recording. Returning
 * true stops the recording exactly as the done tap would.
 *
 * The inputs come from the same voice gate that drives the glyph (see
 * [com.ritikyadav.say.glyph.BandAnalyzer]): adaptive to the room, so "silence"
 * here means "no voice", not "no sound".
 */
object EndOfSpeech {
    /**
     * @param trailingSilenceS seconds since the gate last heard a voice
     * @param spokeYet         whether a voice has been heard at all this dictation
     * @param totalS           seconds recorded so far
     */
    fun shouldFinish(trailingSilenceS: Double, spokeYet: Boolean, totalS: Double): Boolean {
        // TODO(human): decide when Say should stop listening on its own.
        // Returning false here means only the done tap ends a dictation.
        return false
    }
}
