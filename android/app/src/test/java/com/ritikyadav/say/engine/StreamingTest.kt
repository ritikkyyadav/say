package com.ritikyadav.say.engine

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.Random
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/** The Mac suite's cases (say/tests/test_streaming.py), minus the Whisper prompt plumbing. */
class StreamingTest {
    private val sr = Config.SAMPLE_RATE

    private fun speech(seconds: Double, level: Double = 0.05): FloatArray {
        val rng = Random(0)
        return FloatArray((seconds * sr).toInt()) { (rng.nextGaussian() * level).toFloat() }
    }

    private fun silence(seconds: Double) = FloatArray((seconds * sr).toInt())

    private fun concat(vararg parts: FloatArray): FloatArray {
        val out = FloatArray(parts.sumOf { it.size })
        var p = 0
        for (part in parts) { System.arraycopy(part, 0, out, p, part.size); p += part.size }
        return out
    }

    // ── splitOnSilence ───────────────────────────────────────────

    @Test fun shortClipIsOneSegment() = assertEquals(1, Streaming.splitOnSilence(speech(5.0)).size)

    @Test fun splitsAtPauses() {
        val clip = concat(speech(12.0), silence(1.0), speech(12.0), silence(1.0), speech(10.0))
        assertEquals(3, Streaming.splitOnSilence(clip).size)
    }

    @Test fun segmentationIsLossless() {
        // Cuts land inside silence, so no audio may be dropped at the seams.
        val clip = concat(speech(12.0), silence(1.0), speech(9.0))
        assertArrayEquals(clip, concat(*Streaming.splitOnSilence(clip).toTypedArray()), 0f)
    }

    @Test fun doesNotCutOnAShortGap() {
        // A gap between words is not a pause — cutting there would split speech.
        assertEquals(1, Streaming.splitOnSilence(concat(speech(12.0), silence(0.15), speech(6.0))).size)
    }

    @Test fun doesNotCutBelowTheMinimumLength() {
        assertEquals(1, Streaming.splitOnSilence(concat(speech(2.0), silence(1.0), speech(2.0))).size)
    }

    @Test fun forcesACutAtTheMaximum() {
        // Continuous speech still gets segmented, or nothing would stream.
        val segs = Streaming.splitOnSilence(speech(70.0))
        assertTrue(segs.size >= 2)
        for (s in segs.dropLast(1)) assertTrue(s.size.toDouble() / sr <= Streaming.SEGMENT_MAX_SECONDS + 0.2)
    }

    @Test fun emptyAudio() = assertTrue(Streaming.splitOnSilence(FloatArray(0)).isEmpty())

    // ── SilenceSegmenter ─────────────────────────────────────────

    private fun feed(seg: SilenceSegmenter, audio: FloatArray, block: Int = 1024): Int {
        var cuts = 0
        var i = 0
        while (i < audio.size) {
            val end = minOf(i + block, audio.size)
            if (seg.feed(audio.copyOfRange(i, end))) cuts++
            i = end
        }
        return cuts
    }

    @Test fun matchesTheOfflineSplitterOnAPause() {
        assertEquals(1, feed(SilenceSegmenter(), concat(speech(12.0), silence(1.0), speech(6.0))))
    }

    @Test fun noCutOnShortAudio() = assertEquals(0, feed(SilenceSegmenter(), speech(4.0)))

    @Test fun resetClearsProgress() {
        val seg = SilenceSegmenter()
        feed(seg, speech(7.0))
        seg.reset()
        assertEquals(0, feed(seg, silence(1.0)))
    }

    // ── SegmentedSession ─────────────────────────────────────────

    private class Fake(texts: List<String> = emptyList()) : SegmentTranscriber {
        val texts = ArrayDeque(texts)
        var calls = 0
        override fun transcribe(audio: FloatArray): String {
            calls++
            return texts.removeFirstOrNull() ?: "seg$calls"
        }
    }

    @Test fun joinsBackgroundSegmentsWithTheTail() {
        val s = SegmentedSession(Fake(listOf("one", "two", "three")))
        s.push(speech(1.0))
        s.push(speech(1.0))
        assertEquals("one two three", s.finish(speech(1.0)))
    }

