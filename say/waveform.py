"""Audio → motion engine behind the dictation overlay.

Pure Python + numpy: no AppKit, no windows, no threads of its own. Given
(audio in, dt) the output is deterministic, which is what makes the overlay's
*feel* testable frame by frame instead of only judgeable by eye.

Three parts:

  `BandAnalyzer`  mic blocks → N normalised band levels + a voice gate. Runs on
                  the audio thread: one small rFFT per block, result handed over
                  by a single attribute assignment. No locks — a reader can
                  never stall the microphone.

  `BarMotion`     band levels → bar heights, frame by frame. Every bar is its
                  own spring, but neighbours are diffusion-coupled, so the row
                  settles like one stretched membrane rather than twelve
                  independent VU meters. That coupling is the whole trick
                  behind "it moves as one system, not twelve components".

  `silhouette()`  bar heights → the closed polygon of the glyph. One path, not
                  N rectangles: adjacent columns share an edge, so the shape can
                  never show a seam between them and reads as a single object
                  standing on one continuous base.

Everything is expressed in normalised 0–1 heights until `silhouette()`, which
is the only place that knows about points on screen.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

# ── Analysis geometry ───────────────────────────────────────────
BAND_COUNT = 12
SAMPLE_RATE = 16_000
FFT_SIZE = 1024               # 64 ms @ 16 kHz — exactly one mic block
FREQ_MIN = 90.0               # below this is rumble and mains hum, not speech
FREQ_MAX = 5_200.0            # speech detail is gone well before Nyquist

# Speech rolls off ≈6 dB/octave above ~500 Hz. Bands are ~0.5 octave wide, so
# without this compensation the right-hand third of the glyph would never move.
TILT_DB_PER_BAND = 3.0

# Displayed dynamic range, used as the starting width of the window before the
# speech-level tracker has anything to go on. Narrow it and the row gains
# contrast; widen it and every band drifts toward the same mid height, which
# draws a flat slab.
DYNAMIC_RANGE_DB = 21.0

# Contrast curve applied after normalisation. Speech spectra are smooth, so a
# linear mapping parks most bands near the middle and the silhouette loses its
# steps; the exponent pushes quiet bands down toward the base while leaving the
# loud ones where they are.
LEVEL_GAMMA = 1.45

# Fraction of a band represented by its loudest bin rather than its mean. Pure
# mean is stable but averages away the harmonics that make a voice legible as
# pitch; pure max is peaky and biases wide (high) bands upward.
BAND_PEAK_MIX = 0.65

# The display window runs from the room's noise floor up to a slowly-tracked
# estimate of how loudly this person actually speaks.
#
# The obvious design — normalise against the loudest band seen recently, with
# instant attack — is *inverted*, and measurably so. A reference that jumps to
# the current peak rises exactly as fast as your voice does, so shouting draws
# the same glyph as murmuring; then when you stop, the reference decays while
# the room noise does not, and the glyph blooms into the silence. Measured over
# a real 262 s dictation, the quietest 30% of blocks drew *taller* bars (0.56)
# than the loudest 30% (0.21).
#
# So the floor is anchored to the room — which moves the right way, a louder
# room pushing bars down rather than up — and the ceiling adapts over seconds
# and only while a voice is actually present.
SPEECH_ATTACK_DB_PER_S = 9.0      # ceiling rises this fast while you talk…
SPEECH_RELEASE_DB_PER_S = 3.0     # …and settles back this slowly
SPEECH_MIN_SPAN_DB = 18.0         # never squeeze the window tighter than this
SPEECH_MAX_SPAN_DB = 42.0         # nor stretch it until nothing reaches the top
SPEECH_SEED_OVER_FLOOR_DB = 24.0  # where the ceiling sits before anyone speaks
AGC_MIN_DB = -70.0
AGC_MAX_DB = -3.0

# Voice gate. Both thresholds float with an estimate of the room, so a noisy
# café doesn't leave the glyph permanently half-lit.
GATE_SHUT_OVER_NOISE_DB = 5.0
GATE_OPEN_OVER_NOISE_DB = 14.0
# The floor is a low quantile of the RMS seen in the last NOISE_WINDOW_S
# seconds (minimum statistics), not a rise/fall tracker. Speech is full of gaps
# — between words, between syllables — so the quiet tail of a few seconds lands
# on the room even while someone is mid-sentence.
#
# The rise/fall version it replaces could not tell a louder *room* from a
# louder *speaker*, so it climbed into the speech: -79 dB to -42 dB over one
# 262 s dictation, which lifted the display floor above the speaker and
# collapsed the glyph. Suppressing the rise while a voice was present only
# moved the failure: the floor could still fall during inter-syllable gaps but
# never recover, so it ratcheted down to the clamp and the gate stopped
# closing at all. A windowed order statistic has neither failure mode and needs
# no feedback from the gate it feeds.
#
# It must be a quantile and NOT the raw minimum, which is the extreme order
# statistic and therefore latches onto the single quietest block in the window.
# Two things guarantee such a block exists at the start of every dictation, and
# together they were the overlay's worst visible defect:
#
#   1. The mark goes up on trigger-key *down*, so the analyzer is fed from the
#      moment the mic stream opens — and an opening or glitching CoreAudio
#      stream hands back buffers of exact digital silence (-240 dBFS). Measured
#      over 148 stored dictations, 141 contained at least one such block.
#   2. Every dictation begins with the room, before the first word. That lead-in
#      really is the quietest thing in the clip, so a raw minimum locks onto it
#      and holds it for the full window length.
#
# Either one drags the estimate far below the room the *rest* of the dictation
# is spoken in. Because the display window is anchored to it, every band then
# maps near the top: measured on the corpus, 46% of bands sat clamped at 1.0
# for the first two seconds and 71% by the fourth, i.e. a flat slab that cannot
# move — and then, at exactly NOISE_WINDOW_S, the offending block aged out of
# the window and the glyph abruptly became responsive. That "it hangs for a few
# seconds, then starts following my voice" was this, not the springs.
#
# A P10 with digital silence discarded is robust to both: the estimate now sits
# within ~3 dB of its settled value from the first second (was ~40 dB out), and
# the bar height over the first two seconds is 0.33 against a settled 0.29
# (was 0.77). Cost is one sorted() of ~125 floats per block: 3.1 us against a
# 64 ms budget, on the audio thread.
NOISE_WINDOW_S = 8.0
DISPLAY_FLOOR_OVER_NOISE_DB = 2.0  # where bars start lifting, above the room
NOISE_TRACK_COEFF = 0.08   # how fast the estimate slides toward that quantile
NOISE_QUANTILE = 0.10      # low order statistic of the window — see above
# A raw minimum under-reads the room and needed lifting; a P10 does not, and
# this is 0 so the estimator change lands the *settled* floor exactly where the
# old one did. Verified over the corpus: settled bar height 0.286 vs 0.292,
# bands pinned 4.6% vs 5.7%, gate open on 55.6% vs 56.6% of speech blocks.
NOISE_MIN_BIAS_DB = 0.0
# Below this is a dropout, not a room: a microphone always delivers some noise,
# so an all-zero block is PortAudio reporting that it had nothing to give. It
# carries no information about the room and must not be allowed to define it.
NOISE_IGNORE_BELOW_DB = -95.0
NOISE_INIT_DB = -60.0
NOISE_FLOOR_DB = -85.0
NOISE_CEIL_DB = -32.0     # a floor estimate above this is tracking speech, not the room

# ── Motion ──────────────────────────────────────────────────────
# Asymmetric spring: a syllable has to snap up and then fall away slowly. Attack
# is under-damped by a hair (ζ<1) so each bar lands with a trace of overshoot —
# that is the difference between "alive" and "interpolated".
ATTACK_OMEGA = 46.0
ATTACK_ZETA = 0.78
RELEASE_OMEGA = 21.0
RELEASE_ZETA = 1.0            # critically damped: falls without a wobble
BOUNCE = 0.18                 # energy kept when a bar hits the base

# Neighbour diffusion, per second. Too little and the bars look like separate
# meters; too much and the spectrum smears into a single blob.
COUPLING_PER_S = 8.0
COUPLING_MAX_STEP = 0.45      # explicit-diffusion stability clamp

MAX_SUBSTEP = 1.0 / 120.0     # physics substep, independent of frame rate

# Resting ripple — what the glyph does while armed and silent. Deliberately
# about a pixel tall: enough that it reads as live, not enough to be motion.
REST_HEIGHT = 0.055
REST_HZ = 0.42
REST_SKEW = 0.09              # phase shift per band → a slow travelling swell

# Processing sweep — a single hump crossing the row while the model decodes.
SWEEP_BANDS_PER_S = 9.0
SWEEP_WIDTH = 1.7
SWEEP_HEIGHT = 0.42
SWEEP_PAD = 3.0

# Outer bands are held slightly shorter so the silhouette reads as framed
# rather than cropped.
EDGE_TAPER_BANDS = 2.0
EDGE_TAPER_FLOOR = 0.80

_EPS = 1e-12
_TAU = math.pi * 2.0


def clamp01(value: float) -> float:
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return float(value)


# The failure shake: the mark slides side to side and settles, the way a macOS
# password field says "no". Small enough to stay legible at 90 pt, three swings
# so it cannot be mistaken for the spectrum moving.
SHAKE_PT = 5.0
SHAKE_S = 0.45
SHAKE_CYCLES = 3.0


def shake_offset(
    t: float, *, amplitude: float = SHAKE_PT, duration: float = SHAKE_S,
    cycles: float = SHAKE_CYCLES,
) -> float:
    """Horizontal offset in points, `t` seconds into a shake. 0 outside it.

    A sine that decays linearly to rest, so it starts and ends exactly at the
    mark's normal position — no jump on either side.
    """
    if t <= 0.0 or t >= duration:
        return 0.0
    envelope = 1.0 - t / duration
    return amplitude * envelope * math.sin(2.0 * math.pi * cycles * t / duration)


def _smoothstep(edge0: float, edge1: float, x: float) -> float:
    if edge1 <= edge0:
        return 1.0 if x >= edge1 else 0.0
    t = clamp01((x - edge0) / (edge1 - edge0))
    return t * t * (3.0 - 2.0 * t)


def band_edges(
    bands: int = BAND_COUNT,
    fft_size: int = FFT_SIZE,
    sample_rate: int = SAMPLE_RATE,
    fmin: float = FREQ_MIN,
    fmax: float = FREQ_MAX,
) -> list[tuple[int, int]]:
    """Log-spaced (bin_lo, bin_hi) pairs, guaranteed strictly increasing.

    Log spacing is what makes the row read as *pitch*: each band covers the same
    musical interval, so a rising voice walks the glyph evenly left to right
    instead of crowding everything into the first two columns.
    """
    nyquist = sample_rate / 2.0
    fmax = min(float(fmax), nyquist * 0.98)
    fmin = max(float(fmin), sample_rate / fft_size)
    bin_hz = sample_rate / float(fft_size)
    ratio = fmax / fmin

    cuts: list[int] = []
    previous = 0
    for k in range(bands + 1):
        freq = fmin * ratio ** (k / bands)
        cut = max(int(round(freq / bin_hz)), previous + 1, 1)
        cuts.append(cut)
        previous = cut
    return [(cuts[k], cuts[k + 1]) for k in range(bands)]


class BandAnalyzer:
    """Microphone blocks → normalised per-band levels and a voice gate.

    `feed()` is called from the sounddevice callback and must stay cheap: one
    1024-point rFFT plus a dozen slice-means, ~40 µs against a 64 ms budget.
    `snapshot()` is called from the render loop on the main thread; the handover
    is a single tuple assignment, which the GIL makes atomic, so neither side
    ever waits on the other.
    """

    def __init__(
        self,
        bands: int = BAND_COUNT,
        sample_rate: int = SAMPLE_RATE,
        fft_size: int = FFT_SIZE,
        fmin: float = FREQ_MIN,
        fmax: float = FREQ_MAX,
    ) -> None:
        self.bands = int(bands)
        self.sample_rate = int(sample_rate)
        self.fft_size = int(fft_size)
        self._window = np.hanning(self.fft_size).astype('float32')
        # Normalise so a full-scale sinusoid reads 0 dB — it keeps every
        # threshold in this module in honest dBFS.
        self._mag_scale = 2.0 / max(float(self._window.sum()), _EPS)
        self._edges = band_edges(self.bands, self.fft_size, self.sample_rate, fmin, fmax)
        self._tilt = [i * TILT_DB_PER_BAND for i in range(self.bands)]
        self._tail = np.zeros(self.fft_size, dtype='float32')
        self._speech_db: float | None = None
        self._noise_db = NOISE_INIT_DB
        self._noise_seeded = False
        self._gate = 0.0
        self._rms_window: deque[float] = deque(
            maxlen=max(2, int(NOISE_WINDOW_S * SAMPLE_RATE / FFT_SIZE))
        )
        self._latest: tuple[tuple[float, ...], float] = (
            tuple([0.0] * self.bands),
            0.0,
        )

    # ── audio thread ────────────────────────────────────────────

    def feed(self, block) -> None:
        """Absorb one mic block. Never raises — this runs on the audio thread."""
        try:
            samples = np.asarray(block, dtype='float32').reshape(-1)
        except Exception:
            return
        if samples.size == 0:
            return

        dt = samples.size / float(self.sample_rate)

        # The caller owns `block` and reuses its buffer, so anything we keep
        # past this call has to be a copy.
        if samples.size >= self.fft_size:
            buf = samples[-self.fft_size:].copy()
        else:
            buf = np.concatenate((self._tail[samples.size:], samples))
        self._tail = buf

        spectrum = np.fft.rfft(buf * self._window)
        power = np.abs(spectrum) * self._mag_scale
        power *= power

        band_db = [0.0] * self.bands
        loudest = AGC_MIN_DB
        mix = BAND_PEAK_MIX
        for k, (lo, hi) in enumerate(self._edges):
            chunk = power[lo:hi]
            if chunk.size:
                energy = (1.0 - mix) * float(chunk.mean()) + mix * float(chunk.max())
            else:
                energy = 0.0
            db = 10.0 * math.log10(energy + _EPS) + self._tilt[k]
            band_db[k] = db
            if db > loudest:
                loudest = db

        # The room and the gate come first: the display window is anchored to
        # them, so they have to be current before any level is computed.
        rms = float(np.sqrt(np.mean(buf * buf)))
        rms_db = 20.0 * math.log10(rms + _EPS)
        if rms_db > NOISE_IGNORE_BELOW_DB:
            self._rms_window.append(rms_db)
        if self._rms_window:
            ranked = sorted(self._rms_window)
            index = int(NOISE_QUANTILE * (len(ranked) - 1))
            target = ranked[index] + NOISE_MIN_BIAS_DB
            if self._noise_seeded:
                self._noise_db += (target - self._noise_db) * NOISE_TRACK_COEFF
            else:
                # Nothing has been measured yet, so there is no estimate to
                # ease away from — NOISE_INIT_DB is a placeholder, not a prior.
                # Crossfading from it just spends the first second of every
                # cold start rendering someone else's room.
                self._noise_seeded = True
                self._noise_db = target
            self._noise_db = min(max(self._noise_db, NOISE_FLOOR_DB), NOISE_CEIL_DB)

        gate = _smoothstep(
            self._noise_db + GATE_SHUT_OVER_NOISE_DB,
            self._noise_db + GATE_OPEN_OVER_NOISE_DB,
            rms_db,
        )

        # Bars start lifting just above the room. This sits below where the
        # gate finishes opening on purpose — the gate already mutes silence, so
        # anchoring the window that high only costs the glyph its height.
        floor_db = self._noise_db + DISPLAY_FLOOR_OVER_NOISE_DB
        if self._speech_db is None:
            self._speech_db = floor_db + SPEECH_SEED_OVER_FLOOR_DB

        # Track the ceiling ONLY while a voice is present. Letting silence move
        # it is exactly what made the glyph bloom the moment you stopped
        # talking, and letting it jump instantly is what made loud speech read
        # no taller than quiet speech.
        if gate > 0.5:
            delta = loudest - self._speech_db
            if delta > 0.0:
                self._speech_db += min(SPEECH_ATTACK_DB_PER_S * dt, delta)
            else:
                self._speech_db -= min(SPEECH_RELEASE_DB_PER_S * dt, -delta)
        self._speech_db = min(max(self._speech_db, AGC_MIN_DB), AGC_MAX_DB)

        span = min(
            max(self._speech_db - floor_db, SPEECH_MIN_SPAN_DB), SPEECH_MAX_SPAN_DB
        )
        gamma = LEVEL_GAMMA
        levels = tuple(
            clamp01((band_db[k] - floor_db) / span) ** gamma
            for k in range(self.bands)
        )

        self._gate = gate
        self._latest = (levels, gate)

    # ── render thread ───────────────────────────────────────────

    def snapshot(self) -> tuple[tuple[float, ...], float]:
        """Latest (band levels, voice gate). Safe from any thread."""
        return self._latest

    def rearm(self) -> None:
        """Start a new dictation: drop the stale reference level, keep the room.

        The AGC only decays while audio is arriving, so without this a loud
        ending would still be the reference minutes later and the first words of
        the next dictation would render as a flat line until it bled off.
        The noise estimate is deliberately kept — the room has not changed, and
        relearning it costs the gate a second every single time.
        """
        self._tail = np.zeros(self.fft_size, dtype='float32')
        self._speech_db = None
        self._gate = 0.0
        self._latest = (tuple([0.0] * self.bands), 0.0)

    def reset(self) -> None:
        """Forget the room and the speaker both."""
        self.rearm()
        self._noise_db = NOISE_INIT_DB
        self._noise_seeded = False
        self._rms_window.clear()


class Spring:
    """A single damped scalar — used for the glyph's reveal.

    Substeps internally so a dropped frame can never make it explode; the whole
    point of the overlay is that it stays smooth when the machine does not.
    """

    def __init__(self, value: float = 0.0, omega: float = 40.0, zeta: float = 1.0) -> None:
        self.value = float(value)
        self.velocity = 0.0
        self.target = float(value)
        self.omega = float(omega)
        self.zeta = float(zeta)

    def snap(self, value: float) -> None:
        self.value = self.target = float(value)
        self.velocity = 0.0

    def tick(self, dt: float) -> float:
        dt = min(max(float(dt), 1e-5), 0.25)
        steps = max(1, int(math.ceil(dt / MAX_SUBSTEP)))
        h = dt / steps
        for _ in range(steps):
            accel = (self.target - self.value) * self.omega * self.omega
            accel -= 2.0 * self.zeta * self.omega * self.velocity
            self.velocity += accel * h
            self.value += self.velocity * h
        return self.value

    @property
    def settled(self) -> bool:
        return abs(self.value - self.target) < 1e-3 and abs(self.velocity) < 1e-2


class BarMotion:
    """Band levels → bar heights, as one coupled membrane.

    States:
      'rest'    armed and silent — a barely-there travelling ripple
      'listen'  driven by the spectrum
      'work'    a single hump sweeping the row while the model decodes
    """

    REST = 'rest'
    LISTEN = 'listen'
    WORK = 'work'

    def __init__(self, bands: int = BAND_COUNT) -> None:
        self.bands = int(bands)
        self._levels = [0.0] * self.bands
        self._velocity = [0.0] * self.bands
        self._taper = _edge_taper(self.bands)
        self._t = 0.0

    def reset(self) -> None:
        self._levels = [0.0] * self.bands
        self._velocity = [0.0] * self.bands
        self._t = 0.0

    @property
    def heights(self) -> tuple[float, ...]:
        return tuple(self._levels)

    def tick(
        self,
        dt: float,
        levels: Sequence[float] | None = None,
        gain: float = 0.0,
        state: str = LISTEN,
    ) -> tuple[float, ...]:
        dt = min(max(float(dt), 1e-5), 0.25)
        steps = max(1, int(math.ceil(dt / MAX_SUBSTEP)))
        h = dt / steps
        for _ in range(steps):
            self._advance(h, levels, gain, state)
        return tuple(self._levels)

    # ── internals ───────────────────────────────────────────────

    def _advance(
        self,
        dt: float,
        levels: Sequence[float] | None,
        gain: float,
        state: str,
    ) -> None:
        self._t += dt
        targets = self._targets(levels, gain, state)

        for i in range(self.bands):
            target = targets[i]
            level = self._levels[i]
            if target > level:
                omega, zeta = ATTACK_OMEGA, ATTACK_ZETA
            else:
                omega, zeta = RELEASE_OMEGA, RELEASE_ZETA
            accel = (target - level) * omega * omega
            accel -= 2.0 * zeta * omega * self._velocity[i]
            velocity = self._velocity[i] + accel * dt
            level += velocity * dt
            if level < 0.0:
                level = 0.0
                velocity = -velocity * BOUNCE if velocity < 0.0 else velocity
            elif level > 1.0:
                level = 1.0
                velocity = -velocity * BOUNCE if velocity > 0.0 else velocity
            self._levels[i] = level
            self._velocity[i] = velocity

        # Membrane coupling: each bar is tugged toward the average of its
        # neighbours. Without it twelve springs simply run side by side and the
        # row reads as twelve widgets instead of one shape.
        k = min(COUPLING_PER_S * dt, COUPLING_MAX_STEP)
        if k > 0.0:
            previous = self._levels
            coupled = []
            last = self.bands - 1
            for i in range(self.bands):
                left = previous[i - 1] if i > 0 else previous[i]
                right = previous[i + 1] if i < last else previous[i]
                coupled.append(previous[i] + k * (left + right - 2.0 * previous[i]))
            self._levels = coupled

    def _targets(
        self,
        levels: Sequence[float] | None,
        gain: float,
        state: str,
    ) -> list[float]:
        if state == self.WORK:
            span = self.bands + 2.0 * SWEEP_PAD
            position = (self._t * SWEEP_BANDS_PER_S) % span - SWEEP_PAD
            width = 2.0 * SWEEP_WIDTH * SWEEP_WIDTH
            return [
                SWEEP_HEIGHT * math.exp(-((i - position) ** 2) / width)
                for i in range(self.bands)
            ]

        rest = [
            REST_HEIGHT * (0.5 + 0.5 * math.sin(_TAU * (self._t * REST_HZ - i * REST_SKEW)))
            for i in range(self.bands)
        ]
        if state == self.REST or not levels:
            return rest

        g = clamp01(gain)
        out = []
        for i in range(self.bands):
            voice = clamp01(levels[i]) * g * self._taper[i]
            # The ripple survives only in the gap the voice leaves behind, so
            # there is never a moment where two idioms of motion are visible.
            out.append(max(voice, rest[i] * (1.0 - g)))
        return out


def _edge_taper(bands: int) -> list[float]:
    out = []
    for i in range(bands):
        distance = min(i, bands - 1 - i)
        t = clamp01((distance + 0.5) / EDGE_TAPER_BANDS)
        out.append(EDGE_TAPER_FLOOR + (1.0 - EDGE_TAPER_FLOOR) * (t * t * (3.0 - 2.0 * t)))
    return out


@dataclass(frozen=True)
class GlyphGeometry:
    """Points, on screen, for one drawn glyph.

    Columns are gapless by design: the reference mark is a single stepped
    silhouette standing on a continuous base, and shared edges are what stop it
    from reading as a row of separate bars.
    """

    bands: int = BAND_COUNT
    column_width: float = 7.5
    base_height: float = 2.5
    bar_height: float = 16.5
    collapsed_scale: float = 0.24

    @property
    def width(self) -> float:
        return self.bands * self.column_width

    @property
    def height(self) -> float:
        return self.base_height + self.bar_height


def silhouette(
    levels: Sequence[float],
    geometry: GlyphGeometry,
    reveal: float = 1.0,
    origin: tuple[float, float] = (0.0, 0.0),
    snap: float = 0.0,
) -> list[tuple[float, float]]:
    """The whole glyph as one closed polygon, bottom-left → around → close.

    `reveal` (0–1) scales the shape horizontally about its centre *and* scales
    the columns down into the base, which is how the overlay enters and leaves:
    one number retracts the whole mark, so the last thing on screen is a
    closing line rather than a shrinking lump of spectrum.

    `snap` quantises x to a device pixel so the vertical steps stay crisp; the
    tops are deliberately left unsnapped so height changes stay sub-pixel smooth
    instead of stair-stepping as they move.
    """
    x0, y0 = origin
    centre = x0 + geometry.width / 2.0
    scale = geometry.collapsed_scale + (1.0 - geometry.collapsed_scale) * clamp01(reveal)

    def sx(x: float) -> float:
        value = centre + (x - centre) * scale
        if snap > 0.0:
            value = round(value / snap) * snap
        return value

    rise = clamp01(reveal) * geometry.bar_height
    points: list[tuple[float, float]] = [(sx(x0), y0)]
    for i in range(geometry.bands):
        level = clamp01(levels[i]) if i < len(levels) else 0.0
        top = y0 + geometry.base_height + level * rise
        points.append((sx(x0 + i * geometry.column_width), top))
        points.append((sx(x0 + (i + 1) * geometry.column_width), top))
    points.append((sx(x0 + geometry.width), y0))
    return points
