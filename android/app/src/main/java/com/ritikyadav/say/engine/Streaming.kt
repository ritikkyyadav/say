package com.ritikyadav.say.engine

import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger
import kotlin.math.sqrt

/**
 * Segment audio at silence so it decodes while the user is still talking.
 *
 * The wait after tapping "done" scales with how long you spoke, because
 * nothing starts until you stop. Cutting the clip at natural pauses and
 * transcribing each piece in the background collapses that to whatever is
 * left in the final segment — roughly constant, regardless of length. This is
 * the Mac engine's `streaming.py` with the Whisper prompt plumbing removed:
 * Moonshine takes no text prompt, so segments here need no carry-over.
 */
object Streaming {
    /** A segment shorter than this is not worth a separate decoder call. */
    const val SEGMENT_MIN_SECONDS = 8.0

    /** Streaming models take long input, but a cut every 30 s bounds the tail. */
    const val SEGMENT_MAX_SECONDS = 30.0

    /** How much continuous quiet marks a real pause rather than a gap between words. */
    const val SEGMENT_SILENCE_SECONDS = 0.45

    /** RMS below this counts as silence. Speech sits around 0.01-0.15; room tone well under 0.005. */
    const val SILENCE_RMS = 0.004

    fun rms(block: FloatArray, from: Int = 0, to: Int = block.size): Double {
        if (to <= from) return 0.0
        var acc = 0.0
        for (i in from until to) {
            val v = block[i].toDouble()
            acc += v * v
        }
        return sqrt(acc / (to - from))
    }

    /**
     * Split `audio` into segments, cutting only inside silent runs. Pure — the
     * offline form of [SilenceSegmenter], kept so a bench can score the rule
     * against stored dictations. The concatenation is the original audio.
     */
    fun splitOnSilence(
        audio: FloatArray,
        sampleRate: Int = Config.SAMPLE_RATE,
        minSeconds: Double = SEGMENT_MIN_SECONDS,
        maxSeconds: Double = SEGMENT_MAX_SECONDS,
        silenceSeconds: Double = SEGMENT_SILENCE_SECONDS,
        threshold: Double = SILENCE_RMS,
        blockSeconds: Double = 0.064,
    ): List<FloatArray> {
        if (audio.isEmpty()) return emptyList()
        val block = maxOf(1, (blockSeconds * sampleRate).toInt())
        val minSamples = (minSeconds * sampleRate).toInt()
        val maxSamples = (maxSeconds * sampleRate).toInt()
        val silenceBlocksNeeded = maxOf(1, (silenceSeconds / blockSeconds).toInt())

        val segments = ArrayList<FloatArray>()
        var start = 0
        var silentRun = 0
        var pos = 0
        while (pos < audio.size) {
            val end = minOf(pos + block, audio.size)
            silentRun = if (rms(audio, pos, end) < threshold) silentRun + 1 else 0
            val length = end - start
            val hitMax = length >= maxSamples
            val hitPause = length >= minSamples && silentRun >= silenceBlocksNeeded
            if (hitMax || hitPause) {
                segments.add(audio.copyOfRange(start, end))
                start = end
                silentRun = 0
            }
            pos = end
        }
        if (start < audio.size) segments.add(audio.copyOfRange(start, audio.size))
        return segments
    }
}

/**
 * Incremental form of [Streaming.splitOnSilence] for the live audio thread.
 * `feed()` only computes an RMS and compares counters; it returns true when
 * the caller should close the current segment.
 */
class SilenceSegmenter(
    sampleRate: Int = Config.SAMPLE_RATE,
    minSeconds: Double = Streaming.SEGMENT_MIN_SECONDS,
    maxSeconds: Double = Streaming.SEGMENT_MAX_SECONDS,
    silenceSeconds: Double = Streaming.SEGMENT_SILENCE_SECONDS,
    private val threshold: Double = Streaming.SILENCE_RMS,
) {
    private val minSamples = (minSeconds * sampleRate).toInt()
    private val maxSamples = (maxSeconds * sampleRate).toInt()
    private val silenceSamplesNeeded = (silenceSeconds * sampleRate).toInt()
    private var length = 0
    private var silentSamples = 0

    fun reset() {
        length = 0
        silentSamples = 0
    }

    /** Account for one audio block. True means "close the segment now". */
    fun feed(block: FloatArray, count: Int = block.size): Boolean {
        length += count
        if (Streaming.rms(block, 0, count) < threshold) silentSamples += count else silentSamples = 0
        if (length >= maxSamples) { reset(); return true }
        if (length >= minSamples && silentSamples >= silenceSamplesNeeded) { reset(); return true }
        return false
    }
}

