package com.ritikyadav.say.engine

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** The Mac suite's fixtures (say/tests/test_postprocess.py), so the two ports cannot drift. */
class PostprocessTest {
    private fun opts(
        dictionary: List<String?> = emptyList(),
        replacements: Map<String, String> = emptyMap(),
        autoCorrect: Boolean = true,
        capitalise: Boolean = true,
        punctuation: Boolean = true,
    ) = Postprocess.Options(autoCorrect, capitalise, punctuation, dictionary, replacements)

    // ── soundex ──────────────────────────────────────────────────

    @Test fun cloudAndClaudeCollide() {
        // The whole fuzzy strategy rests on this: they sound alike.
        assertEquals(Postprocess.soundex("cloud"), Postprocess.soundex("claude"))
    }

    @Test fun distinctWordsDoNotCollide() {
        assertNotEquals(Postprocess.soundex("cloud"), Postprocess.soundex("kubernetes"))
    }

    @Test fun emptyIsSafe() {
        assertEquals("", Postprocess.soundex(""))
        assertEquals("", Postprocess.soundex("!!!"))
    }

    // ── vocabulary ───────────────────────────────────────────────

    @Test fun fixesTheRealWorldCase() {
        // 'cloud code' -> 'Claude Code': 26 occurrences in the user's history.
        val out = Postprocess.apply("i use cloud code every day", opts(listOf("Claude Code")))
        assertTrue(out, out.contains("Claude Code"))
        assertFalse(out, out.lowercase().contains("cloud code"))
    }

    @Test fun leavesCorrectTextAlone() {
        assertEquals("I use Claude Code daily", Postprocess.applyFuzzyTerms("I use Claude Code daily", opts(listOf("Claude Code"))))
    }

    @Test fun doesNotHijackUnrelatedUseOfAWord() {
        val out = Postprocess.applyFuzzyTerms("we deployed it to the cloud today", opts(listOf("Claude Code")))
        assertTrue(out.contains("cloud"))
        assertFalse(out.contains("Claude"))
    }

    @Test fun singleWordTermsNeedNearCertainty() {
        val o = opts(listOf("Kubernetes"))
        assertTrue(Postprocess.applyFuzzyTerms("deploy to kubernetes now", o).contains("Kubernetes"))
        assertFalse(Postprocess.applyFuzzyTerms("deploy the burner now", o).contains("Kubernetes"))
    }

    @Test fun punctuationAfterATermIsPreserved() {
        assertTrue(Postprocess.applyFuzzyTerms("I like cloud code, honestly", opts(listOf("Claude Code"))).contains("Claude Code,"))
    }

    @Test fun repairsAWordTheModelSplitInTwo() {
        assertTrue(Postprocess.applyFuzzyTerms("dig into the code base it changed", opts(listOf("codebase"))).contains("the codebase it"))
    }

    @Test fun wideningNeverSwallowsAnAdjacentWord() {
        val o = opts(listOf("Claude Code"))
        assertEquals("I use Claude Code daily", Postprocess.applyFuzzyTerms("I use Claude Code daily", o))
        assertEquals("please run Claude Code now", Postprocess.applyFuzzyTerms("please run Claude Code now", o))
    }

    @Test fun literalReplacementsApply() {
        assertEquals("the codebase is large", Postprocess.applyReplacements("the code base is large", opts(replacements = mapOf("code base" to "codebase"))))
    }

    @Test fun replacementIsWholeWordOnly() {
        assertEquals("concatenate", Postprocess.applyReplacements("concatenate", opts(replacements = mapOf("cat" to "dog"))))
    }

    // ── formatting ───────────────────────────────────────────────

    @Test fun capitalisesOpeningWord() = assertTrue(Postprocess.apply("hello there").startsWith("Hello"))

    @Test fun capitalisesAfterSentenceEnd() = assertTrue(Postprocess.fixCapitalisation("one thing. another thing").contains(". Another"))

    @Test fun capitalisesAfterALineBreak() = assertTrue(Postprocess.fixCapitalisation("first line here\n\nsecond paragraph starts").contains("\n\nSecond"))

    @Test fun lineBreaksArePreserved() = assertTrue(Postprocess.apply("first line here\n\nsecond paragraph starts").contains("\n\n"))

    @Test fun standaloneIIsCapitalised() = assertTrue(Postprocess.fixCapitalisation("i think so").contains("I think"))

