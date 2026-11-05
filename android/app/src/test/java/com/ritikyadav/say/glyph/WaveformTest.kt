package com.ritikyadav.say.glyph

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.Random
import kotlin.math.PI
import kotlin.math.abs
import kotlin.math.round
import kotlin.math.sin

/**
 * The Mac suite (say/tests/test_waveform.py) against the Kotlin port. Everything
 * that decides how the glyph *feels* is pure and lives here; only the View is not.
 */
class WaveformTest {
    private val rate = Waveform.SAMPLE_RATE
    private val block = Waveform.FFT_SIZE

    private fun tone(freq: Double, amplitude: Double = 0.25, samples: Int = block) =
        FloatArray(samples) { (amplitude * sin(2 * PI * freq * it / rate)).toFloat() }

    /** Broadband room tone — what the mic hears when nobody is talking. */
    private fun hiss(amplitude: Double = 0.002, samples: Int = block, seed: Long = 7): FloatArray {
        val rng = Random(seed)
        return FloatArray(samples) { (amplitude * rng.nextGaussian()).toFloat() }
    }

    /**
     * Blocks of speech-shaped bursts laid over a room-tone bed. A steady sine
     * has no pauses and no dynamics, and passed happily against the inverted
     * build; real speech is bursts separated by gaps on top of a room.
     */
    private fun speechLike(level: Double, seconds: Double = 6.0, room: Double = 0.0015, seed: Long = 3): List<FloatArray> {
        val rng = Random(seed)
        val blocks = ArrayList<FloatArray>()
        val count = (seconds * rate / block).toInt()
        for (i in 0 until count) {
            val out = DoubleArray(block) { room * rng.nextGaussian() }
            if (i % 8 < 5) {                       // ~320 ms on, ~190 ms off
                val voice = DoubleArray(block) { rng.nextGaussian() }
                // np.convolve(voice, ones(8)/8, 'same'): mean of voice[i-4..i+3], zero padded.
                for (n in 0 until block) {
                    var acc = 0.0
                    for (k in n - 4..n + 3) if (k in 0 until block) acc += voice[k]
                    out[n] += level * acc / 8.0
                }
            }
            blocks.add(FloatArray(block) { out[it].toFloat() })
        }
        return blocks
    }

    /** Visual mass of the glyph per block, gate included — the mean, not the max. */
    private fun displayed(analyzer: BandAnalyzer, blocks: List<FloatArray>): List<Double> =
        blocks.map { b ->
            analyzer.feed(b)
            val s = analyzer.snapshot()
            s.levels.average() * s.gate
        }

    private fun drive(analyzer: BandAnalyzer, b: FloatArray, blocks: Int = 12): BandSnapshot {
        repeat(blocks) { analyzer.feed(b) }
        return analyzer.snapshot()
    }

    // ── band edges ───────────────────────────────────────────────

    @Test fun edgesAreStrictlyIncreasing() {
        val edges = Waveform.bandEdges()
        assertEquals(Waveform.BAND_COUNT, edges.size)
        for ((lo, hi) in edges) assertTrue(lo < hi)
        for ((a, b) in edges.zipWithNext()) assertEquals(a.second, b.first)
    }

    @Test fun edgesStayInsideTheSpectrum() {
        val edges = Waveform.bandEdges()
        assertTrue(edges.first().first >= 1)
        assertTrue(edges.last().second <= block / 2 + 1)
    }

    @Test fun narrowBandCountStillGivesEveryBandABin() {
        val edges = Waveform.bandEdges(bands = 40, fmin = 90.0, fmax = 400.0)
        assertEquals(40, edges.size)
        for ((lo, hi) in edges) assertTrue(lo < hi)
    }

    // ── analyzer ─────────────────────────────────────────────────

    @Test fun silenceLeavesTheGateShut() {
        val s = drive(BandAnalyzer(), FloatArray(block), blocks = 30)
        assertTrue(s.gate < 0.05)
        assertTrue(s.levels.max() <= 1.0)
    }

    @Test fun speechLevelToneOpensTheGate() {
        assertTrue(drive(BandAnalyzer(), tone(400.0, 0.2), blocks = 30).gate > 0.9)
    }

