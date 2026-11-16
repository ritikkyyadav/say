package com.ritikyadav.say.glyph

import kotlin.math.PI
import kotlin.math.abs
import kotlin.math.ceil
import kotlin.math.cos
import kotlin.math.exp
import kotlin.math.log10
import kotlin.math.pow
import kotlin.math.sin
import kotlin.math.sqrt

/**
 * Audio → motion engine behind the dictation glyph.
 *
 * A direct port of the Mac engine's `waveform.py`, constants included — every
 * one of them was tuned by replaying the stored dictation corpus, not by eye,
 * and the tests in WaveformTest are the Python suite's, so the two glyphs
 * cannot drift apart in feel. Pure Kotlin: no View, no thread of its own.
 *
 *   [BandAnalyzer]  mic blocks → 12 normalised band levels + a voice gate, on
 *                   the audio thread; one small FFT per block, result handed
 *                   over by a single volatile assignment.
 *   [BarMotion]     band levels → bar heights, frame by frame. Every bar is
 *                   its own asymmetric spring, and neighbours are
 *                   diffusion-coupled, so the row settles like one membrane.
 *   [silhouette]    bar heights → the closed polygon of the glyph. One path,
 *                   not twelve rectangles: adjacent columns share an edge.
 */
object Waveform {
    // ── Analysis geometry ────────────────────────────────────────
    const val BAND_COUNT = 12
    const val SAMPLE_RATE = 16_000
    const val FFT_SIZE = 1024               // 64 ms @ 16 kHz — exactly one mic block
    const val FREQ_MIN = 90.0               // below this is rumble and mains hum
    const val FREQ_MAX = 5_200.0            // speech detail is gone well before Nyquist

    /** Speech rolls off ≈6 dB/octave above ~500 Hz; without this the right third never moves. */
    const val TILT_DB_PER_BAND = 3.0
    const val DYNAMIC_RANGE_DB = 21.0
    const val LEVEL_GAMMA = 1.45
    const val BAND_PEAK_MIX = 0.65

    // The display window runs from the room's noise floor up to a slowly
    // tracked estimate of how loudly this person actually speaks.
    const val SPEECH_ATTACK_DB_PER_S = 9.0
    const val SPEECH_RELEASE_DB_PER_S = 3.0
    const val SPEECH_MIN_SPAN_DB = 18.0
    const val SPEECH_MAX_SPAN_DB = 42.0
    const val SPEECH_SEED_OVER_FLOOR_DB = 24.0
    const val AGC_MIN_DB = -70.0
    const val AGC_MAX_DB = -3.0

    // Voice gate; both thresholds float with the room estimate.
    const val GATE_SHUT_OVER_NOISE_DB = 5.0
    const val GATE_OPEN_OVER_NOISE_DB = 14.0

    // The floor is a low quantile of the RMS seen in the last NOISE_WINDOW_S
    // seconds (minimum statistics) — a quantile, never the raw minimum, which
    // latches onto the stream's opening silence and pins the glyph.
    const val NOISE_WINDOW_S = 8.0
    const val DISPLAY_FLOOR_OVER_NOISE_DB = 2.0
    const val NOISE_TRACK_COEFF = 0.08
    const val NOISE_QUANTILE = 0.10
    const val NOISE_MIN_BIAS_DB = 0.0
    /** Below this is a dropout, not a room: a microphone always delivers some noise. */
    const val NOISE_IGNORE_BELOW_DB = -95.0
    const val NOISE_INIT_DB = -60.0
    const val NOISE_FLOOR_DB = -85.0
    const val NOISE_CEIL_DB = -32.0

