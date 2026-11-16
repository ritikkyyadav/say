package com.ritikyadav.say.overlay

/**
 * Where dictated text goes inside a field that already has text: at the
 * cursor, replacing any selection, with one space if the cursor is flush
 * against a word. Pure, so the rule is testable without an
 * AccessibilityNodeInfo — the same rule the keyboard and the Home field use.
 */
object TextSplice {
    class Splice(val text: String, val cursor: Int)

    fun splice(existing: String, selStart: Int, selEnd: Int, inserted: String): Splice {
        var start = selStart
        var end = selEnd
        if (start < 0 || start > existing.length) start = existing.length
        if (end < start || end > existing.length) end = start
        val prefix = if (start == 0 || existing[start - 1].isWhitespace()) "" else " "
        // And one after, when the words land in front of another word.
        val suffix = if (end < existing.length && !existing[end].isWhitespace()) " " else ""
        val text = existing.substring(0, start) + prefix + inserted + suffix + existing.substring(end)
        return Splice(text, start + prefix.length + inserted.length)
    }
}