    @Test fun aToneLightsItsOwnBand() {
        val edges = Waveform.bandEdges()
        val binHz = rate.toDouble() / block
        for (freq in listOf(220.0, 900.0, 3000.0)) {
            val levels = drive(BandAnalyzer(), tone(freq, 0.2)).levels
            val expected = edges.indexOfFirst { (lo, hi) -> lo * binHz <= freq && freq < hi * binHz }
            val loudest = levels.indices.maxByOrNull { levels[it] }!!
            assertTrue("$freq Hz lit band $loudest, expected ~$expected", abs(loudest - expected) <= 1)
        }
    }

    @Test fun levelsAreBounded() {
        val analyzer = BandAnalyzer()
        val rng = Random(3)
        repeat(40) {
            analyzer.feed(FloatArray(block) { rng.nextGaussian().toFloat() })
            val s = analyzer.snapshot()
            assertTrue(s.levels.all { it in 0.0..1.0 })
            assertTrue(s.gate in 0.0..1.0)
        }
    }

    @Test fun aQuietSpeakerStillGetsAGlyphButALoudOneReadsTaller() {
        val loud = drive(BandAnalyzer(), tone(500.0, 0.40), blocks = 30).levels
        val quiet = drive(BandAnalyzer(), tone(500.0, 0.02), blocks = 30).levels
        assertTrue(loud.max() > 0.9)
        assertTrue("a quiet speaker sees a dead glyph", quiet.max() > 0.4)
        assertTrue(loud.max() > quiet.max())
    }

    @Test fun louderSpeechDrawsTallerBars() {
        // The bug as reported: the glyph ran backwards.
        val heights = listOf(0.02, 0.06, 0.18, 0.45).map { level ->
            val shown = displayed(BandAnalyzer(), speechLike(level))
            val voiced = shown.sorted().drop(shown.size / 2)     // the talking half
            voiced.average()
        }
        val rounded = heights.map { round(it * 1000) / 1000 }
        assertEquals("not monotonic in loudness: $heights", rounded.sorted(), rounded)
        assertTrue("loud speech barely taller than quiet: $heights", heights.last() > heights.first() * 1.5)
    }

    @Test fun goingQuietDoesNotBloom() {
        // Stop talking and the mark must fall, not swell.
        val room = 0.008
        val analyzer = BandAnalyzer()
        val speaking = displayed(analyzer, speechLike(0.3, room = room))
        val loudestWhileTalking = speaking.max()
        val after = displayed(analyzer, (0 until 90).map { hiss(room, seed = it.toLong()) })
        assertTrue("the glyph grew after speech stopped", after.max() < loudestWhileTalking)
        assertTrue("the glyph never settled once the room went quiet", after.takeLast(20).average() < 0.15)
    }

    @Test fun shortBlocksAreAbsorbed() {
        val analyzer = BandAnalyzer()
        repeat(20) { analyzer.feed(tone(500.0, 0.2, samples = 160)) }
        val s = analyzer.snapshot()
        assertTrue(s.gate > 0.5)
        assertTrue(s.levels.max() > 0.5)
    }

    @Test fun feedNeverRaisesOnJunk() {
        val analyzer = BandAnalyzer()
        analyzer.feed(FloatArray(0))
        analyzer.feed(null)
        analyzer.feed(FloatArray(4), count = 99)
        assertEquals(Waveform.BAND_COUNT, analyzer.snapshot().levels.size)
    }

    @Test fun feedDoesNotRetainTheCallersBuffer() {
        // AudioRecord hands back a buffer the capture loop reuses.
        val analyzer = BandAnalyzer()
        val b = tone(500.0, 0.2)
        analyzer.feed(b)
        val before = analyzer.snapshot()
        b.fill(0f)
        assertEquals(before, analyzer.snapshot())
    }

    @Test fun rearmDropsTheReferenceButKeepsTheRoom() {
        val analyzer = BandAnalyzer()
        drive(analyzer, tone(500.0, 0.3), blocks = 30)
        val noiseBefore = analyzer.noiseDb
        analyzer.rearm()
        assertNull(analyzer.speechDb)
        assertEquals(noiseBefore, analyzer.noiseDb, 0.0)
        analyzer.reset()
        assertEquals(Waveform.NOISE_INIT_DB, analyzer.noiseDb, 0.0)
    }

    @Test fun noiseFloorCannotChaseALongUtterance() {
        val analyzer = BandAnalyzer()
        val speech = tone(300.0, 0.25)
        repeat((20.0 * rate / block).toInt()) { analyzer.feed(speech) }
        assertTrue(analyzer.snapshot().gate > 0.9)
    }

