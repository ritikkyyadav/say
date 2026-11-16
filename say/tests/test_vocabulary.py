"""The vocabulary engine — what the transcripts say about the speaker.

The interesting tests here are the two that stop it lying:

* a decoder repetition loop must not be able to invent a vocabulary (this
  really happened — `drag`, 218 times inside a single dictation, was the
  most-used "word" in the corpus until the filter existed), and
* a thin corpus must produce no insights at all rather than confident
  nonsense about four sentences.
"""

from __future__ import annotations

import os
import sys
import unittest
from datetime import UTC, datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import vocabulary as V  # noqa: E402


def row(text: str, *, when: datetime | None = None, seconds: float = 10.0) -> dict:
    when = when or datetime(2026, 8, 20, 15, 30, tzinfo=UTC)
    return {
        'text': text,
        'ts_utc': when.strftime('%Y-%m-%dT%H:%M:%SZ'),
        'audio_seconds': seconds,
    }


def corpus(sentence: str, n: int = 40, **kw) -> list[dict]:
    """`n` dictations of the same sentence — enough to clear the thresholds."""
    base = datetime(2026, 8, 20, 15, 30, tzinfo=UTC)
    return [row(sentence, when=base + timedelta(days=i % 7, hours=0), **kw) for i in range(n)]


class DegenerateTranscripts(unittest.TestCase):
    def test_a_repetition_loop_is_degenerate(self):
        self.assertTrue(V.is_degenerate('available ' * 40))

    def test_a_short_repeat_is_not_degenerate(self):
        # "yes yes yes" is a thing people say; the ratio test has no power at
        # four words and must not pretend otherwise.
        self.assertFalse(V.is_degenerate('yes yes yes yes'))

    def test_ordinary_prose_is_not_degenerate(self):
        text = ('I want you to go ahead and rebuild the front end so the '
                'insights land first and the transcripts follow underneath.')
        self.assertFalse(V.is_degenerate(text))

    def test_a_loop_cannot_reach_the_vocabulary(self):
        """The regression this filter exists for.

        One degenerate row carried 218 copies of a word and put it straight
        into the top ten. The row is excluded from the *counts*; it is still a
        transcript, we simply do not believe its vocabulary.
        """
        rows = corpus('we should ship the desktop build today and measure it', n=40)
        rows.append(row('drag ' * 220))
        report = V.analyse(rows)

        self.assertEqual(report.excluded, 1)
        self.assertNotIn('drag', [w.word for w in report.top_words])
        self.assertNotIn('drag', ' '.join(i.headline for i in report.insights))


class Thresholds(unittest.TestCase):
    def test_a_thin_corpus_offers_no_insights(self):
        report = V.analyse([row('hello there'), row('this is short')])
        self.assertFalse(report.enough_data)
        self.assertEqual(report.insights, [])
        # …but it still reports what it honestly knows.
        self.assertGreater(report.total_words, 0)

    def test_a_thin_corpus_still_reports_what_it_measured(self):
        """Counts yes, conclusions no.

        `longest_*` is an observation, not a claim about the speaker, so it
        belongs in the thin report alongside the word count — it used to be
        dropped there while `sentence_avg` survived, which is just arbitrary.
        """
        report = V.analyse([row('one two three four five six seven', seconds=42.0)])
        self.assertFalse(report.enough_data)
        self.assertEqual(report.longest_words, 7)
        self.assertEqual(report.longest_seconds, 42.0)
        self.assertGreater(report.typing_seconds, 0)

    def test_an_empty_corpus_is_not_an_error(self):
        report = V.analyse([])
        self.assertFalse(report.enough_data)
        self.assertEqual(report.total_words, 0)
        self.assertEqual(report.insights, [])

    def test_a_word_confined_to_one_dictation_is_not_a_habit(self):
        """Talking about kubernetes once does not make it a personality trait."""
        rows = corpus('we should ship the desktop build today and measure it', n=40)
        rows.append(row('kubernetes ' + 'and the cluster is fine ' * 12))
        report = V.analyse(rows)
        self.assertNotIn('kubernetes', [w.word for w in report.top_words])

    def test_malformed_rows_do_not_raise(self):
        rows = corpus('we should ship the desktop build today and measure it', n=40)
        rows += [{}, {'text': None}, {'text': 'ok', 'ts_utc': 'not-a-date'}]
        report = V.analyse(rows)          # must not raise
        self.assertTrue(report.enough_data)


