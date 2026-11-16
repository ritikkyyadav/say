"""Tests for the overlay's audio→motion engine (`waveform.py`).

The overlay itself needs a window server, but everything that decides how it
*feels* — which band lights up, how fast a bar rises, whether the row moves as
one shape, what happens on a dropped frame — is pure and lives here.
"""

import math
import unittest

import numpy as np
import waveform
from waveform import (
    BandAnalyzer,
    BarMotion,
    GlyphGeometry,
    Spring,
    band_edges,
    silhouette,
)

RATE = waveform.SAMPLE_RATE
BLOCK = waveform.FFT_SIZE


def tone(freq: float, amplitude: float = 0.25, samples: int = BLOCK) -> np.ndarray:
    n = np.arange(samples) / RATE
    return (amplitude * np.sin(2 * np.pi * freq * n)).astype('float32')


def hiss(amplitude: float = 0.002, samples: int = BLOCK, seed: int = 7) -> np.ndarray:
    """Broadband room tone — what the mic hears when nobody is talking."""
    rng = np.random.default_rng(seed)
    return (amplitude * rng.standard_normal(samples)).astype('float32')


def speech_like(level: float, seconds: float = 6.0, room: float = 0.0015, seed: int = 3):
    """Blocks of speech-shaped bursts laid over a room-tone bed.

    A steady sine is useless for testing how loudness maps to height: it has no
    pauses, so the noise-floor window never gets a look at the room, and no
    dynamics, so nothing that depends on change is exercised at all. (An
    earlier version of these tests used one and passed happily against the
    inverted build.) Real speech is bursts separated by gaps, on top of a room
    that does not go away while you talk.
    """
    rng = np.random.default_rng(seed)
    blocks = []
    for i in range(int(seconds * RATE / BLOCK)):
        block = room * rng.standard_normal(BLOCK)
        if (i % 8) < 5:                      # ~320 ms on, ~190 ms off
            voice = rng.standard_normal(BLOCK)
            voice = np.convolve(voice, np.ones(8) / 8, mode='same')  # tilt down
            block = block + level * voice
        blocks.append(block.astype('float32'))
    return blocks


def displayed(analyzer: BandAnalyzer, blocks) -> list[float]:
    """Visual mass of the glyph for each block, gate included.

    The mean across bands, not the max: the tallest bar saturates at 1.0 as
    soon as the reference is anything like the signal, so a max-based measure
    reads a fully inverted build as "fine". The mean is what the silhouette
    actually looks like.
    """
    out = []
    for block in blocks:
        analyzer.feed(block)
        levels, gate = analyzer.snapshot()
        out.append(sum(levels) / len(levels) * gate)
    return out


def drive(analyzer: BandAnalyzer, block: np.ndarray, blocks: int = 12):
    """Feed the same block repeatedly so the window and gate settle."""
    for _ in range(blocks):
        analyzer.feed(block)
    return analyzer.snapshot()


