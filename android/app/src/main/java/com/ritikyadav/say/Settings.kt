package com.ritikyadav.say

import android.content.Context
import android.content.SharedPreferences
import com.ritikyadav.say.engine.Config
import com.ritikyadav.say.engine.Postprocess

/**
 * User overrides on top of [Config], persisted in SharedPreferences and read
 * at call time — so a switch flipped on the Home screen applies to the very
 * next dictation, the way `settings.py` mirrors into `config` on the Mac.
 */
class Settings(context: Context) {
    private val prefs: SharedPreferences =
        context.applicationContext.getSharedPreferences("say_settings", Context.MODE_PRIVATE)

    var autoCorrect: Boolean
        get() = prefs.getBoolean(KEY_AUTO_CORRECT, Config.AUTO_CORRECT)
        set(value) = prefs.edit().putBoolean(KEY_AUTO_CORRECT, value).apply()

    var autoCapitalise: Boolean
        get() = prefs.getBoolean(KEY_AUTO_CAPITALISE, Config.AUTO_CAPITALISE)
        set(value) = prefs.edit().putBoolean(KEY_AUTO_CAPITALISE, value).apply()

    var autoTerminalPunctuation: Boolean
        get() = prefs.getBoolean(KEY_AUTO_TERMINAL_PUNCTUATION, Config.AUTO_TERMINAL_PUNCTUATION)
        set(value) = prefs.edit().putBoolean(KEY_AUTO_TERMINAL_PUNCTUATION, value).apply()

    var returnToKeyboard: Boolean
        get() = prefs.getBoolean(KEY_RETURN_TO_KEYBOARD, Config.RETURN_TO_KEYBOARD)
        set(value) = prefs.edit().putBoolean(KEY_RETURN_TO_KEYBOARD, value).apply()

    /** Catalogue id of the model in use. */
    var model: String
        get() = prefs.getString(KEY_MODEL, Config.MODEL) ?: Config.MODEL
        set(value) = prefs.edit().putString(KEY_MODEL, value).apply()

    /** Vocabulary, one term per line, exactly as the user typed it. */
    var dictionaryText: String
        get() = prefs.getString(KEY_DICTIONARY, "") ?: ""
        set(value) = prefs.edit().putString(KEY_DICTIONARY, value).apply()

    /** Replacements, one per line, "as heard = wanted". */
    var replacementsText: String
        get() = prefs.getString(KEY_REPLACEMENTS, "") ?: ""
        set(value) = prefs.edit().putString(KEY_REPLACEMENTS, value).apply()

    /** Where the floating mark was last dragged to, in screen pixels; -1 = default. */
    var bubbleX: Int
        get() = prefs.getInt(KEY_BUBBLE_X, -1)
        set(value) = prefs.edit().putInt(KEY_BUBBLE_X, value).apply()

    var bubbleY: Int
        get() = prefs.getInt(KEY_BUBBLE_Y, -1)
        set(value) = prefs.edit().putInt(KEY_BUBBLE_Y, value).apply()

    val dictionary: List<String> get() = parseDictionary(dictionaryText)
    val replacements: Map<String, String> get() = parseReplacements(replacementsText)

    fun postprocessOptions(): Postprocess.Options = Postprocess.Options(
        autoCorrect = autoCorrect,
        autoCapitalise = autoCapitalise,
        autoTerminalPunctuation = autoTerminalPunctuation,
        dictionary = dictionary,
        replacements = replacements,
    )

    fun registerListener(listener: SharedPreferences.OnSharedPreferenceChangeListener) =
        prefs.registerOnSharedPreferenceChangeListener(listener)

    fun unregisterListener(listener: SharedPreferences.OnSharedPreferenceChangeListener) =
        prefs.unregisterOnSharedPreferenceChangeListener(listener)

    companion object {
        const val KEY_AUTO_CORRECT = "AUTO_CORRECT"
        const val KEY_AUTO_CAPITALISE = "AUTO_CAPITALISE"
        const val KEY_AUTO_TERMINAL_PUNCTUATION = "AUTO_TERMINAL_PUNCTUATION"
        const val KEY_RETURN_TO_KEYBOARD = "RETURN_TO_KEYBOARD"
        const val KEY_MODEL = "MODEL"
        const val KEY_DICTIONARY = "DICTIONARY"
        const val KEY_REPLACEMENTS = "DICTIONARY_REPLACEMENTS"
        const val KEY_BUBBLE_X = "BUBBLE_X"
        const val KEY_BUBBLE_Y = "BUBBLE_Y"

        /** One term per line; commas also separate, since that is how people type lists. */
        fun parseDictionary(text: String): List<String> =
            text.split('\n', ',').map { it.trim() }.filter { it.isNotEmpty() }.distinct()

        /** "as heard = wanted" per line. Lines without an `=` are ignored rather than guessed at. */
        fun parseReplacements(text: String): Map<String, String> {
            val out = LinkedHashMap<String, String>()
            for (line in text.lines()) {
                val eq = line.indexOf('=')
                if (eq <= 0) continue
                val wrong = line.substring(0, eq).trim()
                val right = line.substring(eq + 1).trim()
                if (wrong.isNotEmpty() && right.isNotEmpty()) out[wrong] = right
            }
            return out
        }
    }
}