    // ── Motion ───────────────────────────────────────────────────
    const val ATTACK_OMEGA = 46.0
    const val ATTACK_ZETA = 0.78
    const val RELEASE_OMEGA = 21.0
    const val RELEASE_ZETA = 1.0
    const val BOUNCE = 0.18
    const val COUPLING_PER_S = 8.0
    const val COUPLING_MAX_STEP = 0.45
    const val MAX_SUBSTEP = 1.0 / 120.0
    const val REST_HEIGHT = 0.055
    const val REST_HZ = 0.42
    const val REST_SKEW = 0.09
    const val SWEEP_BANDS_PER_S = 9.0
    const val SWEEP_WIDTH = 1.7
    const val SWEEP_HEIGHT = 0.42
    const val SWEEP_PAD = 3.0
    const val EDGE_TAPER_BANDS = 2.0
    const val EDGE_TAPER_FLOOR = 0.80

    /**
     * The Say mark, as bar heights: the six stepped columns of the logo on its
     * 30x12 grid (`gen_logo.py` owns that geometry), each column filling two of
     * the twelve bands so the row reads as the mark and not as a spectrum.
     *
     * This is what the floating bubble shows at rest. The logo has always been
     * a frozen frame of this overlay; here it is literally that frame. The
     * alternative — the Mac's barely-there resting ripple — is right for a
     * glyph that only appears while you dictate, and wrong for a control you
     * have to find and tap: it drew a one-pixel line in an empty pill.
     */
    val MARK_PROFILE: DoubleArray = doubleArrayOf(
        3.25, 3.25, 7.0, 7.0, 12.0, 12.0, 9.75, 9.75, 7.0, 7.0, 3.25, 3.25,
    ).map { it / 12.0 }.toDoubleArray()

    const val EPS = 1e-12
    const val TAU = PI * 2.0

    fun clamp01(value: Double): Double = if (value < 0.0) 0.0 else if (value > 1.0) 1.0 else value

    fun smoothstep(edge0: Double, edge1: Double, x: Double): Double {
        if (edge1 <= edge0) return if (x >= edge1) 1.0 else 0.0
        val t = clamp01((x - edge0) / (edge1 - edge0))
        return t * t * (3.0 - 2.0 * t)
    }

    /**
     * Log-spaced (binLo, binHi) pairs, guaranteed strictly increasing. Log
     * spacing is what makes the row read as *pitch*.
     */
    fun bandEdges(
        bands: Int = BAND_COUNT,
        fftSize: Int = FFT_SIZE,
        sampleRate: Int = SAMPLE_RATE,
        fmin: Double = FREQ_MIN,
        fmax: Double = FREQ_MAX,
    ): List<Pair<Int, Int>> {
        val nyquist = sampleRate / 2.0
        val top = minOf(fmax, nyquist * 0.98)
        val bottom = maxOf(fmin, sampleRate.toDouble() / fftSize)
        val binHz = sampleRate.toDouble() / fftSize
        val ratio = top / bottom
        val cuts = IntArray(bands + 1)
        var previous = 0
        for (k in 0..bands) {
            val freq = bottom * ratio.pow(k.toDouble() / bands)
            // Math.rint is round-half-even, which is what Python's round() does.
            val cut = maxOf(Math.rint(freq / binHz).toInt(), previous + 1, 1)
            cuts[k] = cut
            previous = cut
        }
        return (0 until bands).map { cuts[it] to cuts[it + 1] }
    }

    internal fun edgeTaper(bands: Int): DoubleArray = DoubleArray(bands) { i ->
        val distance = minOf(i, bands - 1 - i)
        val t = clamp01((distance + 0.5) / EDGE_TAPER_BANDS)
        EDGE_TAPER_FLOOR + (1.0 - EDGE_TAPER_FLOOR) * (t * t * (3.0 - 2.0 * t))
    }
}

/** Iterative radix-2 FFT, forward, in place. `n` must be a power of two. */
internal class Fft(private val n: Int) {
    private val cosTable = DoubleArray(n / 2) { cos(-2.0 * PI * it / n) }
    private val sinTable = DoubleArray(n / 2) { sin(-2.0 * PI * it / n) }
    private val reversed = IntArray(n).also { table ->
        var bits = 0
        while ((1 shl bits) < n) bits++
        for (i in 0 until n) table[i] = Integer.reverse(i) ushr (32 - bits)
    }

