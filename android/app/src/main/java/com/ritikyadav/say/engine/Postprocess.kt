package com.ritikyadav.say.engine

/**
 * Deterministic clean-up applied between transcription and commit.
 *
 * A line-for-line port of the Mac engine's `postprocess.py`: raw ASR text goes
 * in, the text that actually gets typed comes out. Everything here is
 * rule-based, pure and fast — there is no model in this file — and it never
 * raises: a formatting bug must not cost the user their dictation.
 *
 * Stages, in order:
 *   0. repetition-loop collapse (a decoder that stopped listening),
 *   1. vocabulary — literal replacements, then guarded fuzzy/phonetic repair,
 *   2. formatting — whitespace, capitalisation, terminal punctuation.
 *
 * The Mac's thresholds were set by replaying every stored dictation, not by
 * guessing (see CLAUDE.md); they are carried over unchanged, and the tests in
 * PostprocessTest are the Python suite's fixtures, so the two implementations
 * cannot quietly diverge.
 */
object Postprocess {

    /** What the stages consult. The Mac reads these off `config`; here they travel explicitly. */
    data class Options(
        val autoCorrect: Boolean = true,
        val autoCapitalise: Boolean = true,
        val autoTerminalPunctuation: Boolean = true,
        /** Vocabulary terms: bias the decoder AND repair near-misses afterwards. */
        val dictionary: List<String?> = emptyList(),
        /** Literal whole-word substitutions, as heard → wanted. */
        val replacements: Map<String, String> = emptyMap(),
    )

    // Multi-word terms are far safer to correct than single words — "cloud
    // code" can only plausibly mean "Claude Code", whereas a bare "cloud"
    // usually means the sky or AWS.
    private const val FUZZY_MIN_SINGLE = 0.92
    private const val FUZZY_MIN_MULTI = 0.78

    /** Tokens that should never gain a trailing full stop. */
    private val NO_TERMINAL_PUNCT = Regex("""[)\]}>:;,\-_/\\|=+*&^%$#@~`]$""")

    /** Longest n-gram checked for looping. */
    const val MAX_PHRASE_LEN = 12

    // The same failure with no spaces, when the repeated unit is a sub-word
    // token: "addholdholdholdhold". A 2-12 character unit repeated four or
    // more times. "banana" is safe — "an" occurs only twice.
    private val INTRA_TOKEN_LOOP = Regex("(.{2,12}?)\\1{3,}", RegexOption.IGNORE_CASE)

    // Written out as \p{L}\p{N}_ rather than \w: on the JVM \w is ASCII unless
    // the (?U) flag is on, and Android's regex engine is ICU, which rejects
    // (?U) outright (a PatternSyntaxException at class init — found on the
    // first device run, invisible to the JVM tests). Explicit classes mean
    // the same thing on both engines and match Python's Unicode \w.
    private const val WORD = "[\\p{L}\\p{N}_]"
    private val EDGE_PUNCT = Regex("^[^\\p{L}\\p{N}_]+|[^\\p{L}\\p{N}_]+$")
    private val TRAILING_PUNCT = Regex("[^\\p{L}\\p{N}_]+$")
    private val WHITESPACE = Regex("\\s+")

    // ── phonetics ────────────────────────────────────────────────

    /**
     * Classic Soundex. Collapses vowels, so "cloud" and "claude" both reduce
     * to C43 and compare equal — exactly the class of error a speech model makes.
     */
    fun soundex(word: String): String {
        val w = word.lowercase().filter { it in 'a'..'z' }
        if (w.isEmpty()) return ""
        val first = w[0].uppercaseChar()
        val out = StringBuilder()
        var prev = code(w[0])
        for (ch in w.substring(1)) {
            val c = code(ch)
            if (c.isNotEmpty() && c != prev) out.append(c)
            // h and w are transparent: they don't break a repeat run.
            if (ch != 'h' && ch != 'w') prev = c
        }
        return (first + out.toString() + "000").take(4)
    }

    private fun code(ch: Char): String = when (ch) {
        'b', 'f', 'p', 'v' -> "1"
        'c', 'g', 'j', 'k', 'q', 's', 'x', 'z' -> "2"
        'd', 't' -> "3"
        'l' -> "4"
        'm', 'n' -> "5"
        'r' -> "6"
        else -> ""
    }