    @Test fun tailOnlyWhenNoSegmentsClosed() {
        assertEquals("only", SegmentedSession(Fake(listOf("only"))).finish(speech(1.0)))
    }

    @Test fun emptySegmentsAreSkipped() {
        val s = SegmentedSession(Fake(listOf("", "kept")))
        s.push(speech(1.0))
        assertEquals("kept", s.finish(speech(1.0)))
    }

    @Test fun aFailingSegmentDoesNotLoseTheRest() {
        val boom = object : SegmentTranscriber {
            var calls = 0
            override fun transcribe(audio: FloatArray): String {
                calls++
                if (calls == 1) throw RuntimeException("segment exploded")
                return "survived"
            }
        }
        val s = SegmentedSession(boom)
        s.push(speech(1.0))
        assertEquals("survived", s.finish(speech(1.0)))
        assertFalse(s.complete)
    }

    @Test fun cancelDiscardsEverything() {
        // The decode is held open so the cancel is guaranteed to land mid-flight.
        val gate = CountDownLatch(1)
        val entered = CountDownLatch(1)
        val slow = SegmentTranscriber {
            entered.countDown()
            gate.await(5, TimeUnit.SECONDS)
            "late"
        }
        val s = SegmentedSession(slow)
        s.push(speech(1.0))
        entered.await(5, TimeUnit.SECONDS)
        s.cancel()
        gate.countDown()
        Thread.sleep(50)
        assertEquals(0, s.segmentCount)
    }

    @Test fun completeOnTheHappyPath() {
        val s = SegmentedSession(Fake(listOf("a", "b")))
        s.push(speech(1.0))
        s.finish(speech(1.0))
        assertTrue(s.complete)
    }

    @Test fun timeoutMarksTheResultIncomplete() {
        val slow = SegmentTranscriber { Thread.sleep(400); "slow" }
        val s = SegmentedSession(slow)
        s.push(speech(1.0))
        s.finish(speech(1.0), timeoutMs = 10)
        assertFalse(s.complete)
    }

    @Test fun segmentsAndTailAreCounted() {
        // `attempted` is what lets the controller tell silence from failure.
        val quiet = SegmentTranscriber { "" }
        val s = SegmentedSession(quiet)
        s.push(speech(9.0))
        s.push(speech(9.0))
        assertEquals("", s.finish(speech(1.0)))
        assertEquals(3, s.attempted)
        assertTrue(s.complete)
    }

    @Test fun noTailCountsOnlyTheSegments() {
        val s = SegmentedSession(SegmentTranscriber { "" })
        s.push(speech(9.0))
        s.finish(null)
        assertEquals(1, s.attempted)
    }

    @Test fun aFailingDecodeIsStillAnAttempt() {
        val s = SegmentedSession(SegmentTranscriber { throw RuntimeException("decoder fell over") })
        s.push(speech(9.0))
        s.finish(null)
        assertEquals(1, s.attempted)
        assertFalse(s.complete)
    }

    @Test fun progressIsReportedPerDecodeAndEndsComplete() {
        val seen = ArrayList<Pair<Int, Int>>()
        val s = SegmentedSession(SegmentTranscriber { "x" }) { d, t -> synchronized(seen) { seen.add(d to t) } }
        s.push(speech(9.0))
        s.push(speech(9.0))
        s.finish(speech(3.0))
        assertEquals(3 to 3, seen.last())
        assertEquals(3 to 3, s.progress)
        for ((a, b) in seen.zipWithNext()) {
            assertTrue(a.first <= a.second)
            assertTrue(a.first <= b.first)
            assertTrue(a.second <= b.second)
        }
    }

    @Test fun aRaisingCallbackNeverBreaksTheSession() {
        val s = SegmentedSession(SegmentTranscriber { "x" }) { _, _ -> throw RuntimeException("ui went away") }
        s.push(speech(9.0))
        assertEquals("x x", s.finish(speech(3.0)))
        assertTrue(s.complete)
    }
}