/** What a session decodes with. Only ever called from the session's own threads. */
fun interface SegmentTranscriber {
    fun transcribe(audio: FloatArray): String
}

/**
 * Transcribes segments in the background while the user keeps talking.
 *
 * One worker, strictly in order — segments are joined in the order they were
 * spoken, and a single model has nothing to gain from contention. Every
 * failure path is recorded rather than thrown: [complete] tells the caller
 * whether the joined text is the whole clip, and [attempted] whether any
 * decode ran at all, so silence can be told from failure and the whole buffer
 * re-decoded only when that is actually the right thing to do.
 */
class SegmentedSession(
    private val transcriber: SegmentTranscriber,
    private val onProgress: ((done: Int, total: Int) -> Unit)? = null,
) {
    private val queue = LinkedBlockingQueue<Any>()
    private val parts = ArrayList<String>()
    private val lock = Any()
    private val cancelled = AtomicBoolean(false)
    private val pending = AtomicInteger(0)

    /** False if any segment failed or was still pending when finish() gave up. */
    @Volatile var complete = true
        private set

    /** Decoder calls this session made, counted whether or not they succeeded. */
    @Volatile var attempted = 0
        private set

    @Volatile var completed = 0
        private set

    @Volatile var queued = 0
        private set

    private val worker = Thread({ run() }, "say-segments").apply {
        isDaemon = true
        start()
    }

    private fun run() {
        while (true) {
            val item = queue.take()
            if (item === POISON) return
            try {
                if (!cancelled.get()) {
                    val text = decode(item as FloatArray)
                    // Checked again after the decode: a cancel that lands while
                    // the model is running must not let its result through.
                    if (text.isNotEmpty() && !cancelled.get()) synchronized(lock) { parts.add(text) }
                }
            } catch (e: Throwable) {
                complete = false
            } finally {
                // Progress first, then release finish(): once `pending` reads
                // zero every completed count is already in.
                advance()
                pending.decrementAndGet()
            }
        }
    }

    private fun decode(segment: FloatArray): String {
        synchronized(lock) { attempted += 1 }
        return transcriber.transcribe(segment).trim()
    }

    /** Queue a closed segment. Returns immediately. */
    fun push(segment: FloatArray) {
        if (cancelled.get()) return
        synchronized(lock) { queued += 1 }
        pending.incrementAndGet()
        queue.put(segment)
        report()
    }

    private fun advance() {
        synchronized(lock) { completed += 1 }
        report()
    }

    private fun report() {
        val cb = onProgress ?: return
        try {
            cb(completed, queued)
        } catch (e: Throwable) {
            // A UI that went away must never break the session.
        }
    }

    val progress: Pair<Int, Int> get() = completed to queued

    val segmentCount: Int get() = synchronized(lock) { parts.size }

    /**
     * Decode the tail, wait for earlier segments, and join everything. The
     * tail is decoded on the caller's thread — it is the only piece the user
     * is actually waiting on. Never waits past `timeoutMs`: a wedged worker
     * must not hold a dictation hostage, and whatever finished is returned.
     */
    fun finish(tail: FloatArray?, timeoutMs: Long = 180_000): String {
        val deadline = System.currentTimeMillis() + timeoutMs
        while (pending.get() > 0 && System.currentTimeMillis() < deadline) {
            Thread.sleep(5)
        }
        if (pending.get() > 0) complete = false

        var tailText = ""
        if (tail != null && tail.isNotEmpty()) {
            synchronized(lock) { queued += 1 }
            report()
            try {
                tailText = decode(tail)
            } catch (e: Throwable) {
                complete = false
            } finally {
                advance()
            }
        }
        val joined = synchronized(lock) { ArrayList(parts) }
        if (tailText.isNotEmpty()) joined.add(tailText)
        shutdown()
        return joined.filter { it.isNotEmpty() }.joinToString(" ").trim()
    }

    fun cancel() {
        cancelled.set(true)
        shutdown()
    }

    private fun shutdown() {
        queue.offer(POISON)
    }

    private companion object {
        val POISON = Any()
    }
}