class WordsAndPhrases(unittest.TestCase):
    def setUp(self):
        # 60 dictations that all contain the tic and the phrase, plus enough
        # other words to clear MIN_WORDS.
        self.report = V.analyse(corpus(
            'so like I want you to just go ahead and rebuild the whole front '
            'end because like the transcripts should follow the insights.',
            n=60,
        ))

    def test_stopwords_never_appear_in_top_words(self):
        words = {w.word for w in self.report.top_words}
        self.assertNotIn('the', words)
        self.assertNotIn('you', words)
        self.assertNotIn('and', words)

    def test_the_tic_is_found_and_counted(self):
        self.assertEqual(self.report.fillers[0].word, 'like')
        self.assertEqual(self.report.fillers[0].count, 120)   # twice per dictation
        self.assertGreater(self.report.fillers[0].per, 1)

    def test_a_trigram_absorbs_the_bigrams_inside_it(self):
        """Otherwise one habit is reported three times.

        "just go ahead", "just go" and "go ahead" are the same thing said
        once; listing all three reads as three separate discoveries.
        """
        phrases = [p.word for p in self.report.phrases]
        self.assertIn('just go ahead', phrases)
        self.assertNotIn('just go', phrases)
        self.assertNotIn('go ahead', phrases)

    def test_phrases_never_begin_or_end_on_a_bare_function_word(self):
        for phrase in self.report.phrases:
            first, *_, last = phrase.word.split(' ')
            self.assertNotIn(first, V.STOPWORDS, f'{phrase.word} starts on a fragment')
            self.assertNotIn(last, V.STOPWORDS, f'{phrase.word} ends on a fragment')

    def test_per_is_one_in_n_words(self):
        for stat in self.report.top_words:
            expected = max(1, round(self.report.total_words / stat.count))
            self.assertEqual(stat.per, expected)

    def test_spread_is_a_share_of_dictations(self):
        for stat in self.report.top_words:
            self.assertGreaterEqual(stat.spread, 0.0)
            self.assertLessEqual(stat.spread, 1.0)


class Rhythm(unittest.TestCase):
    def test_hours_and_weekdays_bucket_by_local_time(self):
        report = V.analyse(corpus('we should ship the desktop build today', n=40))
        self.assertEqual(len(report.hours), 24)
        self.assertEqual(len(report.weekdays), 7)
        self.assertEqual(sum(report.hours), report.total_words)
        self.assertEqual(sum(report.weekdays), report.dictations)

    def test_typing_time_is_derived_from_the_word_count(self):
        report = V.analyse(corpus('we should ship the desktop build today', n=40))
        self.assertAlmostEqual(
            report.typing_seconds, report.total_words / V.TYPING_WPM * 60.0, places=0
        )

    def test_the_longest_dictation_is_the_longest_one(self):
        rows = corpus('we should ship the desktop build today', n=80)
        # Genuinely varied prose. Two traps to avoid when writing a fixture
        # for this module: `'x ' * 40` is a decoder loop by its own definition,
        # and so is `f'word{i} …'` — the tokeniser keeps letters only, so every
        # numeric suffix collapses back onto the same word.
        long_thought = (
            'I want the insights panel to land first and the transcripts to '
            'follow underneath so nobody has to hunt for a second tab, and '
            'while we are there please remove every badge about privacy '
            'because it reads as marketing rather than information, then '
            'ship a build I can actually test today.'
        )
        rows.append(row(long_thought, seconds=300.0))
        report = V.analyse(rows)
        self.assertTrue(report.enough_data)
        self.assertEqual(report.longest_seconds, 300.0)
        self.assertEqual(report.excluded, 0)


class Insights(unittest.TestCase):
    def setUp(self):
        self.report = V.analyse(corpus(
            'so like I want you to just go ahead and rebuild the whole front '
            'end because like the transcripts should follow the insights.',
            n=60,
        ))

    def test_the_crutch_insight_leads(self):
        self.assertEqual(self.report.insights[0].id, 'crutch')
        self.assertIn('*like*', self.report.insights[0].headline)

    def test_every_insight_carries_a_drawable_viz(self):
        for insight in self.report.insights:
            self.assertIn(insight.viz.get('kind'), {'dots', 'bars'})
            if insight.viz['kind'] == 'bars':
                self.assertTrue(insight.viz['rows'])
            else:
                self.assertGreaterEqual(insight.viz['every'], 2)

    def test_insight_ids_are_unique(self):
        ids = [i.id for i in self.report.insights]
        self.assertEqual(len(ids), len(set(ids)))

    def test_the_report_serialises_to_plain_json_types(self):
        import json

        json.dumps(self.report.to_dict())     # must not raise


if __name__ == '__main__':
    unittest.main()
