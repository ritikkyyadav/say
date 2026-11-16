package com.ritikyadav.say.engine

/**
 * Phrases a speech model produces from silence — "Thank you.", "you",
 * "[BLANK_AUDIO]" — that must never be typed. The Mac engine applies the same
 * list to both of its backends; membership is tested on a normalised form so
 * the phrases can stay punctuation-free.
 */
object Hallucinations {
    private const val STRIP = " \t\n.!?,…\"'`-"

    private val NEUTRAL = listOf("", "...", ". . .", "[blank_audio]", "[music]", "(music)", "*music*")

    private val EN = listOf(
        "you", "thank you", "thanks", "thanks for watching", "thank you for watching",
        "thanks for watching!", "thank you for watching!", "subscribe",
        "subscribe to my channel", "subscribe to the channel", "please subscribe",
        "like and subscribe", "bye", "bye!", "bye-bye", "goodbye", "see you",
        "see you next time", "see you later", "okay", "ok.", "thank you so much",
        "i love you",
    )

    private val ACTIVE: Set<String> = (NEUTRAL + EN).map(::normalise).toSet()

    fun normalise(text: String): String =
        text.lowercase().trim().trim { it in STRIP }.trim()

    fun isHallucination(text: String): Boolean = normalise(text) in ACTIVE
}
