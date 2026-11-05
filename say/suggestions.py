"""Turn the user's corrections into vocabulary rules they can approve.

A correction says "you typed this, I meant that". The same correction made
several times is a rule waiting to be written: `cloud code` was typed for
`Claude Code` twenty-six times across fourteen dictations before anyone added
it to the dictionary by hand.

This module finds those repeats and describes each one well enough to decide
on. It never applies anything. A rule that is wrong does damage every time it
fires, silently, in text the user has stopped checking — so the last step is a
person saying yes, and what they see first is how often the rule would have
fired on everything they have already dictated.

Pure: correction rows and transcript texts in, candidates out. No store, no
settings, no AppKit.
"""
from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

import corrections

# How many example sentences travel with a candidate.
MAX_EXAMPLES = 3


@dataclass(frozen=True)
class PairStats:
    """Everything known about one `wrong -> right` candidate."""

    wrong: str  # as Say typed it, lower-cased, widened to two words when it can be
    right: str  # what the user changed it to, in their casing
    confirmations: int  # corrections in which the user made exactly this change
    days: int  # distinct days those corrections were made on
    conflicts: int  # corrections that changed `wrong` into something *else*
    corpus_matches: int  # times `wrong` occurs across every stored transcript
    kept_as_typed: int  # of those, in transcripts the user checked and left alone
    single_word: bool  # could not be widened: the rule would fire on a bare word
    examples: tuple[str, ...] = ()  # where it would fire, for the user to read


def should_suggest(stats: PairStats) -> bool:
    """Whether a candidate is worth putting in front of the user.

    TODO(human): decide the rule. Returning False keeps every candidate back,
    which is the safe placeholder: nothing is suggested until this is written.
    """
    return False


# ── Finding candidates ────────────────────────────────────────────────


def _widen(
    before: list[str], after: list[str], a: list[str], b: list[str], i: int, j: int
) -> tuple[list[str], list[str], bool]:
    """Grow a one-word pair by a neighbour both sides agree on.

    Alignment ignores case, so the pair that falls out of
    "cloud code" -> "Claude Code" is the bare ("cloud", "Claude"). A rule that
    rewrites every "cloud" is wrong most of the time. Taking the word that
    follows (or, failing that, precedes) on both sides gives a rule that only
    fires where the user actually made the correction.

    `i` / `j` are where `before` / `after` start in the token lists `a` / `b`.
    Returns the pair and whether it is still a single word.
    """
    if len(before) != 1 or len(after) != 1:
        return before, after, False
    nxt_a, nxt_b = i + 1, j + 1
    if nxt_a < len(a) and nxt_b < len(b) and \
            corrections.normalise(a[nxt_a]) == corrections.normalise(b[nxt_b]) != '':
        return before + [a[nxt_a]], after + [b[nxt_b]], False
    prv_a, prv_b = i - 1, j - 1
    if prv_a >= 0 and prv_b >= 0 and \
            corrections.normalise(a[prv_a]) == corrections.normalise(b[prv_b]) != '':
        return [a[prv_a]] + before, [b[prv_b]] + after, False
    return before, after, True


def _clean(tokens: list[str]) -> str:
    """Phrase text with outer punctuation dropped, inner spacing single."""
    return ' '.join(tokens).strip(corrections._EDGE_PUNCT + ' ')


def pairs_in(inserted: str, corrected: str) -> list[tuple[str, str, bool]]:
    """`(wrong, right, single_word)` for each small substitution in one correction."""
    a = corrections.tokenize(inserted)
    b = corrections.tokenize(corrected)
    d = corrections.diff(inserted, corrected)
    wanted = set(corrections.term_pairs(d))
    out: list[tuple[str, str, bool]] = []
    cursor = 0
    for e in d.edits:
        if e.kind != 'sub' or (e.before, e.after) not in wanted:
            continue
        before = corrections.tokenize(e.before)
        after = corrections.tokenize(e.after)
        # `Edit.index` is the offset into the inserted text; find where the
        # replacement sits in the corrected text by scanning forward from the
        # last one, so repeated words map to the right occurrence.
        j = _find(b, after, cursor)
        if j < 0:
            continue
        cursor = j + len(after)
        before, after, single = _widen(before, after, a, b, e.index, j)
        wrong, right = _clean(before).lower(), _clean(after)
        if wrong and right and wrong != right.lower():
            out.append((wrong, right, single))
    return out