    /** Blend literal similarity with phonetic agreement, 0..1. */
    internal fun similar(a: String, b: String): Double {
        val al = a.lowercase()
        val bl = b.lowercase()
        if (al == bl) return 1.0
        val literal = SequenceMatcher.ratio(al, bl)
        val at = al.split(WHITESPACE).filter { it.isNotEmpty() }
        val bt = bl.split(WHITESPACE).filter { it.isNotEmpty() }
        if (at.size != bt.size || at.isEmpty()) return literal
        val hits = at.indices.count { i ->
            val sx = soundex(at[i])
            sx.isNotEmpty() && sx == soundex(bt[i])
        }
        val phonetic = hits.toDouble() / at.size
        // Literal similarity dominates; phonetic agreement pulls near-misses up.
        return 0.6 * literal + 0.4 * phonetic
    }

    // ── configuration access ─────────────────────────────────────

    private fun terms(options: Options): List<String> =
        options.dictionary.mapNotNull { it?.trim() }.filter { it.isNotEmpty() }

    private fun replacements(options: Options): Map<String, String> =
        options.replacements.entries
            .filter { it.key.trim().isNotEmpty() }
            .associate { it.key.trim() to it.value }

    /** Terms in the form the decoder's key-term bias accepts (no commas). */
    fun keyterms(options: Options): List<String> =
        terms(options).map { it.replace(",", " ").trim() }.filter { it.isNotEmpty() }

    // ── stage 0: decoder repetition ──────────────────────────────

    /**
     * Consecutive repeats required to treat a `size`-word phrase as a loop.
     * The threshold falls as phrases get longer because the odds of saying the
     * same thing verbatim collapse with every extra word: "no no no no" is
     * emphasis, but an eight-word sentence three times in a row is a decoder
     * that has stopped listening.
     */
    fun runThreshold(size: Int): Int = when {
        size <= 4 -> 4
        size <= 8 -> 3
        else -> 2
    }

    private fun normWord(word: String): String = EDGE_PUNCT.replace(word, "").lowercase()

    /** Collapse loops that occur inside a single whitespace-delimited token. */
    fun collapseIntraTokenLoops(text: String): String =
        INTRA_TOKEN_LOOP.replace(text) { it.groupValues[1] }

    /**
     * Collapse degenerate repeated words and phrases to a single instance.
     * Only *consecutive* repeats count; the first instance is always kept, so
     * the salvageable part of the transcript survives.
     */
    fun collapseRepetitions(text: String): String {
        if (text.isBlank()) return text
        val collapsed = collapseIntraTokenLoops(text)
        // Process each run of non-newline text separately and stitch the
        // newline runs back in, so paragraph structure survives.
        val out = StringBuilder()
        val newlines = Regex("\n+")
        var last = 0
        for (m in newlines.findAll(collapsed)) {
            out.append(collapseLine(collapsed.substring(last, m.range.first)))
            out.append(m.value)
            last = m.range.last + 1
        }
        out.append(collapseLine(collapsed.substring(last)))
        return out.toString()
    }

    private fun collapseLine(text: String): String {
        var tokens = text.split(WHITESPACE).filter { it.isNotEmpty() }
        if (tokens.size < 2) return text
        val leading = text.substring(0, text.length - text.trimStart().length)
        val trailing = text.substring(text.trimEnd().length)

        // Shortest n-gram first: each loop is caught at its own period, and
        // the longer passes only see what genuinely remains.
        for (size in 1..MAX_PHRASE_LEN) {
            val threshold = runThreshold(size)
            val out = ArrayList<String>(tokens.size)
            var i = 0
            while (i < tokens.size) {
                val phrase = tokens.subList(i, minOf(i + size, tokens.size)).map(::normWord)
                if (phrase.size < size || phrase.all { it.isEmpty() }) {
                    out.add(tokens[i]); i += 1; continue
                }
                var repeats = 1
                var j = i + size
                while (j + size <= tokens.size) {
                    if (tokens.subList(j, j + size).map(::normWord) != phrase) break
                    repeats += 1
                    j += size
                }
                if (repeats >= threshold) {
                    out.addAll(tokens.subList(i, i + size))
                    i = j
                } else {
                    out.add(tokens[i]); i += 1
                }
            }
            tokens = out
        }
        return leading + tokens.joinToString(" ") + trailing
    }

    /** True when collapsing removed so much that the clip was mostly a loop. */
    fun looksDegenerate(original: String, collapsed: String): Boolean {
        val o = original.split(WHITESPACE).count { it.isNotEmpty() }
        val c = collapsed.split(WHITESPACE).count { it.isNotEmpty() }
        return o >= 20 && c <= o * 0.4
    }

    // ── stage 1: vocabulary ──────────────────────────────────────

    /** Keep the writer's intent: only force case when the source was neutral. */
    private fun matchCase(replacement: String, observed: String): String =
        if (observed.length > 1 && observed == observed.uppercase() && observed != observed.lowercase()) {
            replacement.uppercase()
        } else replacement