    init {
        require(n > 1 && (n and (n - 1)) == 0) { "FFT size must be a power of two" }
    }

    fun forward(re: DoubleArray, im: DoubleArray) {
        for (i in 0 until n) {
            val j = reversed[i]
            if (j > i) {
                val tr = re[i]; re[i] = re[j]; re[j] = tr
                val ti = im[i]; im[i] = im[j]; im[j] = ti
            }
        }
        var len = 2
        while (len <= n) {
            val half = len / 2
            val step = n / len
            var i = 0
            while (i < n) {
                for (k in 0 until half) {
                    val wr = cosTable[k * step]
                    val wi = sinTable[k * step]
                    val a = i + k
                    val b = a + half
                    val tr = re[b] * wr - im[b] * wi
                    val ti = re[b] * wi + im[b] * wr
                    re[b] = re[a] - tr
                    im[b] = im[a] - ti
                    re[a] += tr
                    im[a] += ti
                }
                i += len
            }
            len *= 2
        }
    }
}

/** Latest (band levels, voice gate). Immutable, so a reader never sees a torn frame. */
class BandSnapshot(val levels: DoubleArray, val gate: Double) {
    override fun equals(other: Any?): Boolean =
        other is BandSnapshot && gate == other.gate && levels.contentEquals(other.levels)

    override fun hashCode(): Int = 31 * levels.contentHashCode() + gate.hashCode()
}

/**
 * Microphone blocks → normalised per-band levels and a voice gate.
 *
 * [feed] is called from the capture thread and must stay cheap: one
 * 1024-point FFT plus a dozen slice-means. [snapshot] is called from the render
 * loop; the handover is a single reference assignment, so neither side waits.
 */