    @Test fun aDropoutBlockCannotDefineTheRoom() {
        val analyzer = BandAnalyzer()
        for (b in speechLike(0.05, seconds = 6.0)) analyzer.feed(b)
        val settled = analyzer.noiseDb
        analyzer.feed(FloatArray(block))
        assertEquals(settled, analyzer.noiseDb, 1.0)
        assertTrue(analyzer.noiseDb > Waveform.NOISE_FLOOR_DB + 20.0)
    }

    @Test fun theGlyphDoesNotStartSaturated() {
        val analyzer = BandAnalyzer()
        val leadIn = ArrayList<FloatArray>()
        repeat(6) { leadIn.add(hiss(0.0015)) }
        leadIn.add(3, FloatArray(block))                      // the stream opening
        val speech = speechLike(0.05, seconds = 16.0)
        val heights = displayed(analyzer, leadIn + speech)
        val perSecond = rate / block
        val armed = heights.take(leadIn.size)
        val voiced = heights.drop(leadIn.size)
        val onset = voiced.take(2 * perSecond).filter { it > 0 }
        val settled = voiced.takeLast(4 * perSecond).filter { it > 0 }
        assertTrue(onset.isNotEmpty() && settled.isNotEmpty())
        // Nobody has spoken yet: the gate is shut and the mark is a flat base.
        assertTrue(armed.max() < 0.05)
        // Same voice, same room — so the same glyph.
        assertTrue(onset.average() < 1.2 * settled.average())
    }

    @Test fun theRoomEstimateIsMeasuredNotFadedInFromAConstant() {
        val room = hiss(0.0015)
        val analyzer = BandAnalyzer()
        assertEquals(Waveform.NOISE_INIT_DB, analyzer.noiseDb, 0.0)
        analyzer.feed(room)
        val first = analyzer.noiseDb
        val settled = BandAnalyzer()
        drive(settled, room, blocks = 120)
        assertEquals(first, settled.noiseDb, 1.5)
        val learned = analyzer.noiseDb
        analyzer.rearm()
        analyzer.feed(room)
        assertEquals(learned, analyzer.noiseDb, 2.0)
    }

    // ── spring ───────────────────────────────────────────────────

    @Test fun criticallyDampedSpringDoesNotOvershoot() {
        val spring = Spring(0.0, omega = 44.0, zeta = 1.0)
        spring.target = 1.0
        var peak = 0.0
        repeat(240) { peak = maxOf(peak, spring.tick(1.0 / 60)) }
        assertTrue(peak <= 1.0001)
        assertEquals(1.0, spring.value, 0.01)
    }

    @Test fun revealIsEssentiallyCompleteWithinATenthOfASecond() {
        val spring = Spring(0.0, omega = 44.0, zeta = 1.0)
        spring.target = 1.0
        repeat(6) { spring.tick(1.0 / 60) }
        assertTrue(spring.value > 0.85)
    }

    @Test fun aLongStallDoesNotExplodeTheSpring() {
        val spring = Spring(0.0, omega = 44.0, zeta = 1.0)
        spring.target = 1.0
        spring.tick(0.5)
        assertTrue(spring.value <= 1.05)
        assertFalse(spring.value.isNaN())
    }

    // ── bar motion ───────────────────────────────────────────────

    @Test fun barsRiseTowardTheirBandAndSettle() {
        val motion = BarMotion()
        val targets = DoubleArray(12).also { it[5] = 1.0 }
        var heights = DoubleArray(12)
        repeat(120) { heights = motion.tick(1.0 / 60, targets, 1.0) }
        // Not 1.0: coupling bleeds an isolated spike into its neighbours.
        assertTrue(heights[5] > 0.65)
        assertTrue(heights[0] < 0.3)
    }

    @Test fun heightsNeverLeaveTheGlyph() {
        val motion = BarMotion()
        val rng = Random(11)
        repeat(600) {
            val heights = motion.tick(1.0 / 60, DoubleArray(12) { rng.nextDouble() }, 1.0)
            assertTrue(heights.all { it in -0.001..1.001 })
        }
    }

    @Test fun attackIsFasterThanRelease() {
        val motion = BarMotion()
        val up = DoubleArray(12) { 1.0 }
        var risen = DoubleArray(12)
        var fallen = DoubleArray(12)
        repeat(6) { risen = motion.tick(1.0 / 60, up, 1.0) }
        repeat(6) { fallen = motion.tick(1.0 / 60, DoubleArray(12), 1.0) }
        assertTrue(risen[6] > 0.5)
        assertTrue(fallen[6] > 0.25)
    }

