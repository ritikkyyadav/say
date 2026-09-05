#!/usr/bin/env python3
"""Replay saved dictations through the transcriber under different settings.

Why this exists
---------------
Transcription quality claims are unfalsifiable without a corpus. Every user who
has ever enabled `SAVE_RECORDINGS` is sitting on one: a set of real clips in
their own voice, already paired with the text the engine produced. This tool
replays those clips under alternative decoder settings and scores the output, so
a change like "seed the style prompt" can be *measured* instead of eyeballed.

It is a developer tool. It never mutates the store, the recordings, or settings.

Usage
-----
    # what does the corpus look like?
    python say/tools/replay_bench.py --list

    # sweep the built-in configs over a stratified sample
    python say/tools/replay_bench.py --sample 24

    # validate the winner over everything
    python say/tools/replay_bench.py --configs baseline,seed_nocond

    # score the text already in the database, no inference at all
    python say/tools/replay_bench.py --stored-only

Results are cached in `~/Library/Application Support/Say/bench_cache.json`
keyed by (clip id, config name, model), so re-runs are cheap and interrupted
sweeps resume.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import wave
from dataclasses import dataclass, field
from typing import Any

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import model_catalog  # noqa: E402
import settings  # noqa: E402
import streaming  # noqa: E402
from server.store import get_store  # noqa: E402

# Honour the user's persisted overrides — otherwise the bench would sweep
# against the shipped default rather than the model they actually dictate with.
settings.apply_to_config()

# ── Decoder configurations under test ────────────────────────────────
#
# The hypothesis (see the gap audit): Whisper transcribes in 30-second
# windows and, with `condition_on_previous_text=True`, feeds each window's
# output into the next as a prompt. That keeps terminology consistent but
# also propagates *style* — so a single unpunctuated window poisons the rest
# of a long dictation. With `initial_prompt` empty, nothing establishes the
# style for the first window either.
#
# STYLE_SEED is deliberately bland: properly capitalized, properly punctuated,
# and semantically empty so it biases register without biasing vocabulary.

STYLE_SEED = (
    "Hello. Here is a clear, well-punctuated transcript. "
    "It uses capital letters, commas, and full stops correctly."
)

CONFIGS: dict[str, dict[str, Any]] = {
    # What ships today.
    'baseline': {
        'initial_prompt': None,
        'condition_on_previous_text': True,
    },
    # (a) alone — establish the style for window 1 and let it propagate.
    'seed': {
        'initial_prompt': STYLE_SEED,
        'condition_on_previous_text': True,
    },
    # (b) alone — stop propagation, but nothing sets the initial style.
    'nocond': {
        'initial_prompt': None,
        'condition_on_previous_text': False,
    },
    # (a) + (b) — every window starts from the same clean style seed.
    'seed_nocond': {
        'initial_prompt': STYLE_SEED,
        'condition_on_previous_text': False,
    },
    # Phase 3: cut the clip at silence and decode each piece separately, so at
    # key-release only the tail is left. Each segment carries the tail of the
    # previous segment's text into its prompt, rebuilding by hand the
    # cross-window conditioning that splitting would otherwise destroy.
    'chunked': {
        'initial_prompt': STYLE_SEED,
        'condition_on_previous_text': True,
        'chunked': True,
    },
    # Control: the same split WITHOUT carry-over, to show what the carry-over
    # is actually buying.
    'chunked_nocarry': {
        'initial_prompt': STYLE_SEED,
        'condition_on_previous_text': True,
        'chunked': True,
        'carry_over': False,
    },
}

# Known mis-transcriptions to count. Extend as the dictionary grows.
DICT_ERRORS = [
    (r'\bcloud code\b', 'Claude Code'),
    (r'\bclod code\b', 'Claude Code'),
    (r'\bcode base\b', 'codebase'),
]

_CACHE_PATH = os.path.expanduser(
    '~/Library/Application Support/Say/bench_cache.json'
)


# ── Scoring ──────────────────────────────────────────────────────────

@dataclass
class Score:
    n: int = 0
    no_terminal_punct: int = 0
    starts_capital: int = 0
    words: int = 0
    sentences: int = 0
    dict_errors: int = 0
    infer_seconds: float = 0.0
    audio_seconds: float = 0.0
    empty: int = 0
    per_clip: list[dict[str, Any]] = field(default_factory=list)

    def add(self, clip_id: int, text: str, audio_s: float, infer_s: float) -> None:
        self.n += 1
        self.infer_seconds += infer_s
        self.audio_seconds += audio_s
        stripped = text.strip()
        if not stripped:
            self.empty += 1
            self.per_clip.append({'id': clip_id, 'empty': True})
            return
        w = len(stripped.split())
        sents = len(re.findall(r'[.!?]+', stripped))
        has_punct = bool(re.search(r'[.!?]', stripped))
        cap = stripped[:1].isupper()
        errs = sum(len(re.findall(p, stripped, re.I)) for p, _ in DICT_ERRORS)
        self.words += w
        self.sentences += sents
        if not has_punct:
            self.no_terminal_punct += 1
        if cap:
            self.starts_capital += 1
        self.dict_errors += errs
        self.per_clip.append({
            'id': clip_id, 'words': w, 'sentences': sents,
            'punct': has_punct, 'cap': cap, 'errors': errs,
            'infer_s': round(infer_s, 3),
        })

    def summary(self) -> dict[str, Any]:
        if self.n == 0:
            return {}
        return {
            'clips': self.n,
            'pct_unpunctuated': 100.0 * self.no_terminal_punct / self.n,
            'pct_starts_capital': 100.0 * self.starts_capital / self.n,
            'sentences_per_100w': (100.0 * self.sentences / self.words) if self.words else 0.0,
            'dict_errors': self.dict_errors,
            'empty': self.empty,
            'words': self.words,
            'infer_seconds': self.infer_seconds,
            'rtf': (self.infer_seconds / self.audio_seconds) if self.audio_seconds else 0.0,
        }


# ── Corpus ───────────────────────────────────────────────────────────

def load_corpus(min_words: int = 0) -> list[dict[str, Any]]:
    """Return saved clips that still have readable audio on disk."""
    store = get_store()
    rows = store.recordings(limit=10_000)
    out = []
    for r in rows:
        path = store.audio_path_for(r['id'])
        if not path or not os.path.exists(path):
            continue
        if r.get('word_count', 0) < min_words:
            continue
        out.append({
            'id': r['id'],
            'path': path,
            'stored_text': r.get('text', ''),
            'audio_seconds': r.get('audio_seconds', 0.0),
            'word_count': r.get('word_count', 0),
            'model': r.get('model', ''),
        })
    return out


def stratified_sample(corpus: list[dict[str, Any]], k: int) -> list[dict[str, Any]]:
    """Pick k clips spread evenly across the duration range.

    Style drift is length-dependent, so a random sample would under-represent
    exactly the long clips the hypothesis is about. Sorting by duration and
    taking evenly-spaced indices keeps the short/medium/long mix honest.
    """
    if k >= len(corpus):
        return list(corpus)
    ordered = sorted(corpus, key=lambda c: c['audio_seconds'])
    step = len(ordered) / k
    return [ordered[min(int(i * step), len(ordered) - 1)] for i in range(k)]


def read_wav(path: str) -> np.ndarray:
    """Read a 16 kHz mono WAV into float32 in [-1, 1]."""
    with wave.open(path, 'rb') as w:
        frames = w.readframes(w.getnframes())
        width = w.getsampwidth()
        channels = w.getnchannels()
    if width == 2:
        audio = np.frombuffer(frames, dtype='<i2').astype('float32') / 32768.0
    elif width == 4:
        audio = np.frombuffer(frames, dtype='<f4').astype('float32')
    else:
        raise ValueError(f'unsupported sample width {width} in {path}')
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    return audio


# ── Cache ────────────────────────────────────────────────────────────

def _load_cache() -> dict[str, Any]:
    try:
        with open(_CACHE_PATH, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cache(cache: dict[str, Any]) -> None:
    try:
        os.makedirs(os.path.dirname(_CACHE_PATH), exist_ok=True)
        tmp = _CACHE_PATH + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(cache, f)
        os.replace(tmp, _CACHE_PATH)
    except Exception as exc:
        print(f'  [warn] could not write cache: {exc}', file=sys.stderr)


# ── Runner ───────────────────────────────────────────────────────────

def run_config(
    name: str,
    corpus: list[dict[str, Any]],
    model: str,
    cache: dict[str, Any],
    refresh: bool = False,
) -> Score:
    import mlx_whisper

    opts = CONFIGS[name]
    score = Score()
    for i, clip in enumerate(corpus, 1):
        key = f'{clip["id"]}|{name}|{model}'
        hit = None if refresh else cache.get(key)
        if hit is not None:
            text, infer_s = hit['text'], hit['infer_s']
        else:
            audio = read_wav(clip['path'])
            kwargs: dict[str, Any] = {
                'path_or_hf_repo': model,
                'fp16': True,
                'condition_on_previous_text': opts['condition_on_previous_text'],
            }
            if config.LANGUAGE:
                kwargs['language'] = config.LANGUAGE

            if opts.get('chunked'):
                text, infer_s = _transcribe_chunked(
                    mlx_whisper, audio, kwargs,
                    base_prompt=opts['initial_prompt'] or '',
                    carry_over=opts.get('carry_over', True),
                )
            else:
                if opts['initial_prompt']:
                    kwargs['initial_prompt'] = opts['initial_prompt']
                t0 = time.time()
                result = mlx_whisper.transcribe(audio, **kwargs)
                infer_s = time.time() - t0
                text = (result.get('text') or '').strip()
            cache[key] = {'text': text, 'infer_s': infer_s}
            if i % 5 == 0:
                _save_cache(cache)
        score.add(clip['id'], text, clip['audio_seconds'], infer_s)
        print(f'\r  {name}: {i}/{len(corpus)}', end='', flush=True)
    print()
    _save_cache(cache)
    return score


def _transcribe_chunked(
    mlx_whisper, audio, kwargs: dict[str, Any], *, base_prompt: str, carry_over: bool
) -> tuple[str, float]:
    """Split at silence, decode each piece, and report the *perceived* wait.

    `infer_s` here is deliberately not the total compute. At runtime every
    segment except the last is transcribed while the user is still speaking, so
    what they actually wait for after releasing the key is the final segment
    alone. That is the number worth optimising, so that is what we score.
    """
    segments = streaming.split_on_silence(audio)
    if not segments:
        return '', 0.0

    parts: list[str] = []
    tail_seconds = 0.0
    for i, seg in enumerate(segments):
        seg_kwargs = dict(kwargs)
        prompt = base_prompt
        if carry_over and parts:
            prompt = streaming.carry_over_prompt(base_prompt, ' '.join(parts))
        if prompt:
            seg_kwargs['initial_prompt'] = prompt
        t0 = time.time()
        result = mlx_whisper.transcribe(seg, **seg_kwargs)
        elapsed = time.time() - t0
        if i == len(segments) - 1:
            tail_seconds = elapsed
        piece = (result.get('text') or '').strip()
        if piece:
            parts.append(piece)
    return ' '.join(parts).strip(), tail_seconds


def score_stored(corpus: list[dict[str, Any]]) -> Score:
    score = Score()
    for clip in corpus:
        score.add(clip['id'], clip['stored_text'], clip['audio_seconds'], 0.0)
    return score


# ── Reporting ────────────────────────────────────────────────────────

def print_table(results: dict[str, Score]) -> None:
    cols = [
        ('config', 16, '{:<16}'),
        ('clips', 6, '{:>6}'),
        ('unpunct %', 10, '{:>10}'),
        ('capital %', 10, '{:>10}'),
        ('sent/100w', 10, '{:>10}'),
        ('dict err', 9, '{:>9}'),
        ('empty', 6, '{:>6}'),
        ('RTF', 7, '{:>7}'),
    ]
    print()
    print('  ' + ''.join(f.format(h) for h, _, f in cols))
    print('  ' + '-' * sum(w for _, w, _ in cols))
    for name, sc in results.items():
        s = sc.summary()
        if not s:
            continue
        rtf = f"{s['rtf']:.3f}" if s['rtf'] else '—'
        print(
            f"  {name:<16}{s['clips']:>6}"
            f"{s['pct_unpunctuated']:>10.1f}{s['pct_starts_capital']:>10.1f}"
            f"{s['sentences_per_100w']:>10.2f}{s['dict_errors']:>9}"
            f"{s['empty']:>6}{rtf:>7}"
        )
    print()
    print('  unpunct %  = clips with no . ! ? anywhere      (lower is better)')
    print('  capital %  = clips starting with a capital     (higher is better)')
    print('  sent/100w  = sentence density, prose is 5–7    (higher is better)')
    print('  dict err   = known mis-transcriptions          (lower is better)')
    print()


def main() -> int:
    ap = argparse.ArgumentParser(description='Replay saved dictations under different decoder settings.')
    ap.add_argument('--configs', default=','.join(CONFIGS),
                    help='comma-separated config names (default: all)')
    ap.add_argument('--sample', type=int, default=0,
                    help='use a stratified sample of N clips instead of the full corpus')
    ap.add_argument('--min-words', type=int, default=30,
                    help='ignore clips shorter than this (default 30 — punctuation only matters on real sentences)')
    ap.add_argument('--model', default=None, help='model id (default: config.MODEL)')
    ap.add_argument('--stored-only', action='store_true',
                    help='only score the text already in the DB; run no inference')
    ap.add_argument('--refresh', action='store_true', help='ignore cached results')
    ap.add_argument('--list', action='store_true', help='describe the corpus and exit')
    ap.add_argument('--postprocess', action='store_true',
                    help='also score each config after postprocess.apply() — the end-to-end result')
    ap.add_argument('--dictionary', default='',
                    help='comma-separated vocabulary terms to use for this run (e.g. "Claude Code")')
    args = ap.parse_args()

    if args.dictionary:
        config.DICTIONARY = [t.strip() for t in args.dictionary.split(',') if t.strip()]

    corpus = load_corpus(min_words=args.min_words)
    if not corpus:
        print('No saved recordings with audio found. Enable SAVE_RECORDINGS and dictate first.')
        return 1

    if args.list:
        total = sum(c['audio_seconds'] for c in corpus)
        print(f'{len(corpus)} clips >= {args.min_words} words, {total/60:.1f} min of audio')
        durs = sorted(c['audio_seconds'] for c in corpus)
        print(f'  duration  min {durs[0]:.1f}s  median {durs[len(durs)//2]:.1f}s  max {durs[-1]:.1f}s')
        models = {}
        for c in corpus:
            models[c['model']] = models.get(c['model'], 0) + 1
        for m, n in models.items():
            print(f'  {n:4d}  {m}')
        return 0

    if args.sample:
        corpus = stratified_sample(corpus, args.sample)

    model = args.model or config.MODEL
    if not args.stored_only:
        info = model_catalog.get(model)
        backend = info.backend if info else 'whisper-mlx'
        if backend != 'whisper-mlx':
            print(
                f'Model {model!r} uses the {backend!r} backend. The decoder settings this\n'
                f'tool sweeps (initial_prompt, condition_on_previous_text) are Whisper-only.\n'
                f'Re-run with --model mlx-community/whisper-large-v3-turbo, or use --stored-only.'
            )
            return 2

    total_audio = sum(c['audio_seconds'] for c in corpus)
    print(f'Corpus: {len(corpus)} clips, {total_audio/60:.1f} min of audio')

    results: dict[str, Score] = {'stored (today)': score_stored(corpus)}

    if not args.stored_only:
        names = [n.strip() for n in args.configs.split(',') if n.strip()]
        unknown = [n for n in names if n not in CONFIGS]
        if unknown:
            print(f'Unknown config(s): {unknown}. Known: {list(CONFIGS)}')
            return 2
        print(f'Model:  {model}')
        print(f'Configs: {", ".join(names)}\n')
        cache = _load_cache()
        for name in names:
            results[name] = run_config(name, corpus, model, cache, refresh=args.refresh)
            if args.postprocess:
                results[name + ' +post'] = _rescore_postprocessed(
                    name, corpus, model, cache
                )

    if args.postprocess:
        results['stored +post'] = _rescore_stored_postprocessed(corpus)

    print_table(results)
    return 0


def _rescore_postprocessed(
    name: str, corpus: list[dict[str, Any]], model: str, cache: dict[str, Any]
) -> Score:
    """Score a config's cached output after the deterministic clean-up stage."""
    import postprocess

    score = Score()
    for clip in corpus:
        hit = cache.get(f'{clip["id"]}|{name}|{model}')
        if hit is None:
            continue
        score.add(
            clip['id'],
            postprocess.apply(hit['text']),
            clip['audio_seconds'],
            hit['infer_s'],
        )
    return score


def _rescore_stored_postprocessed(corpus: list[dict[str, Any]]) -> Score:
    """What the existing history would look like with clean-up applied."""
    import postprocess

    score = Score()
    for clip in corpus:
        score.add(
            clip['id'], postprocess.apply(clip['stored_text']), clip['audio_seconds'], 0.0
        )
    return score


if __name__ == '__main__':
    raise SystemExit(main())
