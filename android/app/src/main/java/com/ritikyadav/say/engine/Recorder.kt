package com.ritikyadav.say.engine

import android.annotation.SuppressLint
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.util.Log
import java.util.concurrent.atomic.AtomicBoolean

/** Where samples come from. The microphone in the app; a WAV file under the debug replay hook. */
interface AudioSource {
    /** Open the device. False means it could not be opened (permission, busy, absent). */
    fun open(): Boolean

    /**
     * Blocking read of up to `count` 16-bit mono samples at 16 kHz. Returns
     * samples read, or -1 at the end of a finite stream (a replayed file).
     * A device that stops delivering throws instead — a microphone has no
     * end of stream, and "the mic failed" must never read as "nothing heard".
     */
    @Throws(java.io.IOException::class)
    fun read(buffer: ShortArray, count: Int): Int

    fun close()
}

/** 16 kHz mono PCM from the microphone, with the platform's speech-recognition tuning. */
class MicSource : AudioSource {
    private var record: AudioRecord? = null

    @SuppressLint("MissingPermission") // The caller checks RECORD_AUDIO; a refused open reports false.
    override fun open(): Boolean {
        val minBuffer = AudioRecord.getMinBufferSize(
            Config.SAMPLE_RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT
        )
        if (minBuffer <= 0) return false
        // VOICE_RECOGNITION asks the platform for a clean, unprocessed feed
        // (no aggressive AGC or noise suppression) — what a speech model wants.
        val r = try {
            AudioRecord(
                MediaRecorder.AudioSource.VOICE_RECOGNITION,
                Config.SAMPLE_RATE,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT,
                maxOf(minBuffer, Config.BLOCK_SAMPLES * 2 * 8),
            )
        } catch (e: Exception) {
            Log.w(TAG, "AudioRecord refused", e)
            return false
        }
        if (r.state != AudioRecord.STATE_INITIALIZED) {
            r.release()
            return false
        }
        r.startRecording()
        if (r.recordingState != AudioRecord.RECORDSTATE_RECORDING) {
            r.release()
            return false
        }
        record = r
        return true
    }

    override fun read(buffer: ShortArray, count: Int): Int {
        val r = record ?: throw java.io.IOException("microphone closed")
        val n = r.read(buffer, 0, count, AudioRecord.READ_BLOCKING)
        if (n < 0) throw java.io.IOException("microphone read failed (AudioRecord error $n)")
        return n
    }

    override fun close() {
        record?.let { r ->
            runCatching { r.stop() }
            runCatching { r.release() }
        }
        record = null
    }

    private companion object { const val TAG = "Say.Mic" }
}

/** Everything a stopped recording hands back. */
class Capture(
    /** Audio since the last closed segment — the only piece still to decode. */
    val tail: FloatArray,
    /** The whole clip, for the fallback when a background segment failed. */
    val full: FloatArray,
    /** Seconds the source actually delivered. */
    val liveSeconds: Double,
    /** Why capture stopped early, if the device failed rather than the user stopping it. */
    val failure: String? = null,
)

/**
 * 16 kHz mono capture, one 64 ms block at a time, on a thread of its own.
 *
 * Each block goes three places: the monitor (the glyph — every block, so the
 * mark is already moving before capture is confirmed), the silence segmenter
 * (which closes a segment at a natural pause and hands it to
 * [segmentCallback] for background decoding), and the clip buffer. Mirrors
 * the shape of the Mac's `audio.py`, minus the CoreAudio reopen dance that
 * has no equivalent in AudioRecord.
 */
class Recorder(private val sourceFactory: () -> AudioSource = { MicSource() }) {
    /** Fires for every block on the capture thread. Must not retain the buffer. */
    @Volatile var monitor: ((FloatArray, Int) -> Unit)? = null

    /** A segment closed at a pause. Receives its own copy. Capture thread. */
    @Volatile var segmentCallback: ((FloatArray) -> Unit)? = null

    /** The safety cap was reached; the caller should stop. Capture thread. */
    @Volatile var onMaxReached: (() -> Unit)? = null

    /** The source has no more audio (a replayed file, a device that went away). Capture thread. */
    @Volatile var onSourceEnded: (() -> Unit)? = null