    @Test fun neighboursAreCoupled() {
        val motion = BarMotion()
        val targets = DoubleArray(12).also { it[6] = 1.0 }
        var heights = DoubleArray(12)
        repeat(30) { heights = motion.tick(1.0 / 60, targets, 1.0) }
        assertTrue(heights[5] > 0.02)
        assertTrue(heights[7] > 0.02)
        assertTrue(heights[6] > heights[5])
    }

    @Test fun frameRateDoesNotChangeWhereTheMotionGetsTo() {
        val targets = DoubleArray(12) { 0.6 }
        val fast = BarMotion()
        val slow = BarMotion()
        repeat(120) { fast.tick(1.0 / 120, targets, 1.0) }
        repeat(60) { slow.tick(1.0 / 60, targets, 1.0) }
        for (i in 0 until 12) assertEquals(fast.heights[i], slow.heights[i], 0.01)
    }

    @Test fun aDroppedFrameDoesNotTeleportTheMotion() {
        val motion = BarMotion()
        repeat(60) { motion.tick(1.0 / 60, DoubleArray(12) { 0.8 }, 1.0) }
        val before = motion.heights
        val after = motion.tick(0.25, DoubleArray(12), 1.0)
        assertTrue(after.all { it in 0.0..1.0 })
        assertTrue(after.max() < before.max() + 0.01)
    }

    @Test fun aShutGateReturnsTheRowToTheBase() {
        val motion = BarMotion()
        repeat(60) { motion.tick(1.0 / 60, DoubleArray(12) { 1.0 }, 1.0) }
        var heights = DoubleArray(12)
        repeat(180) { heights = motion.tick(1.0 / 60, DoubleArray(12) { 1.0 }, 0.0) }
        assertTrue(heights.max() < Waveform.REST_HEIGHT * 2.0)
    }

    @Test fun restingRippleIsBarelyThere() {
        val motion = BarMotion()
        var peak = 0.0
        repeat(600) { peak = maxOf(peak, motion.tick(1.0 / 60, null, 0.0, BarMotion.State.REST).max()) }
        assertTrue(peak < Waveform.REST_HEIGHT * 1.5)
        assertTrue(peak > 0.0)
    }

    @Test fun processingSweepTravelsAcrossTheRow() {
        val motion = BarMotion()
        var seenLeft = false
        var seenRight = false
        repeat(240) {
            val heights = motion.tick(1.0 / 60, null, 0.0, BarMotion.State.WORK)
            val leader = heights.indices.maxByOrNull { heights[it] }!!
            seenLeft = seenLeft || leader <= 3
            seenRight = seenRight || leader >= 8
        }
        assertTrue(seenLeft && seenRight)
    }

    @Test fun resetClearsVelocityAsWellAsHeight() {
        val motion = BarMotion()
        repeat(30) { motion.tick(1.0 / 60, DoubleArray(12) { 1.0 }, 1.0) }
        motion.reset()
        assertTrue(motion.heights.all { it == 0.0 })
        val after = motion.tick(1.0 / 60, DoubleArray(12), 0.0, BarMotion.State.REST)
        assertTrue(after.max() <= Waveform.REST_HEIGHT)
    }

    // ── silhouette ───────────────────────────────────────────────

    private fun xs(points: DoubleArray) = points.filterIndexed { i, _ -> i % 2 == 0 }
    private fun ys(points: DoubleArray) = points.filterIndexed { i, _ -> i % 2 == 1 }

    @Test fun shapeIsOneClosedPolygonOverAFlatBase() {
        val g = GlyphGeometry()
        val points = silhouette(DoubleArray(g.bands) { 0.5 }, g)
        assertEquals(2 * (2 * g.bands + 2), points.size)
        assertEquals(0.0, ys(points).first(), 0.0)
        assertEquals(0.0, ys(points).last(), 0.0)
        assertEquals(0.0, xs(points).first(), 1e-9)
        assertEquals(g.width, xs(points).last(), 1e-9)
    }

    @Test fun columnsShareEdges() {
        val g = GlyphGeometry()
        val levels = DoubleArray(g.bands) { 0.4 }.also { it[0] = 0.2; it[1] = 0.9 }
        val x = xs(silhouette(levels, g))
        val tops = x.subList(1, x.size - 1)
        var i = 0
        while (i < tops.size - 2) {
            assertEquals(tops[i + 1], tops[i + 2], 1e-6)
            i += 2
        }
    }