def _find(haystack: list[str], needle: list[str], start: int) -> int:
    for k in range(start, len(haystack) - len(needle) + 1):
        if haystack[k:k + len(needle)] == needle:
            return k
    return -1


def _pattern(wrong: str) -> re.Pattern[str]:
    """Whole-phrase, case-insensitive: the same reach `apply_replacements` has."""
    body = r'[,\s]+'.join(re.escape(w) for w in wrong.split())
    return re.compile(r'(?<![\w])' + body + r'(?![\w])', re.IGNORECASE)


def _snippet(text: str, start: int, end: int, pad: int = 32) -> str:
    left = max(0, start - pad)
    right = min(len(text), end + pad)
    return ('…' if left else '') + text[left:right].strip() + ('…' if right < len(text) else '')


def collect(
    correction_rows: Iterable[Mapping[str, Any]],
    transcripts: Iterable[Mapping[str, Any]],
) -> list[PairStats]:
    """Every repeated-or-not substitution the user has made, with its evidence.

    `correction_rows` carry `inserted`, `corrected`, `ts_utc`, `transcript_id`.
    `transcripts` carry `id` and `text` — every stored transcript, because the
    blast radius of a rule is everything it would touch, not just the
    dictations that happened to be corrected.
    """
    rows = list(correction_rows)
    texts = [(int(t['id']), str(t.get('text') or '')) for t in transcripts]

    # A transcript the user checked and saved unchanged is evidence that what
    # Say typed there was right — including any phrase a rule would rewrite.
    checked = {
        int(r['transcript_id'])
        for r in rows
        if r.get('transcript_id') is not None
        and (r.get('corrected') or '').strip() == (r.get('inserted') or '').strip()
    }

    made: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    singles: dict[tuple[str, str], bool] = {}
    for r in rows:
        day = str(r.get('ts_utc') or '')[:10]
        for wrong, right, single in pairs_in(r.get('inserted') or '', r.get('corrected') or ''):
            made[wrong][right].append(day)
            singles[(wrong, right)] = singles.get((wrong, right), True) and single

    out: list[PairStats] = []
    for wrong, rights in made.items():
        pattern = _pattern(wrong)
        matches = 0
        kept = 0
        examples: list[str] = []
        for tid, text in texts:
            for m in pattern.finditer(text):
                matches += 1
                if tid in checked:
                    kept += 1
                if len(examples) < MAX_EXAMPLES:
                    examples.append(_snippet(text, m.start(), m.end()))
        total = sum(len(days) for days in rights.values())
        for right, days in rights.items():
            out.append(PairStats(
                wrong=wrong,
                right=right,
                confirmations=len(days),
                days=len({d for d in days if d}),
                conflicts=total - len(days),
                corpus_matches=matches,
                kept_as_typed=kept,
                single_word=singles[(wrong, right)],
                examples=tuple(examples),
            ))
    out.sort(key=lambda s: (-s.confirmations, -s.corpus_matches, s.wrong))
    return out


def suggest(
    correction_rows: Iterable[Mapping[str, Any]],
    transcripts: Iterable[Mapping[str, Any]],
    existing: Mapping[str, str] | None = None,
) -> list[PairStats]:
    """Candidates that pass `should_suggest` and are not already a rule."""
    have = {k.lower() for k in (existing or {})}
    return [
        s for s in collect(correction_rows, transcripts)
        if s.wrong not in have and should_suggest(s)
    ]
