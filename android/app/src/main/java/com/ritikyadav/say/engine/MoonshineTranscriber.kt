package com.ritikyadav.say.engine

import ai.moonshine.voice.JNI
import ai.moonshine.voice.Transcriber
import android.util.Log
import com.ritikyadav.say.BuildConfig
import java.io.File

/**
 * The speech-to-text engine: Moonshine Voice, the same runtime the Mac engine
 * loads through `moonshine_voice`, here as the official Android library.
 *
 * Batch decode per segment (`transcribeWithoutStreaming`), exactly as the Mac
 * does — the streaming architectures accept it and it keeps the pipeline one
 * thing on both platforms. The hallucination filter is applied to every result.
 */
class MoonshineTranscriber : SegmentTranscriber {
    private var engine: Transcriber? = null
    private val lock = Any()

    /** Catalogue id of the loaded model, or null. */
    @Volatile var modelId: String? = null
        private set

    @Volatile var arch: Int = -1
        private set

    val isLoaded: Boolean get() = engine != null

    /** Load `dir` (the model's component files) as `arch`. Replaces any loaded model. */
    fun load(modelId: String, dir: File, arch: Int) {
        synchronized(lock) {
            close()
            val t = Transcriber()
            t.loadFromFiles(dir.absolutePath, arch)
            engine = t
            this.modelId = modelId
            this.arch = arch
        }
    }

    fun close() {
        synchronized(lock) {
            engine?.let { runCatching { it.close() } }
            engine = null
            modelId = null
            arch = -1
        }
    }

    /**
     * Bias the decoder toward the user's vocabulary. Only the streaming
     * architectures decode through a path that can apply it; on any other
     * model this is a no-op, and the fuzzy repair stage still runs afterwards.
     */
    fun setKeyterms(terms: List<String>) {
        synchronized(lock) {
            val t = engine ?: return
            if (!isStreamingArch(arch)) return
            try {
                t.setKeyterms(terms)
            } catch (e: RuntimeException) {
                Log.w(TAG, "key terms not applied: ${e.message}")
            }
        }
    }

    override fun transcribe(audio: FloatArray): String {
        // Digital silence must never reach the model. Fed thirty seconds of
        // exact zeros (an emulator with no host microphone; on a phone, a
        // dropout block), the streaming architecture's internal state went bad
        // and every decode afterwards came back empty until the model was
        // reloaded. A microphone always delivers some noise, so an all-zero
        // clip carries nothing worth decoding anyway.
        if (isSilent(audio)) return ""
        synchronized(lock) {
            val t = engine ?: throw IllegalStateException("no model loaded")
            val t0 = System.nanoTime()
            val transcript = t.transcribeWithoutStreaming(audio, Config.SAMPLE_RATE)
            val text = transcript.lines.orEmpty()
                .joinToString(" ") { it.text ?: "" }
                .trim()
            noteRealtime(audio.size.toDouble() / Config.SAMPLE_RATE, (System.nanoTime() - t0) / 1e9)
            // Transcript text only ever reaches the log in a debug build.
            if (BuildConfig.DEBUG) Log.d(TAG, "segment: $text")
            return if (Hallucinations.isHallucination(text)) "" else text
        }
    }

    companion object {
        private const val TAG = "Say.Transcriber"

        /** A decode slower than real time is the tell for a throttled phone — say so in the log. */
        const val SLOW_RTF = 1.0
        private const val SLOW_MIN_AUDIO_SECONDS = 2.0

        /** Below this peak the clip is a dropout, not a room: −80 dBFS. */
        const val SILENT_PEAK = 1e-4f

        fun isSilent(audio: FloatArray): Boolean {
            var peak = 0f
            for (v in audio) {
                val a = if (v < 0) -v else v
                if (a > peak) peak = a
                if (peak >= SILENT_PEAK) return false
            }
            return true
        }

        fun isStreamingArch(arch: Int): Boolean = arch == JNI.MOONSHINE_MODEL_ARCH_TINY_STREAMING ||
            arch == JNI.MOONSHINE_MODEL_ARCH_BASE_STREAMING ||
            arch == JNI.MOONSHINE_MODEL_ARCH_SMALL_STREAMING ||
            arch == JNI.MOONSHINE_MODEL_ARCH_MEDIUM_STREAMING

        fun noteRealtime(audioSeconds: Double, elapsed: Double): Double {
            if (audioSeconds <= 0) return 0.0
            val rtf = elapsed / audioSeconds
            if (audioSeconds >= SLOW_MIN_AUDIO_SECONDS && rtf > SLOW_RTF) {
                Log.w(TAG, "decode slower than real time: %.1fs of audio took %.1fs (rtf %.2f)".format(audioSeconds, elapsed, rtf))
            } else {
                Log.i(TAG, "decoded %.1fs in %.2fs (rtf %.2f)".format(audioSeconds, elapsed, rtf))
            }
            return rtf
        }
    }
}