    private val running = AtomicBoolean(false)
    private var thread: Thread? = null
    private var source: AudioSource? = null
    private val lock = Any()
    private var clip = FloatBuffer()
    private var segmentStart = 0
    private var delivered = 0L
    private var maxFired = false
    private var streaming = true
    @Volatile private var failure: String? = null

    val isRunning: Boolean get() = running.get()

    /** Seconds the source has delivered so far. */
    val liveSeconds: Double get() = delivered.toDouble() / Config.SAMPLE_RATE

    /**
     * Open the source and start capturing. Returns false if the device could
     * not be opened; nothing is left running in that case.
     */
    fun start(streaming: Boolean = true): Boolean {
        synchronized(lock) {
            if (running.get()) return true
            val src = sourceFactory()
            if (!src.open()) {
                src.close()
                return false
            }
            source = src
            clip = FloatBuffer()
            segmentStart = 0
            delivered = 0
            maxFired = false
            failure = null
            this.streaming = streaming
            running.set(true)
            thread = Thread({ loop(src) }, "say-capture").apply { start() }
            return true
        }
    }

    private fun loop(src: AudioSource) {
        val shorts = ShortArray(Config.BLOCK_SAMPLES)
        val floats = FloatArray(Config.BLOCK_SAMPLES)
        val segmenter = SilenceSegmenter()
        while (running.get()) {
            val n = try {
                src.read(shorts, shorts.size)
            } catch (e: Exception) {
                if (running.get()) {
                    failure = e.message ?: e.javaClass.simpleName
                    Log.w(TAG, "capture stopped: $failure")
                    onSourceEnded?.let { cb -> runCatching { cb() } }
                }
                break
            }
            if (n < 0) {
                if (running.get()) onSourceEnded?.let { cb -> runCatching { cb() } }
                break
            }
            if (n == 0) continue
            for (i in 0 until n) floats[i] = shorts[i] / 32768f
            monitor?.let { m -> runCatching { m(floats, n) } }
            synchronized(lock) {
                if (!running.get()) return@synchronized
                clip.append(floats, n)
                delivered += n
                if (streaming && segmenter.feed(floats, n)) {
                    val segment = clip.copyRange(segmentStart, clip.size)
                    segmentStart = clip.size
                    segmentCallback?.let { cb -> runCatching { cb(segment) } }
                }
                if (!maxFired && delivered >= Config.MAX_RECORDING_SECONDS.toLong() * Config.SAMPLE_RATE) {
                    maxFired = true
                    onMaxReached?.let { cb -> runCatching { cb() } }
                }
            }
        }
        running.set(false)
    }

    /** Stop capturing and return what was heard. Safe to call twice. */
    fun stop(): Capture {
        val t: Thread?
        val src: AudioSource?
        synchronized(lock) {
            running.set(false)
            t = thread
            src = source
            thread = null
            source = null
        }
        src?.close()
        t?.join(2000)
        synchronized(lock) {
            val tail = clip.copyRange(segmentStart, clip.size)
            val full = clip.copyRange(0, clip.size)
            return Capture(tail, full, liveSeconds, failure)
        }
    }

    fun cancel() {
        stop()
    }

    companion object {
        private const val TAG = "Say.Recorder"

        /**
         * Debug-only replay hook: when set, the next recording reads from this
         * source instead of the microphone. Installed by the debug build's
         * replay receiver so a stored dictation can be pushed through the whole
         * pipeline on an emulator — the Android `replay_bench`.
         */
        @Volatile var debugSourceFactory: (() -> AudioSource)? = null
    }

    /** A growable float buffer — the clip, appended one block at a time. */
    private class FloatBuffer {
        private var data = FloatArray(Config.SAMPLE_RATE * 8)
        var size = 0
            private set

        fun append(block: FloatArray, count: Int) {
            if (size + count > data.size) {
                data = data.copyOf(maxOf(data.size * 2, size + count))
            }
            System.arraycopy(block, 0, data, size, count)
            size += count
        }

        fun copyRange(from: Int, to: Int): FloatArray = data.copyOfRange(from, to)
    }
}
