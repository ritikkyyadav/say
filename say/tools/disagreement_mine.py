#!/usr/bin/env python3
"""Find the dictations most likely to be wrong, without listening to all of them.

Why this exists
---------------
The only way to know a transcript is wrong is to hear the clip. Several hours
of saved audio is too much to hear, and picking clips at random spends most of
the effort confirming ones that were fine.

A second model given the same audio is a cheap second opinion. Where it writes
the same words, both are probably right. Where it writes different ones, at
least one is wrong, and one listen settles which. This re-decodes saved clips
with another model, ranks them by how much the two versions differ, and writes
that ranking as the review queue the app shows.

Disagreement is where to look, not what is wrong. Nothing here is counted as
an error until you have checked it in the app.

It reads the recordings and the transcript text. It writes two cache files next
to the database (`bench_cache.json`, shared with `replay_bench`, and
`review_queue.json`) and nothing else. Audio never leaves the machine.

Usage
-----
    python say/tools/disagreement_mine.py --sample 40      # a quick first queue
    python say/tools/disagreement_mine.py                  # every saved clip
    python say/tools/disagreement_mine.py --show 15        # print the top of the queue
    python say/tools/disagreement_mine.py --dry-run        # rank, but write no queue
"""
from __future__ import annotations

import argparse
import os
import sys
from importlib import import_module
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import corrections  # noqa: E402
import model_catalog  # noqa: E402
import postprocess  # noqa: E402
import review_queue  # noqa: E402
from server.store import get_store  # noqa: E402

# Imported by name so the type checker does not see one file as two modules.
replay_bench = import_module('tools.replay_bench')

# The second opinion. Larger and slower than the model most people dictate
# with, which is the point: it is run once, offline, not while anyone waits.
DEFAULT_MODEL = 'mlx-community/whisper-large-v3-mlx'
# Used when the clip was already transcribed by DEFAULT_MODEL.
FALLBACK_MODEL = 'mlx-community/whisper-large-v3-turbo'
CONFIG = 'seed'


def _second_model(stored_model: str, preferred: str) -> str:
    return FALLBACK_MODEL if stored_model == preferred else preferred


def _snippets(stored: str, other: str, limit: int = 3) -> list[tuple[str, str]]:
    d = corrections.diff(stored, other)
    return [(e.before or '∅', e.after or '∅') for e in d.edits[:limit]]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--model', default=DEFAULT_MODEL, help='the second-opinion model')
    ap.add_argument('--sample', type=int, default=0,
                    help='a duration-stratified sample of N clips instead of all of them')
    ap.add_argument('--min-words', type=int, default=review_queue.MIN_WORDS)
    ap.add_argument('--limit', type=int, default=review_queue.MAX_ENTRIES,
                    help='longest queue to write')
    ap.add_argument('--show', type=int, default=10, help='how many to print')
    ap.add_argument('--dry-run', action='store_true', help='rank but do not write the queue')
    args = ap.parse_args()

    for model in {args.model, FALLBACK_MODEL}:
        info = model_catalog.get(model)
        if info is None or info.backend != 'whisper-mlx':
            print(f'{model!r} is not a Whisper model this tool can run.')
            return 1

    store = get_store()
    corpus = replay_bench.load_corpus(min_words=args.min_words)
    done = store.manual_corrections()
    corpus = [c for c in corpus if c['id'] not in done]
    if not corpus:
        print('No saved recordings left to check. Turn on "Keep audio" and dictate first.')
        return 1
    if args.sample:
        corpus = replay_bench.stratified_sample(corpus, args.sample)

    minutes = sum(c['audio_seconds'] for c in corpus) / 60
    print(f'{len(corpus)} clips, {minutes:.0f} min of audio. Decoding is cached, so an')
    print('interrupted run picks up where it stopped.')

    cache = replay_bench._load_cache()
    by_model: dict[str, list[dict[str, Any]]] = {}
    for clip in corpus:
        by_model.setdefault(_second_model(clip['model'], args.model), []).append(clip)
    for model, clips in by_model.items():
        if not model_catalog.is_downloaded(model):
            print(f'{model} is not downloaded. Get it from the Models page, then run this again.')
            return 1
        print(f'  second opinion from {model.split("/")[-1]} on {len(clips)} clips')
        replay_bench.run_config(CONFIG, clips, model, cache)

    pairs = []
    for clip in corpus:
        model = _second_model(clip['model'], args.model)
        hit = cache.get(f'{clip["id"]}|{CONFIG}|{model}')
        if not hit:
            continue
        # Both sides through the same clean-up, so a formatting or vocabulary
        # rule that already exists is not reported as a disagreement.
        pairs.append({
            'id': clip['id'],
            'stored': postprocess.apply(clip['stored_text']),
            'other': postprocess.apply(hit['text']),
            'seconds': clip['audio_seconds'],
        })

    ranked = review_queue.rank(pairs, min_words=args.min_words, limit=args.limit)
    agreed = len(pairs) - sum(
        1 for p in pairs if review_queue.disagreement(p['stored'], p['other'])[0] > 0
    )
    print()
    print(f'  {len(pairs)} clips compared. The two models agreed word for word on {agreed}.')
    print(f'  {len(ranked)} are queued for review, most different first.')
    print()

    lookup = {p['id']: p for p in pairs}
    for entry in ranked[: max(0, args.show)]:
        p = lookup[entry['id']]
        print(f'  #{entry["rank"]:<3} id {entry["id"]:<5} {p["seconds"]:>5.0f}s  '
              f'{entry["differing_words"]:>3} of {entry["words"]} words differ')
        for before, after in _snippets(p['stored'], p['other']):
            print(f'        stored: {before[:60]!r}   second opinion: {after[:60]!r}')

    if args.dry_run:
        print('\n  Dry run: no queue written.')
        return 0
    path = review_queue.save(store, ranked, model=args.model)
    print(f'\n  Queue written to {path}')
    print('  Open Say, switch on "To review", listen, and fix or confirm each one.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
