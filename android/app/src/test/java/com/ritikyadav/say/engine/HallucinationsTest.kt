package com.ritikyadav.say.engine

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class HallucinationsTest {
    @Test fun punctuationDoesNotHideAHallucination() {
        assertTrue(Hallucinations.isHallucination("Thank you."))
        assertTrue(Hallucinations.isHallucination(" Thank you! "))
        assertTrue(Hallucinations.isHallucination("Thanks…"))
    }

    @Test fun silenceMarkersAreFiltered() {
        assertTrue(Hallucinations.isHallucination(""))
        assertTrue(Hallucinations.isHallucination("[BLANK_AUDIO]"))
        assertTrue(Hallucinations.isHallucination("you"))
    }

    @Test fun realSpeechPasses() {
        assertFalse(Hallucinations.isHallucination("Thank you for the report, it reads well."))
        assertFalse(Hallucinations.isHallucination("Hello world."))
    }
}
