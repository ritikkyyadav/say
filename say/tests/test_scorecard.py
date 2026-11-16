"""The scorecard's formulas: unmeasured scores nothing, bars are explicit."""
from __future__ import annotations

from importlib import import_module

scorecard = import_module('tools.scorecard')


def test_too_few_presses_is_unmeasured_not_perfect():
    c = scorecard.reliability(['delivered'] * 5)
    assert not c.measured
    assert c.points == 0


def test_cancels_and_silence_are_not_held_against_reliability():
    outcomes = ['delivered'] * 200 + ['cancelled'] * 50 + ['silence'] * 50 + ['too_short'] * 20
    c = scorecard.reliability(outcomes)
    assert c.measured
    assert c.points == 25


def test_the_reliability_bar_is_99_5_percent():
    at_bar = ['delivered'] * 995 + ['error:injection'] * 5
    below = ['delivered'] * 95 + ['refused_busy'] * 5
    assert scorecard.reliability(at_bar).points == 25
    assert 0 < scorecard.reliability(below).points < 25
    assert 'refused_busy 5' in scorecard.reliability(below).evidence


def test_held_counts_against_delivery():
    c = scorecard.reliability(['delivered'] * 90 + ['held'] * 10)
    assert c.points == 0  # 90% is the floor


def test_speed_scores_the_slow_end_not_the_median():
    fast_median_slow_tail = [1.0] * 80 + [8.0] * 20
    c = scorecard.speed(fast_median_slow_tail)
    assert c.points == 0  # p90 is 8 s, past the 6 s floor
    assert scorecard.speed([1.0] * 20).points == 15


def test_accuracy_waits_for_enough_reviews():
    assert not scorecard.accuracy({'reviewed': 10, 'word_rate': 0.01}).measured
    c = scorecard.accuracy({'reviewed': 50, 'word_rate': 0.03})
    assert c.measured and c.points == 20


def test_convenience_counts_only_what_has_shipped():
    c = scorecard.convenience([('a', 3, 'shipped'), ('b', 5, 'planned')])
    assert c.points == 3
    assert 'b' in c.evidence


def test_reach_credits_hindi_only_once_it_is_measured():
    assert scorecard.reach(0).points == 6
    assert scorecard.reach(30).points == 8


def test_footprint_without_a_running_engine_does_not_guess_memory():
    c = scorecard.footprint(None)
    assert not c.measured
    assert c.points == 4
    assert scorecard.footprint(400).points == 10
    assert scorecard.footprint(3000).points == 4


def test_the_weights_add_up_to_100():
    cats = [scorecard.reliability([]), scorecard.speed([]), scorecard.accuracy({}),
            scorecard.convenience(), scorecard.reach(0), scorecard.footprint(None)]
    assert sum(c.weight for c in cats) == 100


def test_render_says_what_is_not_credited():
    text = scorecard.render([scorecard.reliability([]), scorecard.convenience()], 30)
    assert 'Not yet measured, so not credited: Reliability' in text


def test_the_footprint_reading_is_physical_not_rss():
    out = 'Say [52935]: 64-bit    Footprint: 3781 MB (16384 bytes per page)\n    phys_footprint: 3781 MB\n'
    assert scorecard.parse_footprint(out) == 3781
    assert scorecard.parse_footprint('phys_footprint: 1.5 GB') == 1536
    assert scorecard.parse_footprint('nothing here') is None
    # 3.7 GB idle is near the floor, not the 10/10 a 50 MB RSS reading gave.
    assert scorecard.footprint(3781).points == 4