    @Test fun addsMissingTerminalPunctuation() = assertTrue(Postprocess.apply("this has no ending").endsWith("."))

    @Test fun doesNotDoublePunctuate() {
        assertEquals("done.", Postprocess.ensureTerminalPunctuation("done."))
        assertEquals("really?", Postprocess.ensureTerminalPunctuation("really?"))
    }

    @Test fun doesNotPunctuateATrailingSymbol() {
        assertEquals("the flag is -", Postprocess.ensureTerminalPunctuation("the flag is -"))
        assertEquals("see item 3:", Postprocess.ensureTerminalPunctuation("see item 3:"))
    }

    @Test fun whitespaceRepair() {
        assertEquals("too many spaces", Postprocess.fixWhitespace("too   many  spaces"))
        assertEquals("space, before", Postprocess.fixWhitespace("space , before"))
    }

    @Test fun thousandsSeparatorSurvives() = assertEquals("costs 1,000 dollars", Postprocess.fixWhitespace("costs 1,000 dollars"))

    // ── switches ─────────────────────────────────────────────────

    @Test fun masterSwitchDisablesEverything() {
        val raw = "i use cloud code"
        assertEquals(raw, Postprocess.apply(raw, opts(listOf("Claude Code"), autoCorrect = false)))
    }

    @Test fun capitalisationCanBeDisabledAlone() = assertTrue(Postprocess.apply("hello there", opts(capitalise = false)).startsWith("hello"))

    @Test fun terminalPunctuationCanBeDisabledAlone() = assertFalse(Postprocess.apply("no ending here", opts(punctuation = false)).endsWith("."))

    @Test fun emptyInputIsUntouched() {
        assertEquals("", Postprocess.apply(""))
        assertEquals("   ", Postprocess.apply("   "))
    }

    @Test fun neverRaisesOnBadConfig() {
        // A malformed dictionary must not cost the user their dictation.
        val out = Postprocess.apply("i use cloud code", opts(listOf(null, "   ", "Claude Code")))
        assertTrue(out, out.contains("Claude Code"))
    }

    // ── repetition loops ─────────────────────────────────────────
    // Fixtures are real failures pulled from the transcript store.

    @Test fun collapsesSingleWordLoop() {
        val text = ("And, these are all " + "available ".repeat(218)).trim()
        assertEquals("And, these are all available", Postprocess.collapseRepetitions(text))
    }

    @Test fun collapsesIntraTokenLoop() {
        assertEquals("Here we can addhold", Postprocess.collapseRepetitions("Here we can add" + "hold".repeat(40)))
    }

    @Test fun collapsesRepeatedPhrase() {
        assertEquals("I will have the modability", Postprocess.collapseRepetitions("I will have the modability ".repeat(4).trim()))
    }

    @Test fun keepsNaturalRepetition() {
        for (phrase in listOf("that is very very good", "no no no she did not")) {
            assertEquals(phrase, Postprocess.collapseRepetitions(phrase))
        }
    }

    @Test fun leavesOrdinaryProseUntouched() {
        val text = "The report is due on Friday. I will send the draft to the team and then we can review the numbers together."
        assertEquals(text, Postprocess.collapseRepetitions(text))
    }

    @Test fun nonConsecutiveRepeatsAreProse() {
        val text = "the cat sat on the mat and the dog sat on the log the end"
        assertEquals(text, Postprocess.collapseRepetitions(text))
    }

    @Test fun applyCollapsesBeforeFormatting() {
        assertEquals("And these are all available.", Postprocess.apply("and these are all " + "available ".repeat(218)))
    }

    @Test fun looksDegenerateFlagsALoop() {
        val text = "And these are all " + "available ".repeat(218)
        assertTrue(Postprocess.looksDegenerate(text, Postprocess.collapseRepetitions(text)))
    }

    @Test fun looksDegenerateIgnoresNormalText() {
        val text = (0 until 40).joinToString(" ") { "word$it" }
        assertFalse(Postprocess.looksDegenerate(text, text))
    }

    // ── length-scaled thresholds ─────────────────────────────────

    @Test fun eightWordPhraseRepeatedThreeTimes() {
        val loop = "But then send people in the open source. ".repeat(3)
        val text = "is there any people in the open source. " + loop + "A model available."
        val out = Postprocess.collapseRepetitions(text)
        assertEquals(1, Regex("But then send people").findAll(out).count())
        assertTrue(out.contains("A model available."))
        assertTrue(out.contains("is there any people in the open source."))
    }

