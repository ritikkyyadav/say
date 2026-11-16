"""The accuracy benchmark's arithmetic: held-out split, word error rate, choice."""
from __future__ import annotations

from importlib import import_module

bench = import_module('tools.accuracy_bench')


def _clips(n):
    return [{'id': i, 'ts_utc': f'2026-10-{i + 1:02d}T10:00:00Z'} for i in range(n)]


def test_the_newest_third_is_held_out():
    tuning, holdout = bench.split_holdout(_clips(9))
    assert [c['id'] for c in holdout] == [6, 7, 8]
    assert len(tuning) == 6


def test_too_few_clips_to_hold_any_out():
    tuning, holdout = bench.split_holdout(_clips(2))
    assert holdout == [] and len(tuning) == 2


def test_word_error_rate_counts_every_kind_of_mistake():
    rate, errors, words = bench.word_error_rate([
        ('open the settings page', 'open the setting page'),   # substitution
        ('ship it today', 'ship it'),                          # words missed at the end
        ('Hello, world.', 'hello world'),                      # case and punctuation: fine
    ])
    assert (errors, words) == (2, 9)
    assert abs(rate - 2 / 9) < 1e-9


def test_the_recommendation_uses_the_held_out_number_and_names_a_faster_near_tie():
    rows = [
        {'name': 'what Say typed at the time', 'wer': 0.20, 'holdout_wer': 0.20, 'decode_p90': None},
        {'name': 'turbo', 'wer': 0.05, 'holdout_wer': 0.060, 'decode_p90': 4.0},
        {'name': 'tuned-on-itself', 'wer': 0.01, 'holdout_wer': 0.090, 'decode_p90': 4.0},
        {'name': 'moonshine', 'wer': 0.07, 'holdout_wer': 0.065, 'decode_p90': 0.5},
    ]
    text = bench.recommend(rows)
    assert 'Most accurate on the held-out clips: turbo' in text
    assert 'faster: moonshine' in text


def test_no_faster_option_is_invented():
    rows = [{'name': 'turbo', 'wer': 0.05, 'holdout_wer': 0.05, 'decode_p90': 2.0},
            {'name': 'slow-and-worse', 'wer': 0.2, 'holdout_wer': 0.2, 'decode_p90': 1.0}]
    assert 'faster' not in bench.recommend(rows)
