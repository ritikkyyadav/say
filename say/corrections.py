"""What the user changed after Say typed for them.

This is the measurement layer under the whole "maturity" pipeline. Everything
downstream of transcription — vocabulary repair, formatting, per-app profiles,
any future semantic tier — is a bet about what the user actually wanted. The
only honest scorecard for those bets is how often they still had to reach in
and fix the text themselves.

Nothing here touches AppKit, the store, or the network. It is pure text math:
a word-level diff, a set of guards that decide whether a diff is even *a
correction*, and the aggregation that turns diffs into a rate. `readback.py`
supplies the observations; `server/store.py` persists them.

Two distinctions do all the work, and both exist because the naive version of
this metric lies:

  1. **Continuation is not correction.** After Say inserts a sentence the user
     usually keeps typing. Those trailing words are new content, not a repair,
     and counting them would make a perfect insertion look like a broken one.
     Only edits *inside* the span Say produced are corrections.
  2. **A rewrite is not a correction.** If most of the span is gone the user
     changed their mind, or the anchors relocated onto the wrong region. Past
     `MAX_DRIFT` we record nothing rather than learn from noise.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

# Beyond this share of the span changing, the edit is a rewrite (or a bad
# anchor match) rather than a correction. Recording it would poison both the
# metric and anything that learns from the pairs.
MAX_DRIFT = 0.5

# Substitutions longer than this on either side are sentence surgery, not a
# term the acoustic model misheard. They still count toward the rate; they are
# just not offered as learnable vocabulary.
MAX_LEARNABLE_TOKENS = 4

_TOKEN_RE = re.compile(r'\S+')
# Stripped only for *comparison* — the stored pair keeps the user's real text.
_EDGE_PUNCT = '.,!?;:"\'`()[]{}…—–-'


def tokenize(text: str) -> list[str]:
    """Split on whitespace. Punctuation stays attached to its word."""
    return _TOKEN_RE.findall(text or '')


def normalise(token: str) -> str:
    """Comparison form: case- and edge-punctuation-insensitive.

    A trailing comma appearing is a formatting change, not the user telling us
    we misheard the word. Alignment runs on this form so those do not fragment
    the diff; the edits themselves report the original tokens.
    """
    return token.strip(_EDGE_PUNCT).lower()


@dataclass(frozen=True)
class Edit:
    """One aligned change between what Say typed and what it became."""

    kind: str  # 'sub' | 'ins' | 'del'
    before: str  # '' for an insertion
    after: str  # '' for a deletion
    index: int  # token offset into the *inserted* text

    @property
    def cost(self) -> int:
        return max(len(tokenize(self.before)), len(tokenize(self.after)))


@dataclass(frozen=True)
class Diff:
    """The comparison between an insertion and what the user left behind."""

    inserted: str
    corrected: str
    edits: tuple[Edit, ...]
    words: int  # token count of `inserted` — the denominator

    @property
    def distance(self) -> int:
        return sum(e.cost for e in self.edits)

    @property
    def rate(self) -> float:
        """Share of the inserted span the user had to touch. 0.0 is the goal."""
        if self.words <= 0:
            return 0.0
        return min(1.0, self.distance / self.words)

    @property
    def clean(self) -> bool:
        return not self.edits


def _split_boundary_growth(
    opcodes: list[tuple[str, int, int, int, int]],
) -> list[tuple[str, int, int, int, int]]:
    """Peel continuation out of a `replace` that touches either end of the span.

    The end of Say's span is exactly where the user's cursor is left, so their
    next keystrokes land flush against our last word and the aligner fuses both
    into one `replace` block. "cafe." -> "cafe. today and it was good." then
    scores as a total rewrite of the final token when in truth we got it right
    and the user simply kept writing.

    A trailing `replace` that grew is therefore decomposed: the part that lines
    up with our tokens stays a replacement, the overhang becomes an `insert`
    that `diff()` drops.

    Deliberately trailing-only. The cursor is left at the *end* of the span, so
    continuation there is the common case; continuation before it requires the
    user to click back first and is rare. Splitting the leading block too costs
    more than it buys — it mangles the ordinary expansion of a first word
    ("lets" -> "let us") into the nonsense pair ("lets", "us"). Interior blocks
    are likewise left alone: growth in the middle is the user genuinely
    expanding something we wrote.
    """
    if not opcodes:
        return opcodes
    out = list(opcodes)

    tag, i1, i2, j1, j2 = out[-1]
    if tag == 'replace' and (j2 - j1) > (i2 - i1):
        keep = i2 - i1
        out[-1:] = [
            ('replace', i1, i2, j1, j1 + keep),
            ('insert', i2, i2, j1 + keep, j2),
        ]

    return out


def diff(inserted: str, corrected: str) -> Diff:
    """Word-level diff, with leading/trailing continuation excluded.

    Text the user added before or after Say's span is new writing, not a
    repair. Dropping it is the difference between a metric that measures our
    output and one that measures how talkative the user is afterwards.
    """
    a = tokenize(inserted)
    b = tokenize(corrected)
    a_norm = [normalise(t) for t in a]
    b_norm = [normalise(t) for t in b]

    # autojunk would classify frequent tokens ("the", "a") as noise on any
    # sequence over 200 elements and align around them — catastrophic for
    # prose, where those words carry the alignment.
    matcher = SequenceMatcher(None, a_norm, b_norm, autojunk=False)
    opcodes = _split_boundary_growth(list(matcher.get_opcodes()))

    edits: list[Edit] = []
    for pos, (tag, i1, i2, j1, j2) in enumerate(opcodes):
        if tag == 'equal':
            continue
        if tag == 'insert':
            # Pure insertion at either end of our span = continuation, not a fix.
            if pos == 0 or pos == len(opcodes) - 1:
                continue
            edits.append(Edit('ins', '', ' '.join(b[j1:j2]), i1))
        elif tag == 'delete':
            edits.append(Edit('del', ' '.join(a[i1:i2]), '', i1))
        else:  # 'replace'
            edits.append(Edit('sub', ' '.join(a[i1:i2]), ' '.join(b[j1:j2]), i1))

    return Diff(inserted=inserted, corrected=corrected, edits=tuple(edits), words=len(a))


def looks_like_correction(d: Diff) -> bool:
    """Whether a diff is safe to record and learn from.

    Conservative on purpose. A missed correction costs us one data point; a
    false one teaches the system that the user wanted something they never
    asked for, and that error compounds every time it is applied.
    """
    if not d.inserted.strip() or not d.corrected.strip():
        return False
    if d.clean:
        return False
    if d.words <= 0:
        return False
    return d.rate <= MAX_DRIFT


def term_pairs(d: Diff) -> list[tuple[str, str]]:
    """Substitutions small enough to be a misheard term rather than a rewrite.

    These are the raw material for learned vocabulary. Deliberately derived on
    demand instead of stored: the extraction rules will get better, and rows
    frozen under today's rules could never benefit from that.
    """
    pairs: list[tuple[str, str]] = []
    for e in d.edits:
        if e.kind != 'sub':
            continue
        before_tokens = tokenize(e.before)
        after_tokens = tokenize(e.after)
        if not before_tokens or not after_tokens:
            continue
        if len(before_tokens) > MAX_LEARNABLE_TOKENS or len(after_tokens) > MAX_LEARNABLE_TOKENS:
            continue
        # A pure case or punctuation change is the formatter's job, not vocabulary.
        if [normalise(t) for t in before_tokens] == [normalise(t) for t in after_tokens]:
            continue
        pairs.append((e.before, e.after))
    return pairs


@dataclass(frozen=True)
class RateSummary:
    """Aggregate correction behaviour over a window of insertions."""

    insertions: int  # dictations inserted in the window
    corrected: int  # how many of them the user then edited
    words: int  # total words inserted
    edited_words: int  # total words changed

    @property
    def touch_rate(self) -> float:
        """Share of insertions the user had to touch at all."""
        if self.insertions <= 0:
            return 0.0
        return self.corrected / self.insertions

    @property
    def word_rate(self) -> float:
        """Share of inserted words the user had to change."""
        if self.words <= 0:
            return 0.0
        return self.edited_words / self.words


def summarise(rows: Iterable[Mapping[str, Any]], *, insertions: int) -> RateSummary:
    """Fold correction rows into a rate.

    Accepts any mapping carrying `words` and `edit_distance`; callers holding
    `sqlite3.Row`s should `dict()` them first. `insertions` must be the number of dictations *inserted*
    in the window, not the number of corrections — the insertions nobody had to
    touch are exactly what the metric exists to reward, and they leave no row
    here to be counted.
    """
    corrected = 0
    words = 0
    edited = 0
    for r in rows:
        try:
            w = int(r['words'])
            d = int(r['edit_distance'])
        except Exception:
            continue
        if d <= 0:
            continue
        corrected += 1
        words += max(0, w)
        edited += max(0, d)
    return RateSummary(
        insertions=max(int(insertions), corrected),
        corrected=corrected,
        words=words,
        edited_words=edited,
    )