    /**
     * Literal, user-defined substitutions. Whole-word, case-insensitive.
     *
     * Longest phrase first, so "s50 models" wins over a rule for "s50"; and a
     * comma or any run of spaces may separate a phrase's words, because the
     * decoder puts a comma wherever the speaker paused. Mirrors
     * postprocess.apply_replacements, whose separator was once `[,\\s]+` in a
     * raw string — comma, backslash, the letter s — and matched nothing.
     */
    fun applyReplacements(text: String, options: Options): String {
        var out = text
        for ((wrong, right) in replacements(options).entries.sortedByDescending { it.key.length }) {
            val words = wrong.split(WHITESPACE).filter { it.isNotEmpty() }
            val body = if (words.size > 1) words.joinToString("[,\\s]+") { Regex.escape(it) } else Regex.escape(wrong)
            val pattern = Regex("(?<!$WORD)$body(?!$WORD)", RegexOption.IGNORE_CASE)
            out = pattern.replace(out) { matchCase(right, it.value) }
        }
        return out
    }

    private fun despace(s: String): String = WHITESPACE.replace(s, "").lowercase()

    /**
     * Repair near-misses of known vocabulary terms. Slides a window of the
     * same word-length as each term across the transcript and replaces windows
     * that are close enough phonetically and literally. Exact matches are left
     * alone; anything already correct never gets touched.
     */
    fun applyFuzzyTerms(text: String, options: Options): String {
        val terms = terms(options)
        if (terms.isEmpty()) return text
        var tokens = text.split(" ").toMutableList()
        if (tokens.isEmpty()) return text
        // Longest terms first so "Claude Code" wins over a bare "Claude" entry.
        for (term in terms.sortedByDescending { it.split(WHITESPACE).size }) {
            val termWords = term.split(WHITESPACE).count { it.isNotEmpty() }
            if (termWords == 0) continue
            // Speech models split and join compound words unpredictably —
            // "codebase" is heard as "code base" — so a term of W words is
            // compared against windows of W-1..W+1 words, whitespace ignored.
            for (span in listOf(termWords, termWords + 1, termWords - 1)) {
                if (span in 1..tokens.size) tokens = matchSpan(tokens, term, span)
            }
        }
        return tokens.joinToString(" ")
    }

    /**
     * Fuzzy similarity is only allowed when the window is the same width as
     * the term. A wider or narrower window may only match on an exact
     * despaced comparison — that widening exists to repair split/joined words,
     * and letting it match fuzzily would let a 2-word term swallow a neighbour.
     */
    private fun matchSpan(input: MutableList<String>, term: String, span: Int): MutableList<String> {
        val tokens = input
        val termWords = term.split(WHITESPACE).count { it.isNotEmpty() }
        val sameWidth = span == termWords
        val threshold = if (termWords == 1) FUZZY_MIN_SINGLE else FUZZY_MIN_MULTI
        val termDespaced = despace(term)
        val termLen = termDespaced.length
        // Cheap pre-filters: a length band plus a first-consonant check reject
        // almost every candidate before the (slow) full comparison runs.
        val maxLenDelta = maxOf(2, (0.35 * termLen).toInt())
        val termFirst = soundex(termDespaced).take(1)
        var i = 0
        while (i + span <= tokens.size) {
            val window = tokens.subList(i, i + span).joinToString(" ")
            val bare = EDGE_PUNCT.replace(window, "")
            if (bare.isEmpty()) { i += 1; continue }
            // Already exactly right — leave it completely alone.
            if (bare == term) { i += span; continue }
            val bareDespaced = despace(bare)
            val despacedMatch = bareDespaced == termDespaced
            var fuzzyOk = false
            if (!despacedMatch && sameWidth &&
                kotlin.math.abs(bareDespaced.length - termLen) <= maxLenDelta &&
                soundex(bareDespaced).take(1) == termFirst
            ) {
                fuzzyOk = similar(bare, term) >= threshold
            }
            if (despacedMatch || fuzzyOk) {
                val suffix = TRAILING_PUNCT.find(window)?.value ?: ""
                val replaced = (term + suffix).split(" ")
                for (k in 0 until span) tokens.removeAt(i)
                tokens.addAll(i, replaced)
                i += replaced.size
                continue
            }
            i += 1
        }
        return tokens
    }

    // ── stage 2: formatting ──────────────────────────────────────

    fun fixWhitespace(text: String): String {
        var t = text.replace(' ', ' ')
        t = Regex("[ \t]+").replace(t, " ")
        t = Regex(" +([,.!?;:])").replace(t, "$1")           // no space before punctuation
        t = Regex("([,;:])(?=[^\\s\\d])").replace(t, "$1 ")   // space after, but not in 1,000
        return t.trim()
    }

