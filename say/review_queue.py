"""Which transcripts are worth the user's time to check.

Nobody is going to listen to several hours of their own dictation to find the
mistakes in it. The clips worth checking are the ones where a second model,
given the same audio, wrote something different: either one of them is wrong,
and it is cheap to find out which by listening once.

`tools/disagreement_mine.py` does the decoding and writes the queue; the API
attaches it to the transcript feed; the user's answer comes back through
`store.set_manual_correction()`.

Two things this is careful about:

  * **Disagreement is not error.** It says where to look. Nothing here is
    counted as a mistake or fed to vocabulary until a person has checked it.
  * **The file is a cache, not a record.** It holds ids and counts — no
    transcript text, no audio — and deleting it loses nothing that cannot be
    recomputed.

Pure apart from the two functions that touch the file. No AppKit.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import corrections

# Below this many words a clip is not worth a place in the queue: one differing
# word in a three-word clip is a 33% disagreement and tells us nothing.
MIN_WORDS = 8

# Longest queue written. The point is a list someone will actually finish.
MAX_ENTRIES = 60


def queue_path(store: Any) -> Path:
    """Beside the database, so a test store redirects it automatically."""
    return Path(store.path).parent / 'review_queue.json'


def disagreement(stored: str, other: str) -> tuple[int, int]:
    """`(words that differ, words in the stored text)` between two decodes."""
    errors, _ = corrections.word_errors(other, stored)
    return errors, len(corrections.tokenize(stored))


def rank(
    clips: list[dict[str, Any]], *, min_words: int = MIN_WORDS, limit: int = MAX_ENTRIES
) -> list[dict[str, Any]]:
    """Order clips by how much two decodes of the same audio disagree.

    Each clip carries `id`, `stored` and `other` text. Sorted by the number of
    differing words, then by their share: a long dictation with ten wrong words
    is more worth fixing than a short one with two, and among equals the one
    that is mostly wrong comes first. Clips the two decodes agree on are left
    out — there is nothing to check.
    """
    out: list[dict[str, Any]] = []
    for clip in clips:
        errors, words = disagreement(clip.get('stored', ''), clip.get('other', ''))
        if words < min_words or errors <= 0:
            continue
        out.append({
            'id': int(clip['id']),
            'differing_words': errors,
            'words': words,
            'share': round(min(1.0, errors / words), 4),
        })
    out.sort(key=lambda e: (-e['differing_words'], -e['share'], e['id']))
    out = out[: max(0, int(limit))]
    for position, entry in enumerate(out, 1):
        entry['rank'] = position
    return out


def save(store: Any, entries: list[dict[str, Any]], *, model: str = '') -> Path:
    """Write the queue atomically. Returns its path."""
    path = queue_path(store)
    payload = {'version': 1, 'model': model, 'entries': entries}
    tmp = path.with_suffix('.json.tmp')
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(payload, f)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return path


def load(store: Any) -> dict[int, dict[str, Any]]:
    """`transcript_id -> entry`. Empty when there is no queue. Never raises."""
    try:
        with open(queue_path(store), encoding='utf-8') as f:
            payload = json.load(f)
        return {int(e['id']): e for e in payload.get('entries', [])}
    except Exception:
        return {}
