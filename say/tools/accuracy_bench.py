#!/usr/bin/env python3
"""Which model hears *you* best? Word error rate on the clips you corrected.

Why this exists
---------------
Every accuracy claim before this was a proxy: punctuation rates, sentence
density, a handful of hard-coded jargon strings. None of them says how many
words came out wrong. The clips you fixed in the app (or confirmed were right)
are the first ground truth this product has had, and this scores each
installed model and language setting against them.

It decodes the way the app does — through `transcriber.Transcriber`, with the
same prompts, silence gate and hallucination filter, then the same
post-processing — so the number describes the product, not a lab setup.

Two guards, both learned the hard way in this repository:

  * **A held-out slice.** The newest third of the corrected clips is reported
    separately. If a setting is chosen because it looked best on the clips used
    to tune it, the held-out number is the one that says whether it is real.
  * **A selected sample says so.** Clips reach the review queue *because* two
    models disagreed on them, so these error rates run high next to everyday
    use. They are good for comparing settings and bad as a headline.

It never downloads a model and never writes outside the shared decode cache.

Usage
-----
    python say/tools/accuracy_bench.py                          # installed models, your language setting
    python say/tools/accuracy_bench.py --languages auto,en      # also try English pinned
    python say/tools/accuracy_bench.py --models mlx-community/whisper-large-v3-turbo
"""
from __future__ import annotations

import argparse
import os
import sys
from importlib import import_module
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import corrections  # noqa: E402
import model_catalog  # noqa: E402
import postprocess  # noqa: E402
from server.store import get_store  # noqa: E402

replay_bench = import_module('tools.replay_bench')

MIN_REFERENCES = 10
HOLDOUT_SHARE = 1 / 3


def split_holdout(clips: list[dict[str, Any]], share: float = HOLDOUT_SHARE):
    """`(tuning, holdout)`: the newest `share` of clips, by time, is held out."""
    ordered = sorted(clips, key=lambda c: (str(c.get('ts_utc', '')), c['id']))
    n_hold = max(1, round(len(ordered) * share)) if len(ordered) >= 3 else 0
    return ordered[: len(ordered) - n_hold], ordered[len(ordered) - n_hold:]


def word_error_rate(pairs: list[tuple[str, str]]) -> tuple[float, int, int]:
    """`(rate, errors, reference_words)` over `(reference, hypothesis)` pairs. Pure."""
    errors = words = 0
    for reference, hypothesis in pairs:
        e, w = corrections.word_errors(reference, hypothesis)
        errors += e
        words += w
    return (errors / words if words else 0.0), errors, words


def _row(name: str, clips, refs, texts, waits) -> dict[str, Any]:
    tuning, holdout = split_holdout(clips)
    all_rate, errors, words = word_error_rate([(refs[c['id']], texts[c['id']]) for c in clips])
    hold_rate, _, _ = word_error_rate([(refs[c['id']], texts[c['id']]) for c in holdout])
    ordered = sorted(waits)
    p90 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.9))] if ordered else None
    return {'name': name, 'clips': len(clips), 'words': words, 'errors': errors,
            'wer': all_rate, 'holdout_wer': hold_rate, 'holdout': len(holdout),
            'decode_p90': p90}


def render(rows: list[dict[str, Any]]) -> str:
    out = [f'  {"setting":<44}{"clips":>6}{"WER":>8}{"held-out":>10}{"decode p90":>12}',
           '  ' + '-' * 80,
           '  (decode p90: whole-clip decode of 20 s+ clips, for comparing models;'
           ' the app streams, so its wait is shorter)']
    for r in rows:
        p90 = f'{r["decode_p90"]:.1f}s' if r['decode_p90'] is not None else '—'
        out.append(f'  {r["name"][:43]:<44}{r["clips"]:>6}{r["wer"]:>8.1%}'
                   f'{r["holdout_wer"]:>10.1%}{p90:>12}')
    return '\n'.join(out)


def recommend(rows: list[dict[str, Any]], tolerance: float = 0.01) -> str:
    """Best held-out setting, and the fastest one within `tolerance` of it."""
    candidates = [r for r in rows if r['name'] != 'what Say typed at the time']
    if not candidates:
        return ''
    best = min(candidates, key=lambda r: (r['holdout_wer'], r['wer']))
    lines = [f'  Most accurate on the held-out clips: {best["name"]} '
             f'({best["holdout_wer"]:.1%}).']
    timed = [r for r in candidates if r['decode_p90'] is not None
             and r['holdout_wer'] <= best['holdout_wer'] + tolerance]
    fastest = min(timed, key=lambda r: r['decode_p90'], default=None)
    if fastest is not None and fastest is not best and best['decode_p90'] is not None \
            and fastest['decode_p90'] < best['decode_p90']:
        lines.append(f'  Within {tolerance:.0%} of it and faster: {fastest["name"]} '
                     f'(p90 decode {fastest["decode_p90"]:.1f}s vs {best["decode_p90"]:.1f}s).')
    return '\n'.join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--models', default='', help='comma-separated (default: every installed model)')
    ap.add_argument('--languages', default='current',
                    help='comma-separated: current, auto, en, hi, … (default: your setting)')
    args = ap.parse_args()

    store = get_store()
    refs = store.manual_corrections()
    corpus = [c for c in replay_bench.load_corpus(min_words=0) if c['id'] in refs]
    if len(corpus) < MIN_REFERENCES:
        print(f'{len(corpus)} corrected clips with audio; need {MIN_REFERENCES}. Switch on')
        print('"To review" in Say, play each clip, and fix or confirm it.')
        return 1
    with store._lock:
        stamps = dict(store._conn.execute('SELECT id, ts_utc FROM transcripts').fetchall())
    for c in corpus:
        c['ts_utc'] = stamps.get(c['id'], '')

    if args.models:
        models = [m.strip() for m in args.models.split(',') if m.strip()]
    else:
        models = [m.id for m in model_catalog.CATALOG if model_catalog.is_downloaded(m.id)]
    missing = [m for m in models if not model_catalog.is_downloaded(m)]
    if missing:
        print(f'Not downloaded (get them from the Models page): {", ".join(missing)}')
        return 1

    languages: list[Any] = []
    for token in (t.strip() for t in args.languages.split(',') if t.strip()):
        languages.append(replay_bench._UNSET if token == 'current'
                         else None if token == 'auto' else token)

    print(f'{len(corpus)} corrected clips; the newest {len(split_holdout(corpus)[1])} '
          'are held out. These were picked for review because they looked doubtful,')
    print('so expect higher error rates than everyday use.\n')

    rows = [_row('what Say typed at the time', corpus, refs,
                 {c['id']: c['stored_text'] for c in corpus}, [])]
    cache = replay_bench._load_cache()
    for model in models:
        for language in languages:
            label = 'your setting' if language is replay_bench._UNSET else (language or 'auto')
            print(f'  decoding with {model.split("/")[-1]}, language {label}')
            replay_bench.decode_with_engine(corpus, model, cache, language=language)
            texts, waits = {}, []
            for c in corpus:
                hit = cache[replay_bench.engine_key(c['id'], model, language)]
                texts[c['id']] = postprocess.apply(hit['text'])
                if c['audio_seconds'] >= 20:
                    waits.append(hit['infer_s'])
            rows.append(_row(f'{model.split("/")[-1]} · {label}', corpus, refs, texts, waits))

    print()
    print(render(rows))
    print()
    print(recommend(rows))
    print(f'\n  Current default: {config.MODEL}, language {config.LANGUAGE or "auto"}.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