    /** Capitalise the opening word and anything following a sentence end. */
    fun fixCapitalisation(text: String): String {
        if (text.isEmpty()) return text
        var t = Regex("([.!?]\\s+)([a-z])").replace(text) { it.groupValues[1] + it.groupValues[2].uppercase() }
        // A line or paragraph break starts a new sentence even without punctuation.
        t = Regex("(\n\\s*)([a-z])").replace(t) { it.groupValues[1] + it.groupValues[2].uppercase() }
        // Standalone "i" is always capitalised in English.
        t = Regex("(?<!$WORD)i(?!$WORD)").replace(t, "I")
        return t.substring(0, 1).uppercase() + t.substring(1)
    }

    /** Sentence endings in scripts that do not close with a Latin full stop. */
    private const val NATIVE_TERMINATORS = "।॥۔؟。！？"

    /**
     * Whether "." is the right ending for the script this text ends in. Latin,
     * Greek and Cyrillic close with "."; Hindi with a danda, Urdu with its own
     * stop. A "." typed after either is a mistake in someone else's language.
     * Mirrors postprocess._closes_with_a_full_stop.
     */
    private fun closesWithAFullStop(text: String): Boolean {
        for (i in text.indices.reversed()) {
            val ch = text[i]
            if (Character.isLetter(ch)) {
                val cp = ch.code
                return cp < 0x0530 || cp in 0x1E00..0x1EFF
            }
        }
        return true
    }

    /** Give the last sentence an ending, unless one would be wrong. */
    fun ensureTerminalPunctuation(text: String): String {
        if (text.isEmpty()) return text
        val stripped = text.trimEnd()
        if (stripped.isEmpty()) return text
        if (stripped.last() in ".!?\"')…" || stripped.last() in NATIVE_TERMINATORS) return stripped
        if (NO_TERMINAL_PUNCT.containsMatchIn(stripped)) return stripped
        if (!closesWithAFullStop(stripped)) return stripped
        return "$stripped."
    }

    // ── public API ───────────────────────────────────────────────

    /** Run every enabled deterministic stage. Never throws. */
    fun apply(text: String, options: Options = Options()): String {
        if (text.isBlank()) return text
        if (!options.autoCorrect) return text
        return try {
            var out = collapseRepetitions(text)
            out = applyReplacements(out, options)
            out = applyFuzzyTerms(out, options)
            out = fixWhitespace(out)
            if (options.autoCapitalise) out = fixCapitalisation(out)
            if (options.autoTerminalPunctuation) out = ensureTerminalPunctuation(out)
            out
        } catch (e: Exception) {
            text
        }
    }
}

/**
 * Python's `difflib.SequenceMatcher.ratio()`, for strings shorter than the
 * auto-junk threshold (200) — which every vocabulary window is. The fuzzy
 * thresholds above were tuned against this exact measure, so the port has to
 * compute the same number, tie-breaks included: the longest common block that
 * ends earliest in `a` (then `b`) wins, then the recursion on either side.
 */
internal object SequenceMatcher {
    fun ratio(a: String, b: String): Double {
        val total = a.length + b.length
        if (total == 0) return 1.0
        return 2.0 * matches(a, b) / total
    }

    private fun matches(a: String, b: String): Int {
        var sum = 0
        val queue = ArrayDeque<IntArray>()
        queue.add(intArrayOf(0, a.length, 0, b.length))
        while (queue.isNotEmpty()) {
            val (alo, ahi, blo, bhi) = queue.removeFirst()
            val (i, j, k) = longestMatch(a, b, alo, ahi, blo, bhi)
            if (k > 0) {
                sum += k
                if (alo < i && blo < j) queue.add(intArrayOf(alo, i, blo, j))
                if (i + k < ahi && j + k < bhi) queue.add(intArrayOf(i + k, ahi, j + k, bhi))
            }
        }
        return sum
    }

    private fun longestMatch(a: String, b: String, alo: Int, ahi: Int, blo: Int, bhi: Int): Triple<Int, Int, Int> {
        var besti = alo
        var bestj = blo
        var bestsize = 0
        var j2len = HashMap<Int, Int>()
        for (i in alo until ahi) {
            val newj2len = HashMap<Int, Int>()
            for (j in blo until bhi) {
                if (b[j] != a[i]) continue
                val k = (j2len[j - 1] ?: 0) + 1
                newj2len[j] = k
                if (k > bestsize) {
                    besti = i - k + 1
                    bestj = j - k + 1
                    bestsize = k
                }
            }
            j2len = newj2len
        }
        return Triple(besti, bestj, bestsize)
    }
}