class BandEdgeTests(unittest.TestCase):
    def test_edges_are_strictly_increasing(self):
        edges = band_edges()
        self.assertEqual(len(edges), waveform.BAND_COUNT)
        for lo, hi in edges:
            self.assertLess(lo, hi)
        for (_, hi), (lo, _) in zip(edges, edges[1:], strict=False):
            self.assertEqual(hi, lo)

    def test_edges_stay_inside_the_spectrum(self):
        edges = band_edges()
        self.assertGreaterEqual(edges[0][0], 1)
        self.assertLessEqual(edges[-1][1], BLOCK // 2 + 1)

    def test_narrow_band_count_still_gives_every_band_a_bin(self):
        # A pathological request (more bands than bins in the range) must not
        # produce an empty band — an empty slice would mean a permanently dead
        # column on screen.
        edges = band_edges(bands=40, fmin=90.0, fmax=400.0)
        self.assertEqual(len(edges), 40)
        for lo, hi in edges:
            self.assertLess(lo, hi)


class BandAnalyzerTests(unittest.TestCase):
    def test_silence_leaves_the_gate_shut(self):
        analyzer = BandAnalyzer()
        levels, gate = drive(analyzer, np.zeros(BLOCK, dtype='float32'), blocks=30)
        self.assertLess(gate, 0.05)
        self.assertLessEqual(max(levels), 1.0)

    def test_speech_level_tone_opens_the_gate(self):
        analyzer = BandAnalyzer()
        _, gate = drive(analyzer, tone(400.0, 0.2), blocks=30)
        self.assertGreater(gate, 0.9)

    def test_a_tone_lights_its_own_band(self):
        edges = band_edges()
        bin_hz = RATE / BLOCK
        for freq in (220.0, 900.0, 3000.0):
            analyzer = BandAnalyzer()
            levels, _ = drive(analyzer, tone(freq, 0.2))
            expected = max(
                range(len(edges)),
                key=lambda k: (edges[k][0] * bin_hz <= freq < edges[k][1] * bin_hz),
            )
            loudest = max(range(len(levels)), key=lambda k: levels[k])
            self.assertLessEqual(
                abs(loudest - expected), 1,
                f'{freq} Hz lit band {loudest}, expected ~{expected}',
            )

    def test_levels_are_bounded(self):
        analyzer = BandAnalyzer()
        rng = np.random.default_rng(3)
        for _ in range(40):
            analyzer.feed(rng.standard_normal(BLOCK).astype('float32'))
            levels, gate = analyzer.snapshot()
            self.assertTrue(all(0.0 <= v <= 1.0 for v in levels))
            self.assertTrue(0.0 <= gate <= 1.0)

    def test_a_quiet_speaker_still_gets_a_glyph_but_a_loud_one_reads_taller(self):
        # This test used to assert both filled the mark identically, which is
        # the same thing as saying the display carries no loudness information
        # — and that auto-ranging is exactly what ran the glyph backwards. A
        # quiet speaker must still see it move; they must not see the *same*
        # thing as someone shouting.
        loud, _ = drive(BandAnalyzer(), tone(500.0, 0.40), blocks=30)
        quiet, _ = drive(BandAnalyzer(), tone(500.0, 0.02), blocks=30)
        self.assertGreater(max(loud), 0.9)
        self.assertGreater(max(quiet), 0.4, 'a quiet speaker sees a dead glyph')
        self.assertGreater(max(loud), max(quiet))

    def test_louder_speech_draws_taller_bars(self):
        """The bug as reported: the glyph ran backwards.

        Normalising against the instantaneous loudest band gives a reference
        that rises exactly as fast as the voice does, so it cancels itself out.
        Measured over a real 262 s dictation, the quietest 30% of blocks drew
        *taller* bars (0.56) than the loudest 30% (0.21).
        """
        heights = []
        for level in (0.02, 0.06, 0.18, 0.45):
            shown = displayed(BandAnalyzer(), speech_like(level))
            voiced = sorted(shown)[len(shown) // 2:]      # the talking half
            heights.append(sum(voiced) / len(voiced))

        self.assertEqual(
            [round(h, 3) for h in heights],
            sorted(round(h, 3) for h in heights),
            f'not monotonic in loudness: {heights}',
        )
        self.assertGreater(
            heights[-1], heights[0] * 1.5,
            f'loud speech barely taller than quiet: {heights}',
        )

    def test_going_quiet_does_not_bloom(self):
        """Stop talking and the mark must fall, not swell.

        The old reference decayed during silence while the room noise did not,
        so the instant you stopped speaking the noise was renormalised upward
        and the glyph bloomed — the loudest thing on screen was the silence.
        """
        # A real room, not a silent anechoic one: the whole failure is the
        # reference sliding down while the room noise stays where it is, so a
        # near-zero noise bed cannot show it.
        room = 0.008
        analyzer = BandAnalyzer()
        speaking = displayed(analyzer, speech_like(0.3, room=room))
        loudest_while_talking = max(speaking)

        after = displayed(analyzer, [hiss(room, seed=i) for i in range(90)])

        self.assertLess(
            max(after), loudest_while_talking,
            'the glyph grew after speech stopped',
        )
        self.assertLess(
            sum(after[-20:]) / 20, 0.15,
            'the glyph never settled once the room went quiet',
        )

    def test_short_blocks_are_absorbed(self):
        analyzer = BandAnalyzer()
        for _ in range(20):
            analyzer.feed(tone(500.0, 0.2, samples=160))
        levels, gate = analyzer.snapshot()
        self.assertGreater(gate, 0.5)
        self.assertGreater(max(levels), 0.5)

    def test_feed_never_raises_on_junk(self):
        analyzer = BandAnalyzer()
        for junk in (np.array([]), None, 'not audio', np.zeros((4, 2), dtype='float32')):
            analyzer.feed(junk)          # must not raise: this is the audio thread
        self.assertEqual(len(analyzer.snapshot()[0]), waveform.BAND_COUNT)

    def test_feed_does_not_retain_the_callers_buffer(self):
        # sounddevice hands back a view onto a buffer it reuses; keeping it
        # would make the display show whatever the next block overwrote it with.
        analyzer = BandAnalyzer()
        block = tone(500.0, 0.2)
        analyzer.feed(block)
        before = analyzer.snapshot()
        block[:] = 0.0
        self.assertEqual(analyzer.snapshot(), before)

    def test_rearm_drops_the_reference_but_keeps_the_room(self):
        analyzer = BandAnalyzer()
        drive(analyzer, tone(500.0, 0.3), blocks=30)
        noise_before = analyzer._noise_db
        analyzer.rearm()
        self.assertIsNone(analyzer._speech_db)
        self.assertEqual(analyzer._noise_db, noise_before)
        analyzer.reset()
        self.assertEqual(analyzer._noise_db, waveform.NOISE_INIT_DB)

    def test_noise_floor_cannot_chase_a_long_utterance(self):
        # At 1.5 dB/s the floor climbed into the speaker and shut the gate
        # mid-sentence; this is the regression guard for that.
        analyzer = BandAnalyzer()
        speech = tone(300.0, 0.25)
        for _ in range(int(20.0 * RATE / BLOCK)):     # 20 s of continuous speech
            analyzer.feed(speech)
        self.assertGreater(analyzer.snapshot()[1], 0.9)

    def test_a_dropout_block_cannot_define_the_room(self):
        # An opening or glitching CoreAudio stream hands back a buffer of exact
        # zeros; 141 of 148 stored dictations contain at least one. Under a raw
        # minimum a single one of them owned the room estimate for the whole
        # window length, dragging it to the clamp.
        analyzer = BandAnalyzer()
        for block in speech_like(0.05, seconds=6.0):
            analyzer.feed(block)
        settled = analyzer._noise_db

        analyzer.feed(np.zeros(BLOCK, dtype='float32'))

        self.assertAlmostEqual(analyzer._noise_db, settled, delta=1.0)
        self.assertGreater(analyzer._noise_db, waveform.NOISE_FLOOR_DB + 20.0)

    def test_the_glyph_does_not_start_saturated(self):
        # The defect this guards: the mark goes up on trigger-key down, so it
        # eats the stream's opening silence and the room before the first word.
        # A raw-minimum floor latched onto them, every band then mapped near the
        # top of the display window, and the glyph stood at full height and
        # stopped moving — until, at exactly NOISE_WINDOW_S, the offending block
        # aged out and it abruptly came alive. Over the stored corpus that was
        # 46% of bands pinned at 1.0 across the first two seconds against 5.7%
        # settled. Two things have to hold: armed-and-silent draws nothing, and
        # the first seconds of speech draw the same glyph as the tenth.
        analyzer = BandAnalyzer()
        lead_in = [hiss(0.0015) for _ in range(6)]
        lead_in.insert(3, np.zeros(BLOCK, dtype='float32'))   # the stream opening
        speech = speech_like(0.05, seconds=16.0)

        heights = displayed(analyzer, lead_in + speech)
        per_second = int(RATE / BLOCK)
        armed = heights[:len(lead_in)]
        voiced = heights[len(lead_in):]
        onset = [h for h in voiced[:2 * per_second] if h > 0]
        settled = [h for h in voiced[-4 * per_second:] if h > 0]
        self.assertTrue(onset and settled)

        # Nobody has spoken yet: the gate is shut and the mark is a flat base.
        # It used to sit half-lit here, which is what read as "already going".
        self.assertLess(max(armed), 0.05)
        # Same voice, same room — so the same glyph. The bug drew 1.37x on this
        # stimulus and 2.9x on real dictations.
        self.assertLess(sum(onset) / len(onset), 1.2 * (sum(settled) / len(settled)))

    def test_the_room_estimate_is_measured_not_faded_in_from_a_constant(self):
        # NOISE_INIT_DB is a placeholder, not a prior. Easing away from it at
        # NOISE_TRACK_COEFF spent the first second of every cold start drawing
        # a room nobody is sitting in — so one block of the real room has to
        # land essentially where a long look at it does.
        room = hiss(0.0015)
        analyzer = BandAnalyzer()
        self.assertEqual(analyzer._noise_db, waveform.NOISE_INIT_DB)
        analyzer.feed(room)
        first = analyzer._noise_db

        settled = BandAnalyzer()
        drive(settled, room, blocks=120)
        self.assertAlmostEqual(first, settled._noise_db, delta=1.5)

        # …and a second dictation keeps what the first one learned: the room
        # has not changed, and relearning it costs the gate a second every time.
        learned = analyzer._noise_db
        analyzer.rearm()
        analyzer.feed(room)
        self.assertAlmostEqual(analyzer._noise_db, learned, delta=2.0)


class SpringTests(unittest.TestCase):
    def test_critically_damped_spring_does_not_overshoot(self):
        spring = Spring(0.0, omega=44.0, zeta=1.0)
        spring.target = 1.0
        peak = 0.0
        for _ in range(240):
            peak = max(peak, spring.tick(1 / 60))
        self.assertLessEqual(peak, 1.0001)
        self.assertAlmostEqual(spring.value, 1.0, places=2)

    def test_reveal_is_essentially_complete_within_a_tenth_of_a_second(self):
        spring = Spring(0.0, omega=44.0, zeta=1.0)
        spring.target = 1.0
        for _ in range(6):                            # 100 ms at 60 Hz
            spring.tick(1 / 60)
        self.assertGreater(spring.value, 0.85)

    def test_a_long_stall_does_not_explode_the_spring(self):
        spring = Spring(0.0, omega=44.0, zeta=1.0)
        spring.target = 1.0
        spring.tick(0.5)                              # a half-second hitch
        self.assertLessEqual(spring.value, 1.05)
        self.assertFalse(math.isnan(spring.value))


class BarMotionTests(unittest.TestCase):
    def test_bars_rise_toward_their_band_and_settle(self):
        motion = BarMotion()
        targets = [0.0] * 12
        targets[5] = 1.0
        for _ in range(120):
            heights = motion.tick(1 / 60, targets, 1.0)
        # Not 1.0: coupling bleeds an isolated spike into its neighbours, which
        # is the point — a lone column cannot stand apart from the shape.
        self.assertGreater(heights[5], 0.65)
        self.assertLess(heights[0], 0.3)

    def test_heights_never_leave_the_glyph(self):
        motion = BarMotion()
        rng = np.random.default_rng(11)
        for _ in range(600):
            targets = rng.random(12).tolist()
            heights = motion.tick(1 / 60, targets, 1.0)
            self.assertTrue(all(-0.001 <= h <= 1.001 for h in heights))

    def test_attack_is_faster_than_release(self):
        motion = BarMotion()
        up = [1.0] * 12
        for _ in range(6):
            risen = motion.tick(1 / 60, up, 1.0)
        for _ in range(6):
            fallen = motion.tick(1 / 60, [0.0] * 12, 1.0)
        self.assertGreater(risen[6], 0.5)                 # 100 ms to get most of the way up
        self.assertGreater(fallen[6], 0.25)               # …and still falling 100 ms later

    def test_neighbours_are_coupled(self):
        # One band alone must lift the columns beside it, or the row reads as
        # twelve separate meters instead of one shape.
        motion = BarMotion()
        targets = [0.0] * 12
        targets[6] = 1.0
        for _ in range(30):
            heights = motion.tick(1 / 60, targets, 1.0)
        self.assertGreater(heights[5], 0.02)
        self.assertGreater(heights[7], 0.02)
        self.assertGreater(heights[6], heights[5])

    def test_frame_rate_does_not_change_where_the_motion_gets_to(self):
        targets = [0.6] * 12
        fast, slow = BarMotion(), BarMotion()
        for _ in range(120):
            fast.tick(1 / 120, targets, 1.0)
        for _ in range(60):
            slow.tick(1 / 60, targets, 1.0)
        for a, b in zip(fast.heights, slow.heights, strict=True):
            self.assertAlmostEqual(a, b, places=2)

    def test_a_dropped_frame_does_not_teleport_the_motion(self):
        motion = BarMotion()
        for _ in range(60):
            motion.tick(1 / 60, [0.8] * 12, 1.0)
        before = motion.heights
        after = motion.tick(0.25, [0.0] * 12, 1.0)        # a quarter-second stall
        self.assertTrue(all(0.0 <= h <= 1.0 for h in after))
        self.assertLess(max(after), max(before) + 0.01)

    def test_a_shut_gate_returns_the_row_to_the_base(self):
        motion = BarMotion()
        for _ in range(60):
            motion.tick(1 / 60, [1.0] * 12, 1.0)
        for _ in range(180):
            heights = motion.tick(1 / 60, [1.0] * 12, 0.0)
        self.assertLess(max(heights), waveform.REST_HEIGHT * 2.0)

    def test_resting_ripple_is_barely_there(self):
        motion = BarMotion()
        peak = 0.0
        for _ in range(600):
            peak = max(peak, max(motion.tick(1 / 60, None, 0.0, BarMotion.REST)))
        self.assertLess(peak, waveform.REST_HEIGHT * 1.5)
        self.assertGreater(peak, 0.0)

    def test_processing_sweep_travels_across_the_row(self):
        motion = BarMotion()
        seen_left = seen_right = False
        for _ in range(240):
            heights = motion.tick(1 / 60, None, 0.0, BarMotion.WORK)
            leader = max(range(12), key=lambda k: heights[k])
            seen_left |= leader <= 3
            seen_right |= leader >= 8
        self.assertTrue(seen_left and seen_right)

    def test_reset_clears_velocity_as_well_as_height(self):
        motion = BarMotion()
        for _ in range(30):
            motion.tick(1 / 60, [1.0] * 12, 1.0)
        motion.reset()
        self.assertEqual(motion.heights, tuple([0.0] * 12))
        after = motion.tick(1 / 60, [0.0] * 12, 0.0, BarMotion.REST)
        self.assertLessEqual(max(after), waveform.REST_HEIGHT)


class SilhouetteTests(unittest.TestCase):
    def test_shape_is_one_closed_polygon_over_a_flat_base(self):
        geometry = GlyphGeometry()
        points = silhouette([0.5] * geometry.bands, geometry)
        self.assertEqual(len(points), 2 * geometry.bands + 2)
        self.assertEqual(points[0][1], 0.0)               # starts on the base
        self.assertEqual(points[-1][1], 0.0)              # and returns to it
        self.assertAlmostEqual(points[0][0], 0.0)
        self.assertAlmostEqual(points[-1][0], geometry.width)

    def test_columns_share_edges(self):
        geometry = GlyphGeometry()
        points = silhouette([0.2, 0.9] + [0.4] * (geometry.bands - 2), geometry)
        tops = points[1:-1]
        for i in range(0, len(tops) - 2, 2):
            self.assertAlmostEqual(tops[i + 1][0], tops[i + 2][0], places=6)

    def test_base_is_present_even_at_zero(self):
        geometry = GlyphGeometry()
        points = silhouette([0.0] * geometry.bands, geometry)
        self.assertTrue(all(y in (0.0, geometry.base_height) for _, y in points))

    def test_reveal_retracts_width_and_height_together(self):
        geometry = GlyphGeometry()
        levels = [1.0] * geometry.bands
        wide = silhouette(levels, geometry, reveal=1.0)
        closing = silhouette(levels, geometry, reveal=0.3)
        self.assertLess(
            max(x for x, _ in closing) - min(x for x, _ in closing),
            max(x for x, _ in wide) - min(x for x, _ in wide),
        )
        self.assertLess(max(y for _, y in closing), max(y for _, y in wide))

    def test_reveal_collapses_about_the_centre(self):
        geometry = GlyphGeometry()
        points = silhouette([0.5] * geometry.bands, geometry, reveal=0.0)
        xs = [x for x, _ in points]
        self.assertAlmostEqual(
            (min(xs) + max(xs)) / 2.0, geometry.width / 2.0, places=6
        )

    def test_snap_quantises_x_but_not_height(self):
        geometry = GlyphGeometry(column_width=7.3)
        points = silhouette([0.37] * geometry.bands, geometry, snap=0.5)
        for x, _ in points:
            self.assertAlmostEqual(x * 2.0, round(x * 2.0), places=6)
        tops = {y for _, y in points if y > geometry.base_height}
        self.assertTrue(any(abs(y * 2.0 - round(y * 2.0)) > 1e-6 for y in tops))

    def test_short_level_lists_do_not_raise(self):
        geometry = GlyphGeometry()
        points = silhouette([0.5], geometry)
        self.assertEqual(len(points), 2 * geometry.bands + 2)


if __name__ == '__main__':
    unittest.main()


class ShakeTests(unittest.TestCase):
    """The failure shake starts and ends at rest, and reads as side to side."""

    def test_it_starts_and_ends_exactly_at_rest(self):
        from waveform import SHAKE_S, shake_offset

        self.assertEqual(shake_offset(0.0), 0.0)
        self.assertEqual(shake_offset(SHAKE_S), 0.0)
        self.assertEqual(shake_offset(-1.0), 0.0)
        self.assertEqual(shake_offset(SHAKE_S + 1.0), 0.0)

    def test_it_swings_both_ways_and_never_past_its_amplitude(self):
        from waveform import SHAKE_CYCLES, SHAKE_PT, SHAKE_S, shake_offset

        samples = [shake_offset(SHAKE_S * k / 240) for k in range(241)]
        self.assertLessEqual(max(abs(v) for v in samples), SHAKE_PT)
        crossings = sum(1 for a, b in zip(samples, samples[1:], strict=False) if a * b < 0)
        self.assertGreaterEqual(crossings, int(2 * SHAKE_CYCLES) - 1)

    def test_it_settles(self):
        from waveform import SHAKE_S, shake_offset

        early = max(abs(shake_offset(SHAKE_S * k / 100)) for k in range(0, 30))
        late = max(abs(shake_offset(SHAKE_S * k / 100)) for k in range(70, 100))
        self.assertLess(late, early / 2)
