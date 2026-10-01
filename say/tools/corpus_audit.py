#!/usr/bin/env python3
"""Audit Say's historical recordings and transcripts.

Scans the local transcripts database (~/Library/Application Support/Say/transcripts.db),
measures error rates, inspects repetition loops and hallucinations, and tests
the post-processing pipeline against real recorded speech.

Usage:
    python say/tools/corpus_audit.py               # summary report & improvements
    python say/tools/corpus_audit.py --diffs       # show before/after transcript diffs
    python say/tools/corpus_audit.py --terms       # show tech term matches & frequencies
    python say/tools/corpus_audit.py --loops       # show repetition loop instances
"""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
from collections import Counter
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import postprocess  # noqa: E402
import settings  # noqa: E402

settings.apply_to_config()

DB_PATH = os.path.expanduser('~/Library/Application Support/Say/transcripts.db')


def load_corpus() -> list[dict[str, Any]]:
    if not os.path.exists(DB_PATH):
        return []
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute('SELECT * FROM transcripts ORDER BY id ASC').fetchall()
    return [dict(r) for r in rows]


def audit(corpus: list[dict[str, Any]], verbose_diffs: bool = False, verbose_loops: bool = False) -> None:
    if not corpus:
        print(f"No transcripts database found at {DB_PATH}")
        return

    total_clips = len(corpus)
    total_audio_s = sum(c.get('audio_seconds', 0.0) for c in corpus)
    total_words = sum(c.get('word_count', 0) for c in corpus)

    models: Counter[str] = Counter()
    apps: Counter[str] = Counter()

    for c in corpus:
        models[c.get('model') or 'unknown'] += 1
        apps[c.get('target_app') or 'unknown'] += 1

    print("=" * 70)
    print("  SAY CORPUS AUDIT REPORT")
    print("=" * 70)
    print(f"  Database:        {DB_PATH}")
    print(f"  Total Clips:     {total_clips}")
    print(f"  Total Audio:     {total_audio_s / 60:.1f} minutes ({total_audio_s / 3600:.2f} hours)")
    print(f"  Total Words:     {total_words:,}")
    print(f"  Date Span:       {corpus[0].get('ts_utc', '')[:10]} to {corpus[-1].get('ts_utc', '')[:10]}")

    print("\n── Models Used ────────────────────────────────────────────────────────")
    for m, cnt in models.most_common():
        pct = 100.0 * cnt / total_clips
        print(f"  {cnt:4d} ({pct:5.1f}%)  {m}")

    print("\n── Top Target Applications ────────────────────────────────────────────")
    for a, cnt in apps.most_common(7):
        pct = 100.0 * cnt / total_clips
        print(f"  {cnt:4d} ({pct:5.1f}%)  {a}")

    # Evaluate post-processing impact
    cleaned_diffs: list[tuple[int, str, str, str]] = []
    loop_instances: list[tuple[int, str, str]] = []
    tail_fixes: list[tuple[int, str, str]] = []
    replacement_fixes: list[tuple[int, str, str]] = []

    # Check replacements pattern
    repls = getattr(config, 'DICTIONARY_REPLACEMENTS', {}) or {}

    for c in corpus:
        raw = c.get('text', '')
        clean = postprocess.apply(raw, target_app=c.get('target_app'))

        # Check repetition loop
        col = postprocess.collapse_repetitions(raw)
        if col != raw:
            loop_instances.append((c['id'], raw, col))

        # Check tail cleaner
        tail = postprocess.clean_hallucinated_tail(raw)
        if tail != raw:
            tail_fixes.append((c['id'], raw, tail))

        # Check replacements
        for wrong in repls:
            if re.search(r'\b' + re.escape(wrong) + r'\b', raw, re.I):
                replacement_fixes.append((c['id'], wrong, raw))
                break

        if clean != raw:
            diffs_desc = []
            if col != raw:
                diffs_desc.append("loop collapsed")
            if tail != raw:
                diffs_desc.append("hallucination stripped")
            if any(re.search(r'\b' + re.escape(w) + r'\b', raw, re.I) for w in repls):
                diffs_desc.append("tech term replaced")
            cleaned_diffs.append((c['id'], raw, clean, ", ".join(diffs_desc) or "formatting/punct"))

    print("\n── Pipeline Quality Metrics ───────────────────────────────────────────")
    print(f"  Transcripts Improved by Post-Process: {len(cleaned_diffs)} / {total_clips} ({100.0 * len(cleaned_diffs) / total_clips:.1f}%)")
    print(f"  Decoder Repetition Loops Collapsed:    {len(loop_instances)}")
    print(f"  Trailing Hallucinations Stripped:      {len(tail_fixes)}")
    print(f"  Known Tech Mishearings Corrected:      {len(replacement_fixes)}")

    if verbose_loops and loop_instances:
        print("\n── Repetition Loops Detected ──────────────────────────────────────────")
        for cid, raw, col in loop_instances:
            print(f"  [ID {cid}]")
            print(f"    Raw:       {raw[:100]}...")
            print(f"    Collapsed: {col[:100]}...")

    if verbose_diffs and cleaned_diffs:
        print("\n── Sample Post-Processing Improvements ────────────────────────────────")
        for cid, raw, clean, reason in cleaned_diffs[:15]:
            print(f"  [ID {cid}] ({reason}):")
            print(f"    - {raw[:100]}...")
            print(f"    + {clean[:100]}...")


def print_terms_report(corpus: list[dict[str, Any]]) -> None:
    repls = getattr(config, 'DICTIONARY_REPLACEMENTS', {}) or {}
    terms = getattr(config, 'DICTIONARY', []) or []

    print("\n── Configured Terminology Matches in Historical Audio ──────────────────")
    term_counts: Counter[str] = Counter()
    for c in corpus:
        t = c.get('text', '')
        for term in terms:
            if re.search(r'\b' + re.escape(term) + r'\b', t, re.I):
                term_counts[term] += 1

    for term, count in term_counts.most_common():
        print(f"  {count:4d}x  {term}")

    print("\n── Audited Technical Replacements (Acoustic Corrections) ───────────────")
    for wrong, right in sorted(repls.items(), key=lambda x: x[1]):
        matches = sum(1 for c in corpus if re.search(r'\b' + re.escape(wrong) + r'\b', c.get('text', ''), re.I))
        if matches > 0:
            print(f"  {matches:3d} matches:  '{wrong}' -> '{right}'")


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit Say recordings and transcription quality.")
    parser.add_argument("--diffs", action="store_true", help="Print diffs of cleaned transcripts")
    parser.add_argument("--loops", action="store_true", help="Print detected repetition loops")
    parser.add_argument("--terms", action="store_true", help="Print tech terms and replacement frequencies")
    args = parser.parse_args()

    corpus = load_corpus()
    audit(corpus, verbose_diffs=args.diffs, verbose_loops=args.loops)
    if args.terms:
        print_terms_report(corpus)
    return 0


if __name__ == '__main__':
    sys.exit(main())
