package com.ritikyadav.say.overlay

import org.junit.Assert.assertEquals
import org.junit.Test

class TextSpliceTest {
    @Test fun emptyFieldGetsTheWordsAndTheCursorAfterThem() {
        val s = TextSplice.splice("", 0, 0, "Hello there.")
        assertEquals("Hello there.", s.text)
        assertEquals(12, s.cursor)
    }

    @Test fun cursorFlushAgainstAWordGetsOneSpace() {
        val s = TextSplice.splice("Dear Sam", 8, 8, "how are you?")
        assertEquals("Dear Sam how are you?", s.text)
        assertEquals(21, s.cursor)
    }

    @Test fun cursorAfterASpaceGetsNoExtraSpace() {
        assertEquals("Dear Sam how", TextSplice.splice("Dear Sam ", 9, 9, "how").text)
    }

    @Test fun insertsAtTheCursorNotTheEnd() {
        val s = TextSplice.splice("one three", 4, 4, "two")
        assertEquals("one two three", s.text)
        assertEquals(7, s.cursor)
    }

    @Test fun aSelectionIsReplaced() {
        assertEquals("one two four", TextSplice.splice("one xxx four", 4, 7, "two").text)
    }

    @Test fun unknownSelectionAppends() {
        assertEquals("text more", TextSplice.splice("text", -1, -1, "more").text)
        assertEquals("text more", TextSplice.splice("text", 99, 99, "more").text)
    }
}