class BandAnalyzer(
    val bands: Int = Waveform.BAND_COUNT,
    val sampleRate: Int = Waveform.SAMPLE_RATE,
    val fftSize: Int = Waveform.FFT_SIZE,
    fmin: Double = Waveform.FREQ_MIN,
    fmax: Double = Waveform.FREQ_MAX,
) {
    private val window = DoubleArray(fftSize) { 0.5 - 0.5 * cos(2.0 * PI * it / (fftSize - 1)) }
    // Normalise so a full-scale sinusoid reads 0 dB — keeps every threshold in honest dBFS.
    private val magScale = 2.0 / maxOf(window.sum(), Waveform.EPS)
    private val edges = Waveform.bandEdges(bands, fftSize, sampleRate, fmin, fmax)
    private val tilt = DoubleArray(bands) { it * Waveform.TILT_DB_PER_BAND }
    private val fft = Fft(fftSize)
    private var tail = DoubleArray(fftSize)
    private val re = DoubleArray(fftSize)
    private val im = DoubleArray(fftSize)
    private val bandDb = DoubleArray(bands)

    /** Slowly tracked ceiling of how loudly this person speaks; null until they do. */
    internal var speechDb: Double? = null
        private set

    internal var noiseDb = Waveform.NOISE_INIT_DB
        private set

    private var noiseSeeded = false
    private var gate = 0.0
    private val windowCapacity = maxOf(2, (Waveform.NOISE_WINDOW_S * Waveform.SAMPLE_RATE / Waveform.FFT_SIZE).toInt())
    private val rmsWindow = ArrayDeque<Double>(windowCapacity)

    @Volatile
    private var latest = BandSnapshot(DoubleArray(bands), 0.0)

    // ── audio thread ─────────────────────────────────────────────

    /** Absorb one mic block. Never throws — this runs on the capture thread. */
    fun feed(block: FloatArray?, count: Int = block?.size ?: 0) {
        try {
            if (block == null || count <= 0) return
            val n = minOf(count, block.size)
            if (n <= 0) return
            val dt = n.toDouble() / sampleRate

            // The caller owns `block` and reuses its buffer: everything kept
            // past this call is a copy into our own arrays.
            val buf = DoubleArray(fftSize)
            if (n >= fftSize) {
                for (i in 0 until fftSize) buf[i] = block[n - fftSize + i].toDouble()
            } else {
                val keep = fftSize - n
                System.arraycopy(tail, n, buf, 0, keep)
                for (i in 0 until n) buf[keep + i] = block[i].toDouble()
            }
            tail = buf

            for (i in 0 until fftSize) {
                re[i] = buf[i] * window[i]
                im[i] = 0.0
            }
            fft.forward(re, im)

            var loudest = Waveform.AGC_MIN_DB
            val mix = Waveform.BAND_PEAK_MIX
            for (k in 0 until bands) {
                val (lo, hi) = edges[k]
                var sum = 0.0
                var peak = 0.0
                var bins = 0
                for (b in lo until minOf(hi, fftSize / 2 + 1)) {
                    val mag = sqrt(re[b] * re[b] + im[b] * im[b]) * magScale
                    val p = mag * mag
                    sum += p
                    if (p > peak) peak = p
                    bins++
                }
                val energy = if (bins > 0) (1.0 - mix) * (sum / bins) + mix * peak else 0.0
                val db = 10.0 * log10(energy + Waveform.EPS) + tilt[k]
                bandDb[k] = db
                if (db > loudest) loudest = db
            }

            // The room and the gate come first: the display window is anchored
            // to them, so they have to be current before any level is computed.
            var acc = 0.0
            for (i in 0 until fftSize) acc += buf[i] * buf[i]
            val rms = sqrt(acc / fftSize)
            val rmsDb = 20.0 * log10(rms + Waveform.EPS)
            if (rmsDb > Waveform.NOISE_IGNORE_BELOW_DB) {
                if (rmsWindow.size >= windowCapacity) rmsWindow.removeFirst()
                rmsWindow.addLast(rmsDb)
            }
            if (rmsWindow.isNotEmpty()) {
                val ranked = rmsWindow.sorted()
                val index = (Waveform.NOISE_QUANTILE * (ranked.size - 1)).toInt()
                val target = ranked[index] + Waveform.NOISE_MIN_BIAS_DB
                if (noiseSeeded) {
                    noiseDb += (target - noiseDb) * Waveform.NOISE_TRACK_COEFF
                } else {
                    // Nothing measured yet, so there is no estimate to ease away
                    // from — NOISE_INIT_DB is a placeholder, not a prior.
                    noiseSeeded = true
                    noiseDb = target
                }
                noiseDb = minOf(maxOf(noiseDb, Waveform.NOISE_FLOOR_DB), Waveform.NOISE_CEIL_DB)
            }

            val g = Waveform.smoothstep(
                noiseDb + Waveform.GATE_SHUT_OVER_NOISE_DB,
                noiseDb + Waveform.GATE_OPEN_OVER_NOISE_DB,
                rmsDb,
            )

            val floorDb = noiseDb + Waveform.DISPLAY_FLOOR_OVER_NOISE_DB
            var speech = speechDb ?: (floorDb + Waveform.SPEECH_SEED_OVER_FLOOR_DB)

            // Track the ceiling ONLY while a voice is present.
            if (g > 0.5) {
                val delta = loudest - speech
                if (delta > 0.0) speech += minOf(Waveform.SPEECH_ATTACK_DB_PER_S * dt, delta)
                else speech -= minOf(Waveform.SPEECH_RELEASE_DB_PER_S * dt, -delta)
            }
            speech = minOf(maxOf(speech, Waveform.AGC_MIN_DB), Waveform.AGC_MAX_DB)
            speechDb = speech

            val span = minOf(maxOf(speech - floorDb, Waveform.SPEECH_MIN_SPAN_DB), Waveform.SPEECH_MAX_SPAN_DB)
            val levels = DoubleArray(bands) { k ->
                Waveform.clamp01((bandDb[k] - floorDb) / span).pow(Waveform.LEVEL_GAMMA)
            }
            gate = g
            latest = BandSnapshot(levels, g)
        } catch (e: Throwable) {
            // Visualisation can never be allowed to break capture.
        }
    }

    // ── render thread ────────────────────────────────────────────

    fun snapshot(): BandSnapshot = latest

    /** Start a new dictation: drop the stale reference level, keep the room. */
    fun rearm() {
        tail = DoubleArray(fftSize)
        speechDb = null
        gate = 0.0
        latest = BandSnapshot(DoubleArray(bands), 0.0)
    }

    /** Forget the room and the speaker both. */
    fun reset() {
        rearm()
        noiseDb = Waveform.NOISE_INIT_DB
        noiseSeeded = false
        rmsWindow.clear()
    }
}

