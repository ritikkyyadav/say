"""What the transcripts say about the person who spoke them.

This is the analysis behind the Home screen: not "how many words" — the store
already answers that — but *which* words, how often they come back, and which
of them the speaker has no idea they are using. A crutch word is invisible from
the inside; the whole point of counting is that nobody can count their own.

Pure and offline by construction: it takes rows and returns a report. No
AppKit, no SQL, no clock unless one is handed in. That is what makes the
thresholds below testable rather than merely plausible.

Two rules carry the accuracy of everything downstream:

* **Degenerate transcripts are excluded, not counted.** A decoder repetition
  loop (see `postprocess.collapse_repetitions`) can put 218 copies of one word
  into a single row. Left in, it invents a vocabulary the user does not have —
  against the real corpus, `drag` (218×, all from one dictation) and
  `available` (226× across nine) both walked straight into the top ten. The
  unique-word-ratio filter drops them; the transcripts themselves are kept, it
  is only their *vocabulary* that is untrustworthy.

* **Nothing is reported until there is enough to report.** With four
  dictations, "you say 'the' once every 18 words" is noise wearing a lab coat.
  Below `MIN_WORDS` / `MIN_DICTATIONS` the report comes back with counts and no
  insights, and the UI says so instead of dressing up an accident.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from typing import Any

# ── Tokenising ──────────────────────────────────────────────────────────────
# Apostrophes stay inside a word ("don't" is one word, not two); everything
# else splits. Deliberately not a full tokeniser — the input is already
# lowercase-able English prose from a decoder, not arbitrary text.
_WORD_RE = re.compile(r"[a-z]+(?:'[a-z]+)*")
_SENTENCE_RE = re.compile(r'[.!?]+')

# Words that are structural rather than characteristic. Everyone uses these at
# roughly the same rate, so leaving them in produces the same top-ten for every
# human being alive and tells the user nothing about themselves.
STOPWORDS: frozenset[str] = frozenset(["a", "an", "and", "are", "as", "at", "be", "been", "being", "but", "by", "can", "could", "did", "do", "does", "doing", "done", "for", "from", "had", "has", "have", "having", "he", "her", "hers", "him", "his", "how", "i", "if", "in", "into", "is", "it", "its", "me", "my", "of", "on", "or", "our", "ours", "out", "she", "should", "so", "than", "that", "the", "their", "theirs", "them", "then", "there", "these", "they", "this", "those", "to", "us", "was", "we", "were", "what", "when", "where", "which", "who", "whom", "will", "with", "would", "you", "your", "yours", "am", "been", "shall", "may", "might", "must", "not", "no", "nor", "s", "t", "don't", "it's", "i'm", "that's", "isn't", "aren't", "wasn't", "won't", "can't", "didn't", "doesn't", "i've", "you're", "we're", "they're", "i'll", "he's", "she's", "here", "o'clock"])

# Words that are *not* structural but are still nobody's signature — the
# speaker is describing the act of speaking. Kept out of "most-said" so the
# list stays about them.
_DISCOURSE_ONLY: frozenset[str] = frozenset({'um', 'uh', 'erm', 'mmm', 'hmm'})

# The verbal tics worth naming. Each is something people say without choosing
# to; the report measures how often, never whether it is a problem.
FILLERS: tuple[str, ...] = (
    'like', 'just', 'actually', 'basically', 'literally', 'really', 'okay',
    'yeah', 'right', 'so', 'well', 'kind of', 'sort of', 'you know', 'i mean',
)

# ── Thresholds ──────────────────────────────────────────────────────────────
# A transcript whose unique-word ratio falls below this is a decoder loop, not
# speech. Measured against the real corpus: genuine dictations sit at 0.30–0.85
# even when repetitive; the four degenerate rows sat at 0.02–0.14.
DEGENERATE_UNIQUE_RATIO = 0.25
DEGENERATE_MIN_WORDS = 12          # below this, repetition is just a short answer

MIN_WORDS = 400                    # before any insight is offered at all
MIN_DICTATIONS = 5

# A word has to actually recur to be a habit. One dictation about kubernetes
# should not make "kubernetes" a personality trait, so a word must appear
# across at least this share of dictations to be called a habit.
HABIT_MIN_SPREAD = 0.12
HABIT_MIN_COUNT = 8

# A phrase is rarer than any of the words in it, so it cannot be held to the
# same spread. At the word threshold, "just go ahead" (52 times across 30 of
# 336 dictations — unmistakably a signature) was being dropped while the
# fragments "just go" and "go ahead" survived, which is exactly backwards.
PHRASE_MIN_SPREAD = 0.04
PHRASE_MIN_COUNT = 6

TYPING_WPM = 40.0                  # a good office typing pace, for time-saved

_WEEKDAYS = ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday')


def tokenize(text: str) -> list[str]:
    """Lowercase word tokens. Apostrophes are kept inside words."""
    return _WORD_RE.findall((text or '').lower())


def is_degenerate(text: str) -> bool:
    """True when a transcript is a decoder repetition loop rather than speech.

    Short texts are never degenerate — "yes yes yes" is a real thing to say,
    and at four words the ratio test has no power anyway.
    """
    words = tokenize(text)
    if len(words) < DEGENERATE_MIN_WORDS:
        return False
    return (len(set(words)) / len(words)) < DEGENERATE_UNIQUE_RATIO


def _local_dt(ts_utc: str) -> datetime | None:
    try:
        return datetime.fromisoformat(ts_utc.replace('Z', '+00:00')).astimezone()
    except Exception:
        return None


def _row_get(row: Any, key: str, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


# ── Report shapes ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class WordStat:
    word: str
    count: int
    per: int          # one in every `per` words spoken
    spread: float     # share of dictations it appears in, 0–1


@dataclass(frozen=True)
class Insight:
    """One thing the corpus knows about the speaker.

    `headline` and `body` carry light markup so the phrasing lives in one
    place instead of being reassembled in TypeScript: `*word*` is the emphasis
    the design sets in italic accent, `**text**` is bold. `viz` names the
    picture to draw and carries only what that picture needs.
    """

    id: str
    headline: str
    body: str
    viz: dict[str, Any]


@dataclass(frozen=True)
class VocabularyReport:
    total_words: int
    unique_words: int
    dictations: int
    excluded: int                       # degenerate rows left out of the counts
    enough_data: bool
    top_words: list[WordStat] = field(default_factory=list)
    phrases: list[WordStat] = field(default_factory=list)
    fillers: list[WordStat] = field(default_factory=list)
    hours: list[int] = field(default_factory=list)      # 24 buckets of words
    weekdays: list[int] = field(default_factory=list)   # 7 buckets, Monday first
    sentence_avg: float = 0.0
    longest_words: int = 0
    longest_seconds: float = 0.0
    speaking_seconds: float = 0.0
    typing_seconds: float = 0.0
    insights: list[Insight] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── Phrase extraction ───────────────────────────────────────────────────────

# A phrase that begins or ends on a bare function word is a fragment, not an
# expression: "to just" and "like the" are artefacts of counting pairs, while
# "just go ahead" and "figure it out" are things a person actually says.
_PHRASE_EDGE = STOPWORDS | {'like', 'very', 'much', 'more', 'too', 'also'}


def _phrases(per_dictation: Sequence[Sequence[str]], total: int) -> list[WordStat]:
    """Characteristic 2- and 3-word expressions, longest-wins.

    A trigram absorbs the bigrams inside it — reporting "just go ahead",
    "just go" and "go ahead" as three separate habits is the same habit
    counted three times.
    """
    bi: Counter[str] = Counter()
    tri: Counter[str] = Counter()
    spread: dict[str, set[int]] = {}

    def note(key: str, idx: int) -> None:
        spread.setdefault(key, set()).add(idx)

    for idx, words in enumerate(per_dictation):
        for a, b in zip(words, words[1:], strict=False):
            if a not in _PHRASE_EDGE and b not in _PHRASE_EDGE:
                k = f'{a} {b}'
                bi[k] += 1
                note(k, idx)
        for a, b, c in zip(words, words[1:], words[2:], strict=False):
            if a not in _PHRASE_EDGE and c not in _PHRASE_EDGE:
                k = f'{a} {b} {c}'
                tri[k] += 1
                note(k, idx)

    n = max(1, len(per_dictation))
    chosen: list[tuple[str, int]] = []
    # Trigrams first so they can claim their sub-phrases.
    for key, count in tri.most_common(80):
        if count >= PHRASE_MIN_COUNT and len(spread.get(key, ())) / n >= PHRASE_MIN_SPREAD:
            chosen.append((key, count))
    claimed = {part for key, _ in chosen for part in _subphrases(key)}
    for key, count in bi.most_common(120):
        if key in claimed:
            continue
        if count >= PHRASE_MIN_COUNT and len(spread.get(key, ())) / n >= PHRASE_MIN_SPREAD:
            chosen.append((key, count))

    chosen.sort(key=lambda kv: -kv[1])
    return [
        WordStat(word=k, count=c, per=max(1, round(total / c)), spread=len(spread[k]) / n)
        for k, c in chosen[:8]
    ]


def _subphrases(trigram: str) -> tuple[str, str]:
    a, b, c = trigram.split(' ')
    return f'{a} {b}', f'{b} {c}'


# ── The analysis ────────────────────────────────────────────────────────────


def analyse(rows: Iterable[Any]) -> VocabularyReport:
    """Turn transcript rows into everything the Home screen shows.

    Accepts dicts or objects with `text`, `ts_utc`, `audio_seconds`. Never
    raises on malformed rows — a row that cannot be read is skipped, because
    one bad transcript must not be able to blank the whole screen.
    """
    per_dictation: list[list[str]] = []
    hours = [0] * 24
    weekdays = [0] * 7
    sentence_lengths: list[int] = []
    speaking = 0.0
    longest_words = 0
    longest_seconds = 0.0
    excluded = 0

    for row in rows:
        text = _row_get(row, 'text') or ''
        if is_degenerate(text):
            excluded += 1
            continue
        words = tokenize(text)
        if not words:
            continue
        per_dictation.append(words)

        seconds = float(_row_get(row, 'audio_seconds', 0.0) or 0.0)
        speaking += seconds
        if len(words) > longest_words:
            longest_words, longest_seconds = len(words), seconds

        when = _local_dt(str(_row_get(row, 'ts_utc') or ''))
        if when is not None:
            hours[when.hour] += len(words)
            weekdays[when.weekday()] += 1

        for chunk in _SENTENCE_RE.split(text):
            n = len(chunk.split())
            if n > 2:
                sentence_lengths.append(n)

    flat = [w for words in per_dictation for w in words]
    total = len(flat)
    counts = Counter(flat)
    dictations = len(per_dictation)
    sentence_avg = (sum(sentence_lengths) / len(sentence_lengths)) if sentence_lengths else 0.0

    if total < MIN_WORDS or dictations < MIN_DICTATIONS:
        # Counts yes, conclusions no. Everything here is something we
        # measured; only the narrative needs a corpus to stand on.
        return VocabularyReport(
            total_words=total, unique_words=len(counts), dictations=dictations,
            excluded=excluded, enough_data=False, hours=hours, weekdays=weekdays,
            sentence_avg=round(sentence_avg, 1), speaking_seconds=round(speaking, 1),
            typing_seconds=round(total / TYPING_WPM * 60.0, 1),
            longest_words=longest_words, longest_seconds=round(longest_seconds, 1),
        )

    spread: Counter[str] = Counter()
    for words in per_dictation:
        spread.update(set(words))

    def stat(word: str, count: int) -> WordStat:
        return WordStat(
            word=word, count=count,
            per=max(1, round(total / count)),
            spread=round(spread[word] / dictations, 3),
        )

    top_words = [
        stat(w, c)
        for w, c in counts.most_common(400)
        if w not in STOPWORDS and w not in _DISCOURSE_ONLY
        and c >= HABIT_MIN_COUNT and spread[w] / dictations >= HABIT_MIN_SPREAD
    ][:10]

    filler_stats: list[WordStat] = []
    for phrase in FILLERS:
        if ' ' in phrase:
            count = _count_phrase(per_dictation, phrase)
            hit = sum(1 for words in per_dictation if _has_phrase(words, phrase))
        else:
            count = counts.get(phrase, 0)
            hit = spread[phrase]
        if count >= HABIT_MIN_COUNT:
            filler_stats.append(WordStat(
                word=phrase, count=count,
                per=max(1, round(total / count)),
                spread=round(hit / dictations, 3),
            ))
    filler_stats.sort(key=lambda s: -s.count)

    report = VocabularyReport(
        total_words=total,
        unique_words=len(counts),
        dictations=dictations,
        excluded=excluded,
        enough_data=True,
        top_words=top_words,
        phrases=_phrases(per_dictation, total),
        fillers=filler_stats[:6],
        hours=hours,
        weekdays=weekdays,
        sentence_avg=round(sentence_avg, 1),
        longest_words=longest_words,
        longest_seconds=round(longest_seconds, 1),
        speaking_seconds=round(speaking, 1),
        typing_seconds=round(total / TYPING_WPM * 60.0, 1),
    )
    return _with_insights(report)


def _has_phrase(words: Sequence[str], phrase: str) -> bool:
    parts = phrase.split(' ')
    n = len(parts)
    return any(list(words[i:i + n]) == parts for i in range(len(words) - n + 1))


def _count_phrase(per_dictation: Sequence[Sequence[str]], phrase: str) -> int:
    parts = phrase.split(' ')
    n = len(parts)
    total = 0
    for words in per_dictation:
        for i in range(len(words) - n + 1):
            if list(words[i:i + n]) == parts:
                total += 1
    return total


# ── Narrative ───────────────────────────────────────────────────────────────
# The sentences live here, not in the UI, so the thresholds that decide whether
# a claim is true and the words that make the claim cannot drift apart.


def _with_insights(r: VocabularyReport) -> VocabularyReport:
    out: list[Insight] = []

    if r.fillers:
        top = r.fillers[0]
        out.append(Insight(
            id='crutch',
            headline=f'You lean on the word *{top.word}*.',
            body=(
                f'**{top.count:,} times** — once every **{top.per} words**, and it turns up '
                f'in {round(top.spread * 100)}% of everything you dictate. '
                'Nobody puts it there on purpose.'
            ),
            viz={'kind': 'dots', 'every': min(40, top.per), 'rows': 4,
                 'caption': f'1 in every {top.per} words', 'label': top.word},
        ))

    if len(r.fillers) >= 3:
        rest = r.fillers[1:3]
        together = sum(s.count for s in rest)
        names = ' and '.join(f'*{s.word}*' for s in rest)
        out.append(Insight(
            id='crutch-runners-up',
            headline=f'{names.capitalize()} follow it around.',
            body=(
                f'Another **{together:,} words** between them — about '
                f'**{max(1, round(together / r.total_words * 100))}%** of everything you '
                'have ever said. These are the words that buy you a beat while you think.'
            ),
            viz={'kind': 'bars', 'rows': [[s.word, s.count] for s in r.fillers[:4]]},
        ))

    if r.sentence_avg >= 4:
        longer = r.sentence_avg >= 16
        out.append(Insight(
            id='sentence-length',
            headline=f'You think in *{"long" if longer else "short"}* sentences.',
            body=(
                f'**{r.sentence_avg} words** on average, where most written English sits '
                f'near 14. Your longest unbroken thought ran **{r.longest_words:,} words** — '
                f'{_duration_phrase(r.longest_seconds)} without stopping.'
            ),
            viz={'kind': 'bars', 'rows': [
                ['You', r.sentence_avg], ['Written English', 14], ['Spoken average', 11],
            ], 'unit': ' w'},
        ))

    if r.phrases:
        first = r.phrases[0]
        tail = f' Right behind it: **{r.phrases[1].word}**, {r.phrases[1].count} times.' \
            if len(r.phrases) > 1 else ''
        out.append(Insight(
            id='signature-phrase',
            headline=f'You keep saying *{first.word}*.',
            body=f'**{first.count} times**, word for word.{tail}',
            viz={'kind': 'bars', 'rows': [[p.word, p.count] for p in r.phrases[:4]]},
        ))

    if any(r.hours):
        peak = max(range(24), key=lambda h: r.hours[h])
        morning = sum(r.hours[5:12])
        out.append(Insight(
            id='peak-hour',
            headline=f'*{_hour_label(peak)}* is when you have the most to say.',
            body=(
                f'**{r.hours[peak]:,} words** live in that single hour'
                + (' — more than your entire morning put together.'
                   if r.hours[peak] > morning else '.')
            ),
            viz={'kind': 'bars', 'rows': [
                [_hour_label(h), r.hours[h]]
                for h in sorted(range(24), key=lambda h: -r.hours[h])[:4]
            ]},
        ))

    if r.unique_words >= 200:
        out.append(Insight(
            id='breadth',
            headline=f'You know *{r.unique_words:,}* different words.',
            body=(
                f'Out of {r.total_words:,} spoken. Roughly **one word in '
                f'{max(2, round(r.total_words / r.unique_words))}** is one you had not '
                'used yet — a wider range than most dictation ever sees.'
            ),
            viz={'kind': 'dots', 'every': max(2, min(20, round(r.total_words / r.unique_words))),
                 'rows': 6, 'caption': 'one you had not used yet',
                 'label': f'{r.unique_words:,} distinct'},
        ))

    if any(r.weekdays):
        best = max(range(7), key=lambda d: r.weekdays[d])
        worst = min(range(7), key=lambda d: r.weekdays[d])
        ratio = r.weekdays[best] / max(1, r.weekdays[worst])
        out.append(Insight(
            id='best-day',
            headline=f'*{_WEEKDAYS[best]}* is your loudest day.',
            body=(
                f'**{r.weekdays[best]} dictations** land on a {_WEEKDAYS[best]} — '
                f'{ratio:.0f}× a {_WEEKDAYS[worst]}.'
            ),
            viz={'kind': 'bars', 'rows': [
                [_WEEKDAYS[d][:3], r.weekdays[d]]
                for d in sorted(range(7), key=lambda d: -r.weekdays[d])[:4]
            ]},
        ))

    return replace(r, insights=out)


def _hour_label(hour: int) -> str:
    if hour == 0:
        return 'Midnight'
    if hour == 12:
        return 'Noon'
    suffix = 'AM' if hour < 12 else 'PM'
    return f'{hour % 12} {suffix}'


def _duration_phrase(seconds: float) -> str:
    minutes = seconds / 60.0
    if minutes < 1.5:
        return f'{round(seconds)} seconds'
    if minutes < 10:
        return f'{minutes:.1f} minutes'.replace('.0 ', ' ')
    return f'{round(minutes)} minutes'
