package com.ritikyadav.say.engine

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.Random

class SilenceGuardTest {
    @Test fun exactZerosAreSilent() = assertTrue(MoonshineTranscriber.isSilent(FloatArray(16_000)))

    @Test fun roomToneIsNotSilent() {
        val rng = Random(1)
        assertFalse(MoonshineTranscriber.isSilent(FloatArray(16_000) { (rng.nextGaussian() * 0.002).toFloat() }))
    }

    @Test fun aSingleRealSampleIsEnough() {
        val audio = FloatArray(16_000).also { it[9_000] = 0.01f }
        assertFalse(MoonshineTranscriber.isSilent(audio))
    }

    @Test fun emptyIsSilent() = assertTrue(MoonshineTranscriber.isSilent(FloatArray(0)))
}