    @Test fun longPhraseRepeatedTwice() {
        val phrase = "the quick brown fox jumps over the lazy dog again and again "
        assertEquals(phrase.trim(), Postprocess.collapseRepetitions(phrase.repeat(2).trim()))
    }

    @Test fun shortPhraseTwiceIsNotALoop() {
        val text = "I think I think we should go"
        assertEquals(text, Postprocess.collapseRepetitions(text))
    }

    @Test fun mediumPhraseTwiceIsNotALoop() {
        val text = "we need to ship we need to ship the thing"
        assertEquals(text, Postprocess.collapseRepetitions(text))
    }

    @Test fun thresholdScalesDownWithLength() {
        assertEquals(4, Postprocess.runThreshold(1))
        assertEquals(4, Postprocess.runThreshold(4))
        assertEquals(3, Postprocess.runThreshold(8))
        assertEquals(2, Postprocess.runThreshold(12))
    }

    // ── difflib parity ───────────────────────────────────────────

    @Test fun sequenceMatcherMatchesPythonOnKnownPairs() {
        // Values from `difflib.SequenceMatcher(None, a, b).ratio()`.
        assertEquals(0.8571428571428571, SequenceMatcher.ratio("cloud code", "claude code"), 1e-9)
        assertEquals(0.4, SequenceMatcher.ratio("the burner", "kubernetes"), 1e-9)
        assertEquals(0.7368421052631579, SequenceMatcher.ratio("kube control", "kubectl"), 1e-9)
        assertEquals(0.9411764705882353, SequenceMatcher.ratio("code base", "codebase"), 1e-9)
        assertEquals(1.0, SequenceMatcher.ratio("abc", "abc"), 1e-9)
        assertEquals(0.0, SequenceMatcher.ratio("abc", "xyz"), 1e-9)
    }

    @Test fun keytermsDropCommasAndBlanks() {
        assertEquals(listOf("Claude Code", "Kubernetes"), Postprocess.keyterms(opts(listOf(" Claude Code ", "", "Kubernetes,"))))
    }

    // ── other scripts (mirrors test_postprocess.OtherScriptsTests) ──────────

    @Test fun aHindiSentenceKeepsItsDandaAndGainsNothing() {
        val text = "मुझे कल सुबह दस बजे बैठक में जाना है।"
        assertEquals(text, Postprocess.apply(text, opts()))
    }

    @Test fun anUrduSentenceKeepsItsOwnFullStop() {
        val text = "مجھے کل صبح دس بجے میٹنگ میں جانا ہے۔"
        assertEquals(text, Postprocess.apply(text, opts()))
    }

    @Test fun hindiWithNoEndingIsLeftAsTheDecoderWroteIt() {
        val text = "मुझे कल सुबह बैठक में जाना है"
        assertEquals(text, Postprocess.ensureTerminalPunctuation(text))
    }

    @Test fun englishCyrillicAndGreekStillGetAFullStop() {
        assertEquals("send the report.", Postprocess.ensureTerminalPunctuation("send the report"))
        assertEquals("Отправь отчёт.", Postprocess.ensureTerminalPunctuation("Отправь отчёт"))
        assertEquals("Στείλε την αναφορά.", Postprocess.ensureTerminalPunctuation("Στείλε την αναφορά"))
        assertEquals("call me at 10.", Postprocess.ensureTerminalPunctuation("call me at 10"))
    }

    // ── replacements (mirrors test_postprocess.ReplacementSeparatorTests) ───

    @Test fun aTwoWordPhraseMatchesAcrossAnyWhitespaceOrAComma() {
        val o = opts(replacements = mapOf("cloud code" to "Claude Code"))
        assertEquals("open Claude Code and Claude Code again",
            Postprocess.applyReplacements("open cloud code and cloud  code again", o))
        assertEquals("open Claude Code now", Postprocess.applyReplacements("open cloud, code now", o))
    }

    @Test fun theSeparatorIsNotTheLetterS() {
        val o = opts(replacements = mapOf("code base" to "codebase"))
        assertEquals("codesbase", Postprocess.applyReplacements("codesbase", o))
    }

    @Test fun longerPhrasesWinOverTheirOwnPrefix() {
        val o = opts(replacements = linkedMapOf("s50" to "STT", "s50 models" to "STT models"))
        assertEquals("these STT models", Postprocess.applyReplacements("these s50 models", o))
    }
}