    @Test fun baseIsPresentEvenAtZero() {
        val g = GlyphGeometry()
        val y = ys(silhouette(DoubleArray(g.bands), g))
        assertTrue(y.all { it == 0.0 || it == g.baseHeight })
    }

    @Test fun revealRetractsWidthAndHeightTogether() {
        val g = GlyphGeometry()
        val levels = DoubleArray(g.bands) { 1.0 }
        val wide = silhouette(levels, g, reveal = 1.0)
        val closing = silhouette(levels, g, reveal = 0.3)
        assertTrue(xs(closing).max() - xs(closing).min() < xs(wide).max() - xs(wide).min())
        assertTrue(ys(closing).max() < ys(wide).max())
    }

    @Test fun revealCollapsesAboutTheCentre() {
        val g = GlyphGeometry()
        val x = xs(silhouette(DoubleArray(g.bands) { 0.5 }, g, reveal = 0.0))
        assertEquals(g.width / 2.0, (x.min() + x.max()) / 2.0, 1e-6)
    }

    @Test fun snapQuantisesXButNotHeight() {
        val g = GlyphGeometry(columnWidth = 7.3)
        val points = silhouette(DoubleArray(g.bands) { 0.37 }, g, snap = 0.5)
        for (x in xs(points)) assertEquals(x * 2.0, Math.rint(x * 2.0), 1e-6)
        val tops = ys(points).filter { it > g.baseHeight }.toSet()
        assertTrue(tops.any { abs(it * 2.0 - Math.rint(it * 2.0)) > 1e-6 })
    }

    @Test fun shortLevelListsDoNotRaise() {
        val g = GlyphGeometry()
        assertEquals(2 * (2 * g.bands + 2), silhouette(doubleArrayOf(0.5), g).size)
        assertNotNull(silhouette(DoubleArray(0), g))
    }
}

/** The resting state of the floating bubble: the Say mark, held steady. */
class MarkStateTest {
    @org.junit.Test fun restSettlesIntoTheSayMark() {
        val motion = BarMotion()
        var heights = DoubleArray(12)
        repeat(240) { heights = motion.tick(1.0 / 60, null, 0.0, BarMotion.State.MARK) }
        // The mark's silhouette: up to the third column, then down, never flat.
        org.junit.Assert.assertTrue(heights[4] > heights[1])
        org.junit.Assert.assertTrue(heights[4] > heights[10])
        org.junit.Assert.assertTrue(heights[6] > heights[10])
        org.junit.Assert.assertTrue("the mark must be clearly visible, not a hairline", heights[4] > 0.7)
        org.junit.Assert.assertTrue(heights.all { it in 0.0..1.0 })
    }

    @org.junit.Test fun theProfileIsTheLogosOwnGeometry() {
        // gen_logo.py's MARK_COLUMNS heights on the 30x12 grid, two bands each.
        org.junit.Assert.assertEquals(12, Waveform.MARK_PROFILE.size)
        org.junit.Assert.assertEquals(1.0, Waveform.MARK_PROFILE.max(), 1e-9)
        org.junit.Assert.assertEquals(3.25 / 12.0, Waveform.MARK_PROFILE.first(), 1e-9)
        org.junit.Assert.assertEquals(Waveform.MARK_PROFILE[0], Waveform.MARK_PROFILE[1], 1e-9)
    }
}

/** The held mark must be the logo's own steps, not a diffused hill. */
class MarkFidelityTest {
    @org.junit.Test fun theHeldMarkKeepsItsSteps() {
        val motion = BarMotion()
        var heights = DoubleArray(12)
        repeat(300) { heights = motion.tick(1.0 / 60, null, 0.0, BarMotion.State.MARK) }
        for (i in 0 until 12) {
            org.junit.Assert.assertEquals(Waveform.MARK_PROFILE[i], heights[i], 0.02)
        }
        // Pairs of bands form one logo column, so each pair is level.
        for (i in 0 until 12 step 2) {
            org.junit.Assert.assertEquals(heights[i], heights[i + 1], 1e-6)
        }
    }

    @org.junit.Test fun couplingStillActsOnLiveAudio() {
        val motion = BarMotion()
        val targets = DoubleArray(12).also { it[6] = 1.0 }
        var heights = DoubleArray(12)
        repeat(30) { heights = motion.tick(1.0 / 60, targets, 1.0) }
        org.junit.Assert.assertTrue("live audio must still read as one membrane", heights[5] > 0.02)
    }
}