/** A single damped scalar — used for the glyph's reveal. Substeps so a dropped frame cannot make it explode. */
class Spring(value: Double = 0.0, var omega: Double = 40.0, var zeta: Double = 1.0) {
    var value = value
    var velocity = 0.0
    var target = value

    fun snap(v: Double) {
        value = v; target = v; velocity = 0.0
    }

    fun tick(dtIn: Double): Double {
        val dt = minOf(maxOf(dtIn, 1e-5), 0.25)
        val steps = maxOf(1, ceil(dt / Waveform.MAX_SUBSTEP).toInt())
        val h = dt / steps
        repeat(steps) {
            var accel = (target - value) * omega * omega
            accel -= 2.0 * zeta * omega * velocity
            velocity += accel * h
            value += velocity * h
        }
        return value
    }

    val settled: Boolean get() = abs(value - target) < 1e-3 && abs(velocity) < 1e-2
}

/**
 * Band levels → bar heights, as one coupled membrane.
 *
 * States: [REST] armed and silent (a barely-there travelling ripple),
 * [LISTEN] driven by the spectrum, [WORK] a single hump sweeping the row
 * while the model decodes.
 */
class BarMotion(val bands: Int = Waveform.BAND_COUNT) {
    enum class State {
        /** Armed and silent: a barely-there travelling ripple. */
        REST,

        /** Idle but on screen: the Say mark, held. */
        MARK,

        /** Driven by the spectrum. */
        LISTEN,

        /** A single hump sweeping the row while the model decodes. */
        WORK,
    }

    private var levels = DoubleArray(bands)
    private var velocity = DoubleArray(bands)
    private val taper = Waveform.edgeTaper(bands)
    private var t = 0.0

    fun reset() {
        levels = DoubleArray(bands)
        velocity = DoubleArray(bands)
        t = 0.0
    }

    val heights: DoubleArray get() = levels.copyOf()

    fun tick(dtIn: Double, input: DoubleArray? = null, gain: Double = 0.0, state: State = State.LISTEN): DoubleArray {
        val dt = minOf(maxOf(dtIn, 1e-5), 0.25)
        val steps = maxOf(1, ceil(dt / Waveform.MAX_SUBSTEP).toInt())
        val h = dt / steps
        repeat(steps) { advance(h, input, gain, state) }
        return levels.copyOf()
    }

    private fun advance(dt: Double, input: DoubleArray?, gain: Double, state: State) {
        t += dt
        val targets = targets(input, gain, state)
        for (i in 0 until bands) {
            val target = targets[i]
            var level = levels[i]
            val omega: Double
            val zeta: Double
            if (target > level) { omega = Waveform.ATTACK_OMEGA; zeta = Waveform.ATTACK_ZETA }
            else { omega = Waveform.RELEASE_OMEGA; zeta = Waveform.RELEASE_ZETA }
            var accel = (target - level) * omega * omega
            accel -= 2.0 * zeta * omega * velocity[i]
            var v = velocity[i] + accel * dt
            level += v * dt
            if (level < 0.0) {
                level = 0.0
                if (v < 0.0) v = -v * Waveform.BOUNCE
            } else if (level > 1.0) {
                level = 1.0
                if (v > 0.0) v = -v * Waveform.BOUNCE
            }
            levels[i] = level
            velocity[i] = v
        }
        // Membrane coupling: each bar is tugged toward the average of its
        // neighbours. Not while the mark is held — the logo is a specific
        // shape with six crisp steps, and diffusion rounds it into a hill.
        val k = if (state == State.MARK) 0.0
        else minOf(Waveform.COUPLING_PER_S * dt, Waveform.COUPLING_MAX_STEP)
        if (k > 0.0) {
            val previous = levels
            val coupled = DoubleArray(bands)
            val last = bands - 1
            for (i in 0 until bands) {
                val left = if (i > 0) previous[i - 1] else previous[i]
                val right = if (i < last) previous[i + 1] else previous[i]
                coupled[i] = previous[i] + k * (left + right - 2.0 * previous[i])
            }
            levels = coupled
        }
    }

