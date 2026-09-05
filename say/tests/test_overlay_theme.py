"""Overlay colour themes.

The load-bearing test in here is `DefaultIsUnchanged`: the whole point of
shipping themes is that nobody who never opens the customiser can tell they
exist. `ink` must keep resolving to "adaptive", which is the signal that
`overlay.py` should follow the system appearance exactly as it did before this
module was written.

The rest is the fail-safe contract: every bad input — an unknown id, a garbage
colour, an empty custom list — falls back to the adaptive glyph rather than
raising, because a broken settings file must never be able to leave someone
dictating with no on-screen feedback at all.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import overlay_theme as T  # noqa: E402


class HexParsing(unittest.TestCase):
    def test_six_digit_with_and_without_hash(self):
        self.assertEqual(T.parse_hex('#FF0000'), T.parse_hex('ff0000'))
        r, g, b = T.parse_hex('#FF0000')
        self.assertAlmostEqual(r, 1.0)
        self.assertAlmostEqual(g, 0.0)
        self.assertAlmostEqual(b, 0.0)

    def test_three_digit_shorthand_expands(self):
        self.assertEqual(T.parse_hex('#0f0'), T.parse_hex('#00FF00'))

    def test_garbage_raises(self):
        for bad in ('', 'red', '#12', '#1234567', 'zzzzzz', None):
            with self.assertRaises(T.BadColor):
                T.parse_hex(bad)  # type: ignore[arg-type]

    def test_normalise_is_canonical(self):
        # The same colour must never persist two ways, or the UI cannot tell
        # whether the swatch it is showing is the one that is selected.
        self.assertEqual(T.normalise_hex('#0f0'), '#00FF00')
        self.assertEqual(T.normalise_hex('d9713f'), '#D9713F')

    def test_clean_colors_caps_the_list(self):
        many = ['#111111'] * (T.MAX_COLORS + 4)
        self.assertEqual(len(T.clean_colors(many)), T.MAX_COLORS)

    def test_clean_colors_rejects_rather_than_silently_dropping(self):
        with self.assertRaises(T.BadColor):
            T.clean_colors(['#FF0000', 'nope'])


class DefaultIsUnchanged(unittest.TestCase):
    """The compatibility guarantee. Break these and every existing user's
    glyph changes appearance on upgrade."""

    def test_ink_is_adaptive(self):
        self.assertIsNone(T.resolve('ink'))

    def test_ink_carries_no_colours(self):
        ink = T.get('ink')
        self.assertIsNotNone(ink)
        self.assertEqual(ink.colors, ())
        self.assertTrue(ink.adaptive)

    def test_ink_is_the_first_preset(self):
        self.assertEqual(T.catalog()[0].id, 'ink')

    def test_no_argument_and_empty_string_both_mean_ink(self):
        self.assertIsNone(T.resolve(''))
        self.assertIsNone(T.resolve('   '))


class FailSafe(unittest.TestCase):
    def test_unknown_theme_falls_back_to_adaptive(self):
        self.assertIsNone(T.resolve('chartreuse-explosion'))

    def test_custom_with_no_colours_falls_back(self):
        self.assertIsNone(T.resolve('custom', []))
        self.assertIsNone(T.resolve('custom', None))

    def test_custom_with_only_bad_colours_falls_back(self):
        # Not an exception: a corrupt settings file must not cost the glyph.
        self.assertIsNone(T.resolve('custom', ['nope', '###']))

    def test_custom_keeps_the_good_colours_and_skips_the_bad(self):
        resolved = T.resolve('custom', ['#FF0000', 'nope', '#0000FF'])
        self.assertIsNotNone(resolved)
        colors, _rim = resolved
        self.assertEqual(len(colors), 2)

    def test_custom_respects_the_cap(self):
        resolved = T.resolve('custom', ['#111111'] * 20)
        self.assertEqual(len(resolved[0]), T.MAX_COLORS)


class Presets(unittest.TestCase):
    def test_every_preset_colour_parses(self):
        for theme in T.catalog():
            for value in theme.colors:
                T.parse_hex(value)          # must not raise

    def test_preset_ids_are_unique(self):
        ids = [t.id for t in T.catalog()]
        self.assertEqual(len(ids), len(set(ids)))

    def test_custom_is_not_a_preset_but_is_a_valid_id(self):
        self.assertNotIn(T.CUSTOM_ID, T.PRESET_IDS)
        self.assertIn(T.CUSTOM_ID, T.VALID_THEME_IDS)

    def test_a_two_colour_preset_resolves_to_two_colours(self):
        colors, _rim = T.resolve('signal')
        self.assertEqual(len(colors), 2)

    def test_to_dict_is_json_shaped(self):
        import json

        json.dumps([T.to_dict(t) for t in T.catalog()])   # must not raise


class AutoRim(unittest.TestCase):
    """The rim is legibility, not taste — so it is computed, never chosen."""

    def test_a_bright_pair_gets_a_dark_rim(self):
        rim = T.auto_rim([T.parse_hex('#FFFFFF'), T.parse_hex('#E2402E')])
        self.assertEqual(rim, T.TONE_DARK)

    def test_a_dark_pair_gets_a_light_rim(self):
        rim = T.auto_rim([T.parse_hex('#17150F'), T.parse_hex('#3F7D52')])
        self.assertEqual(rim, T.TONE_LIGHT)

    def test_the_rim_is_always_one_of_the_two_tones(self):
        for theme in T.catalog():
            resolved = T.resolve(theme.id)
            if resolved is None:
                continue
            _colors, rim = resolved
            self.assertIn(rim, (T.TONE_DARK, T.TONE_LIGHT), theme.id)

    def test_no_colours_still_returns_a_tone(self):
        self.assertIn(T.auto_rim([]), (T.TONE_DARK, T.TONE_LIGHT))


class LiveOverlayRegistry(unittest.TestCase):
    def tearDown(self):
        T.register_overlay(None)

    def test_nothing_is_registered_by_default(self):
        T.register_overlay(None)
        self.assertIsNone(T.live_overlay())

    def test_register_then_read(self):
        sentinel = object()
        T.register_overlay(sentinel)
        self.assertIs(T.live_overlay(), sentinel)



class Patterns(unittest.TestCase):
    """How the colours get distributed across the columns.

    The whole reason these exist: `alternate` colours by *column index*, and
    an index never changes, so it draws the identical stripe in every frame of
    every dictation. Everything else colours from the audio, which is what
    makes the row read as one coordinated field that never repeats.
    """

    HEIGHTS = [0.10, 0.82, 0.31, 0.95, 0.22, 0.61, 0.44, 0.90, 0.15, 0.70, 0.52, 0.27]

    def test_every_advertised_pattern_is_implemented(self):
        for info in T.PATTERN_INFO:
            self.assertIn(info['id'], T.VALID_PATTERNS)
            self.assertTrue(info['name'] and info['blurb'])
        self.assertEqual(len(T.PATTERN_INFO), len(T.VALID_PATTERNS))

    def test_indices_are_always_in_range(self):
        for pattern in T.VALID_PATTERNS:
            for n in (1, 2, 3, T.MAX_COLORS):
                out = T.column_colors(pattern, n, self.HEIGHTS, t=1.3)
                self.assertEqual(len(out), len(self.HEIGHTS), pattern)
                self.assertTrue(all(0 <= i < n for i in out), (pattern, n, out))

    def test_one_colour_is_always_index_zero(self):
        for pattern in T.VALID_PATTERNS:
            self.assertEqual(
                T.column_colors(pattern, 1, self.HEIGHTS), [0] * len(self.HEIGHTS)
            )

    def test_no_heights_is_not_an_error(self):
        self.assertEqual(T.column_colors('pulse', 2, []), [])

    def test_alternate_is_the_stripe_it_is_named_after(self):
        self.assertEqual(
            T.column_colors('alternate', 2, self.HEIGHTS),
            [0, 1] * 6,
        )

    def test_alternate_ignores_the_audio_entirely(self):
        """The behaviour every other pattern exists to escape."""
        quiet = [0.05] * 12
        loud = [0.95] * 12
        self.assertEqual(
            T.column_colors('alternate', 2, quiet),
            T.column_colors('alternate', 2, loud),
        )

    def test_pulse_changes_when_the_audio_changes(self):
        quiet = [0.05] * 12
        loud = [0.95] * 12
        self.assertNotEqual(
            T.column_colors('pulse', 2, quiet),
            T.column_colors('pulse', 2, loud),
        )

    def test_pulse_does_not_collapse_across_the_speaking_range(self):
        """A flat spectrum must still carry more than one colour.

        Colouring on loudness alone turns the whole mark one colour the moment
        every band reads the same, which looks like the theme broke. That is
        what PULSE_SCATTER is for — and it has to be big enough: measured, 0.35
        still went uniform everywhere below 0.35 and above 0.65, i.e. across
        most of the range anyone actually speaks in.
        """
        for level in (0.1, 0.2, 0.35, 0.5, 0.65, 0.8, 0.9):
            out = T.column_colors('pulse', 2, [level] * 12)
            self.assertGreater(len(set(out)), 1, f'flat at {level} collapsed to one colour')

    def test_pulse_is_deliberately_uniform_at_the_extremes(self):
        """Silence and saturation *should* be one colour.

        Every band really is the same then, and the glyph saying so is the
        colour reporting the truth rather than a bug.
        """
        self.assertEqual(len(set(T.column_colors('pulse', 2, [0.0] * 12))), 1)
        self.assertEqual(len(set(T.column_colors('pulse', 2, [1.0] * 12))), 1)
        # …and they are not the *same* colour.
        self.assertNotEqual(
            T.column_colors('pulse', 2, [0.0] * 12),
            T.column_colors('pulse', 2, [1.0] * 12),
        )

    def test_pulse_puts_louder_bars_on_a_later_colour(self):
        """Averaged over the row, louder must mean further up the palette."""
        quiet = T.column_colors('pulse', 3, [0.05] * 12)
        loud = T.column_colors('pulse', 3, [0.95] * 12)
        self.assertGreater(sum(loud) / 12, sum(quiet) / 12)

    def test_flow_travels_over_time(self):
        still = T.column_colors('flow', 2, self.HEIGHTS, t=0.0)
        later = T.column_colors('flow', 2, self.HEIGHTS, t=1.0)
        self.assertNotEqual(still, later)

    def test_drift_is_stable_but_not_a_stripe(self):
        first = T.column_colors('drift', 2, self.HEIGHTS, t=0.0)
        second = T.column_colors('drift', 2, self.HEIGHTS, t=9.0)
        self.assertEqual(first, second, 'drift must not move')
        self.assertNotEqual(first, [0, 1] * 6, 'drift must not be the stripe')

    def test_spark_lights_only_the_peaks(self):
        out = T.column_colors('spark', 2, self.HEIGHTS)
        lit = [i for i, v in enumerate(out) if v == 1]
        # round(12 * 0.3) = 4 bars: indices 3 (.95), 7 (.90), 1 (.82), 9 (.70).
        self.assertEqual(sorted(lit), [1, 3, 7, 9])
        self.assertEqual(len(lit), max(1, round(12 * T.SPARK_SHARE)))

    def test_spark_works_at_any_volume(self):
        """Ranked, not thresholded — a whisper must still light its own peaks."""
        whisper = [h * 0.05 for h in self.HEIGHTS]
        self.assertEqual(
            T.column_colors('spark', 2, whisper),
            T.column_colors('spark', 2, self.HEIGHTS),
        )

    def test_an_unknown_pattern_falls_back_to_the_default(self):
        self.assertEqual(
            T.column_colors('interpretive-dance', 2, self.HEIGHTS),
            T.column_colors(T.DEFAULT_PATTERN, 2, self.HEIGHTS),
        )

    def test_the_default_is_not_the_stripe(self):
        self.assertNotEqual(T.DEFAULT_PATTERN, T.ALTERNATE)

if __name__ == '__main__':
    unittest.main()
