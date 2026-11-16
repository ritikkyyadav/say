"""Corrections -> vocabulary candidates the user can approve."""
from __future__ import annotations

from unittest.mock import patch

import suggestions


def _row(tid, inserted, corrected, day='2026-10-01'):
    return {'transcript_id': tid, 'inserted': inserted, 'corrected': corrected,
            'ts_utc': f'{day}T10:00:00Z'}


def test_a_one_word_pair_is_widened_with_the_word_that_follows():
    """Alignment ignores case, so the raw pair here is the bare
    ('cloud', 'Claude') — a rule that would rewrite every cloud."""
    pairs = suggestions.pairs_in('open cloud code now', 'open Claude Code now')
    assert pairs == [('cloud code', 'Claude Code', False)]


def test_with_nothing_after_it_the_pair_takes_the_word_before():
    pairs = suggestions.pairs_in('use the s50', 'use the STT')
    assert pairs == [('the s50', 'the STT', False)]


def test_a_lone_word_cannot_be_widened_and_says_so():
    assert suggestions.pairs_in('gohercel', 'Vercel') == [('gohercel', 'Vercel', True)]


def test_a_formatting_only_change_is_not_a_candidate():
    assert suggestions.pairs_in('hello world', 'Hello, world.') == []


def test_continuation_is_not_a_candidate():
    assert suggestions.pairs_in('ship it.', 'ship it. then rest') == []


def test_repeats_are_counted_across_days_and_against_the_whole_corpus():
    rows = [
        _row(1, 'open cloud code now', 'open Claude Code now', '2026-09-01'),
        _row(2, 'ask cloud code first', 'ask Claude Code first', '2026-09-01'),
        _row(3, 'run cloud code again', 'run Claude Code again', '2026-09-03'),
    ]
    transcripts = [
        {'id': 1, 'text': 'open cloud code now'},
        {'id': 2, 'text': 'ask cloud code first'},
        {'id': 3, 'text': 'run cloud code again'},
        {'id': 4, 'text': 'the cloud code path is slow and cloud, code is fine'},
        {'id': 5, 'text': 'clouds are not code'},
    ]
    (s,) = suggestions.collect(rows, transcripts)
    assert (s.wrong, s.right) == ('cloud code', 'Claude Code')
    assert s.confirmations == 3
    assert s.days == 2
    assert s.conflicts == 0
    assert s.corpus_matches == 5  # three corrected rows + two in row 4, none in row 5
    assert not s.single_word
    assert len(s.examples) == suggestions.MAX_EXAMPLES


def test_a_phrase_corrected_two_different_ways_is_a_conflict():
    rows = [
        _row(1, 'the rapper class', 'the wrapper class'),
        _row(2, 'the rapper class', 'the mapper class'),
    ]
    stats = {s.right: s for s in suggestions.collect(rows, [])}
    assert stats['wrapper class'].conflicts == 1
    assert stats['mapper class'].conflicts == 1


def test_a_transcript_checked_and_left_alone_counts_against_a_rule():
    """The user heard that clip and kept 'cloud code'. A rule rewriting it
    there would have been wrong."""
    rows = [
        _row(1, 'open cloud code now', 'open Claude Code now'),
        _row(2, 'the cloud code path', 'the cloud code path'),  # checked, right
    ]
    transcripts = [{'id': 1, 'text': 'open cloud code now'},
                   {'id': 2, 'text': 'the cloud code path'}]
    (s,) = suggestions.collect(rows, transcripts)
    assert s.corpus_matches == 2
    assert s.kept_as_typed == 1


def test_nothing_is_suggested_until_the_rule_is_written_or_says_yes():
    rows = [_row(1, 'open cloud code now', 'open Claude Code now')]
    with patch.object(suggestions, 'should_suggest', lambda s: False):
        assert suggestions.suggest(rows, []) == []
    with patch.object(suggestions, 'should_suggest', lambda s: True):
        assert [s.wrong for s in suggestions.suggest(rows, [])] == ['cloud code']


def test_a_rule_the_user_already_has_is_not_offered_again():
    rows = [_row(1, 'open cloud code now', 'open Claude Code now')]
    with patch.object(suggestions, 'should_suggest', lambda s: True):
        assert suggestions.suggest(rows, [], existing={'Cloud Code': 'Claude Code'}) == []
