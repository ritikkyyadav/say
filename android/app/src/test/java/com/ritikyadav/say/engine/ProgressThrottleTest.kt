package com.ritikyadav.say.engine

import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * A download reports progress on every 64 KB read. What reaches a listener has
 * to be far rarer than that: each report rebuilds the model list on the main
 * thread, and unthrottled the download outran the UI thread until the heap
 * died — a real `OutOfMemoryError` at 192 MB, 50 seconds into the first honest
 * test of the first-launch fetch.
 *
 * This pins the arithmetic that decides how many reports a real download can
 * produce, at speeds a phone actually sees.
 */
class ProgressThrottleTest {
    /** Reports emitted for `totalBytes` fetched at `bytesPerSecond`, under the shipped rule. */
    private fun reports(totalBytes: Long, bytesPerSecond: Long): Int {
        val chunk = 64L * 1024
        var done = 0L
        var millis = 0L
        var lastPercent = -1
        // Not Long.MIN_VALUE: `millis - lastAt` then overflows negative and the
        // gate never opens. (It did, and this test scored a working throttle
        // as emitting nothing at all.)
        var lastAt = -ModelManager.PROGRESS_INTERVAL_MS
        var count = 0
        while (done < totalBytes) {
            done += chunk
            millis += chunk * 1000 / bytesPerSecond
            val percent = (100 * done / maxOf(1, totalBytes)).toInt().coerceIn(0, 99)
            if (percent != lastPercent && millis - lastAt >= ModelManager.PROGRESS_INTERVAL_MS) {
                lastPercent = percent
                lastAt = millis
                count++
            }
        }
        return count
    }

    private val tiny = ModelCatalog.get("tiny-streaming-en")!!.downloadBytes
    private val medium = ModelCatalog.get("medium-streaming-en")!!.downloadBytes

    @Test fun anOrdinaryDownloadReportsOftenEnoughToWatchAndRarelyEnoughToSurvive() {
        val n = reports(tiny, 2_500_000)          // 51 MB in ~20 s, what the emulator managed
        assertTrue("$n reports is not a moving progress bar", n >= 20)
        assertTrue("$n reports is more than a UI thread should be handed", n <= 100)
    }

    @Test fun aFastLinkCannotFloodTheUiThread() {
        assertTrue(reports(medium, 50_000_000) <= 100)
        assertTrue(reports(tiny, 200_000_000) <= 100)
    }

    @Test fun aSlowLinkStillReportsPerWholePercent() {
        assertTrue(reports(tiny, 200_000) in 90..100)
    }

    @Test fun theUnthrottledPathItReplacesFiredHundredsOfTimesPerFile() {
        assertTrue("this is what filled the heap", medium / (64 * 1024) > 4_000)
        assertTrue(tiny / (64 * 1024) > 700)
    }
}