    private fun targets(input: DoubleArray?, gain: Double, state: State): DoubleArray {
        if (state == State.MARK) return Waveform.MARK_PROFILE
        if (state == State.WORK) {
            val span = bands + 2.0 * Waveform.SWEEP_PAD
            val position = (t * Waveform.SWEEP_BANDS_PER_S) % span - Waveform.SWEEP_PAD
            val width = 2.0 * Waveform.SWEEP_WIDTH * Waveform.SWEEP_WIDTH
            return DoubleArray(bands) { i -> Waveform.SWEEP_HEIGHT * exp(-((i - position) * (i - position)) / width) }
        }
        val rest = DoubleArray(bands) { i ->
            Waveform.REST_HEIGHT * (0.5 + 0.5 * sin(Waveform.TAU * (t * Waveform.REST_HZ - i * Waveform.REST_SKEW)))
        }
        if (state == State.REST || input == null || input.isEmpty()) return rest
        val g = Waveform.clamp01(gain)
        return DoubleArray(bands) { i ->
            val level = if (i < input.size) Waveform.clamp01(input[i]) else 0.0
            val voice = level * g * taper[i]
            // The ripple survives only in the gap the voice leaves behind.
            maxOf(voice, rest[i] * (1.0 - g))
        }
    }
}

/**
 * Points, on screen, for one drawn glyph. Columns are gapless by design: the
 * mark is a single stepped silhouette standing on a continuous base.
 */
data class GlyphGeometry(
    val bands: Int = Waveform.BAND_COUNT,
    val columnWidth: Double = 7.5,
    val baseHeight: Double = 2.5,
    val barHeight: Double = 16.5,
    val collapsedScale: Double = 0.24,
) {
    val width: Double get() = bands * columnWidth
    val height: Double get() = baseHeight + barHeight
}

/**
 * The whole glyph as one closed polygon, bottom-left → around → close, as
 * interleaved (x, y) pairs. `reveal` (0–1) scales the shape horizontally
 * about its centre *and* scales the columns down into the base; `snap`
 * quantises x to a device pixel so the vertical steps stay crisp, while the
 * tops are deliberately left unsnapped so heights stay sub-pixel smooth.
 */
fun silhouette(
    levels: DoubleArray,
    geometry: GlyphGeometry,
    reveal: Double = 1.0,
    originX: Double = 0.0,
    originY: Double = 0.0,
    snap: Double = 0.0,
): DoubleArray {
    val centre = originX + geometry.width / 2.0
    val scale = geometry.collapsedScale + (1.0 - geometry.collapsedScale) * Waveform.clamp01(reveal)
    fun sx(x: Double): Double {
        var v = centre + (x - centre) * scale
        if (snap > 0.0) v = Math.rint(v / snap) * snap
        return v
    }
    val rise = Waveform.clamp01(reveal) * geometry.barHeight
    val out = DoubleArray(2 * (2 * geometry.bands + 2))
    var p = 0
    out[p++] = sx(originX); out[p++] = originY
    for (i in 0 until geometry.bands) {
        val level = if (i < levels.size) Waveform.clamp01(levels[i]) else 0.0
        val top = originY + geometry.baseHeight + level * rise
        out[p++] = sx(originX + i * geometry.columnWidth); out[p++] = top
        out[p++] = sx(originX + (i + 1) * geometry.columnWidth); out[p++] = top
    }
    out[p++] = sx(originX + geometry.width); out[p] = originY
    return out
}
