#!/usr/bin/env python3
"""Measure and preview the overlay's audio→motion engine on real dictations.

The overlay's feel is a tuning problem, and tuning it by eye against live
speech is how you end up with a spectrum that only looks right for one voice in
one room. This replays the stored corpus instead:

    python say/tools/waveform_bench.py --stats            # per-band statistics
    python say/tools/waveform_bench.py --gif out.gif      # what it actually does
    python say/tools/waveform_bench.py --strip out.png    # a filmstrip of frames

`--stats` is the one that settles arguments: if the per-band medians slope, the
spectral tilt is wrong and one end of the glyph is dead. The renderer draws
through the same `waveform.silhouette()` the real overlay draws through, so a
frame here is the frame you get on screen.
"""

from __future__ import annotations

import argparse
import math
import os
import sys
import wave

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import waveform  # noqa: E402
from waveform import BandAnalyzer, BarMotion, GlyphGeometry, silhouette  # noqa: E402

RECORDINGS_DIR = os.path.expanduser('~/Library/Application Support/Say/recordings')
GEOMETRY = GlyphGeometry()
BLOCK = 1024
FPS = 60.0


def load_wav(path: str) -> tuple[np.ndarray, int]:
    with wave.open(path, 'rb') as wf:
        rate = wf.getframerate()
        frames = wf.readframes(wf.getnframes())
        width = wf.getsampwidth()
        channels = wf.getnchannels()
    if width == 2:
        data = np.frombuffer(frames, dtype='<i2').astype('float32') / 32768.0
    elif width == 4:
        data = np.frombuffer(frames, dtype='<f4').astype('float32')
    else:
        raise ValueError(f'unsupported sample width {width} in {path}')
    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    return data, rate


def corpus(limit: int | None = None) -> list[str]:
    if not os.path.isdir(RECORDINGS_DIR):
        return []
    paths = [
        os.path.join(RECORDINGS_DIR, name)
        for name in os.listdir(RECORDINGS_DIR)
        if name.endswith('.wav')
    ]
    paths.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return paths[:limit] if limit else paths


def run_clip(path: str) -> tuple[list[tuple[float, ...]], list[float], list[tuple[float, ...]]]:
    """Replay one clip at the real block size and frame rate.

    Returns (band levels per block, gate per block, bar heights per frame).
    """
    audio, rate = load_wav(path)
    analyzer = BandAnalyzer(bands=waveform.BAND_COUNT, sample_rate=rate)
    motion = BarMotion(bands=waveform.BAND_COUNT)
    per_block_levels: list[tuple[float, ...]] = []
    gates: list[float] = []
    frames: list[tuple[float, ...]] = []

    block_seconds = BLOCK / float(rate)
    ticks_per_block = max(1, int(round(block_seconds * FPS)))
    dt = block_seconds / ticks_per_block

    for start in range(0, len(audio) - BLOCK + 1, BLOCK):
        analyzer.feed(audio[start:start + BLOCK])
        levels, gate = analyzer.snapshot()
        per_block_levels.append(levels)
        gates.append(gate)
        for _ in range(ticks_per_block):
            frames.append(motion.tick(dt, levels, gate, BarMotion.LISTEN))
    return per_block_levels, gates, frames


def cmd_stats(limit: int | None) -> int:
    paths = corpus(limit)
    if not paths:
        print(f'No recordings under {RECORDINGS_DIR}')
        return 1

    bands = waveform.BAND_COUNT
    voiced: list[list[float]] = [[] for _ in range(bands)]
    heights: list[list[float]] = [[] for _ in range(bands)]
    open_blocks = 0
    total_blocks = 0
    seconds = 0.0

    for path in paths:
        try:
            levels, gates, frames = run_clip(path)
        except Exception as exc:  # a corrupt clip must not end the sweep
            print(f'  skip {os.path.basename(path)}: {exc}')
            continue
        total_blocks += len(gates)
        seconds += len(gates) * BLOCK / float(waveform.SAMPLE_RATE)
        for row, gate in zip(levels, gates, strict=False):
            if gate > 0.5:
                open_blocks += 1
                for k in range(bands):
                    voiced[k].append(row[k])
        for row in frames:
            for k in range(bands):
                heights[k].append(row[k])

    if not open_blocks:
        print('No voiced blocks detected — the gate never opened.')
        return 1

    print(f'clips {len(paths)}   audio {seconds / 60.0:.1f} min   '
          f'blocks {total_blocks}   voiced {100.0 * open_blocks / total_blocks:.1f}%')
    print()
    print('band   Hz range        voiced p50  p90    frame mean  p99   bar')
    edges = waveform.band_edges()
    bin_hz = waveform.SAMPLE_RATE / waveform.FFT_SIZE
    for k in range(bands):
        lo, hi = edges[k]
        v = np.array(voiced[k])
        h = np.array(heights[k])
        p50, p90 = float(np.percentile(v, 50)), float(np.percentile(v, 90))
        mean, p99 = float(h.mean()), float(np.percentile(h, 99))
        bar = '#' * int(round(p50 * 24))
        print(f'{k:>3}   {lo * bin_hz:>5.0f}-{hi * bin_hz:<5.0f}Hz   '
              f'{p50:>6.2f} {p90:>6.2f}   {mean:>8.2f} {p99:>6.2f}   {bar}')

    all_h = np.array([v for row in heights for v in row])
    print()
    print(f'frame height: mean {all_h.mean():.3f}  p50 {np.percentile(all_h, 50):.3f}  '
          f'p95 {np.percentile(all_h, 95):.3f}  max {all_h.max():.3f}')
    slope = float(np.polyfit(range(bands), [np.percentile(np.array(v), 50) for v in voiced], 1)[0])
    print(f'tilt residual: {slope:+.4f} level/band  (flat is 0; '
          f'raise TILT_DB_PER_BAND if negative)')
    return 0


# ── rendering (same geometry the overlay draws) ─────────────────

def _render_frames(frames, geometry, scale, reveal=1.0, dark=False):
    from Quartz import (
        CGBitmapContextCreate,
        CGBitmapContextCreateImage,
        CGColorSpaceCreateDeviceRGB,
        CGContextAddLineToPoint,
        CGContextBeginPath,
        CGContextClosePath,
        CGContextFillPath,
        CGContextFillRect,
        CGContextMoveToPoint,
        CGContextScaleCTM,
        CGContextSetRGBFillColor,
        CGRectMake,
        kCGImageAlphaPremultipliedLast,
    )

    pad_x, pad_y = 12.0, 10.0
    w_pt = geometry.width + 2 * pad_x
    h_pt = geometry.height + 2 * pad_y
    w_px, h_px = int(w_pt * scale), int(h_pt * scale)
    space = CGColorSpaceCreateDeviceRGB()

    bg = (0.09, 0.10, 0.12) if dark else (0.957, 0.949, 0.937)
    ink = (0.965, 0.969, 0.980) if dark else (0.063, 0.071, 0.094)

    images = []
    for levels in frames:
        ctx = CGBitmapContextCreate(
            None, w_px, h_px, 8, w_px * 4, space, kCGImageAlphaPremultipliedLast
        )
        CGContextScaleCTM(ctx, scale, scale)
        CGContextSetRGBFillColor(ctx, bg[0], bg[1], bg[2], 1.0)
        CGContextFillRect(ctx, CGRectMake(0, 0, w_pt, h_pt))
        points = silhouette(levels, geometry, reveal=reveal, origin=(pad_x, pad_y))
        CGContextSetRGBFillColor(ctx, ink[0], ink[1], ink[2], 1.0)
        CGContextBeginPath(ctx)
        CGContextMoveToPoint(ctx, points[0][0], points[0][1])
        for x, y in points[1:]:
            CGContextAddLineToPoint(ctx, x, y)
        CGContextClosePath(ctx)
        CGContextFillPath(ctx)
        images.append(CGBitmapContextCreateImage(ctx))
    return images, w_px, h_px


def _write_gif(images, path: str, fps: float) -> None:
    from Foundation import NSURL
    from Quartz import (
        CGImageDestinationAddImage,
        CGImageDestinationCreateWithURL,
        CGImageDestinationFinalize,
        CGImageDestinationSetProperties,
    )

    url = NSURL.fileURLWithPath_(path)
    dest = CGImageDestinationCreateWithURL(url, 'com.compuserve.gif', len(images), None)
    CGImageDestinationSetProperties(
        dest, {'{GIF}': {'LoopCount': 0}}
    )
    frame_props = {'{GIF}': {'DelayTime': 1.0 / fps, 'UnclampedDelayTime': 1.0 / fps}}
    for image in images:
        CGImageDestinationAddImage(dest, image, frame_props)
    CGImageDestinationFinalize(dest)


def _write_png(image, path: str) -> None:
    from Foundation import NSURL
    from Quartz import (
        CGImageDestinationAddImage,
        CGImageDestinationCreateWithURL,
        CGImageDestinationFinalize,
    )

    url = NSURL.fileURLWithPath_(path)
    dest = CGImageDestinationCreateWithURL(url, 'public.png', 1, None)
    CGImageDestinationAddImage(dest, image, None)
    CGImageDestinationFinalize(dest)


def cmd_gif(out: str, wav: str | None, seconds: float, gif_fps: float, dark: bool) -> int:
    path = wav or (corpus(1)[0] if corpus(1) else None)
    if path is None:
        print('No clip to render.')
        return 1
    _, _, frames = run_clip(path)
    keep = int(seconds * FPS)
    frames = frames[:keep] if keep else frames
    step = max(1, int(round(FPS / gif_fps)))
    frames = frames[::step]
    images, w, h = _render_frames(frames, GEOMETRY, scale=4.0, dark=dark)
    _write_gif(images, out, FPS / step)
    print(f'{out}  {len(images)} frames  {w}x{h}px  from {os.path.basename(path)}')
    return 0


def cmd_strip(out: str, wav: str | None, count: int, dark: bool) -> int:
    from Quartz import (
        CGBitmapContextCreate,
        CGBitmapContextCreateImage,
        CGColorSpaceCreateDeviceRGB,
        CGContextDrawImage,
        CGContextFillRect,
        CGContextSetRGBFillColor,
        CGRectMake,
        kCGImageAlphaPremultipliedLast,
    )

    path = wav or (corpus(1)[0] if corpus(1) else None)
    if path is None:
        print('No clip to render.')
        return 1
    _, gates, frames = run_clip(path)
    # Sample across the voiced middle of the clip so the strip is not all silence.
    voiced = [i for i, row in enumerate(frames) if max(row) > 0.05]
    if len(voiced) > count:
        picks = [voiced[int(i * (len(voiced) - 1) / (count - 1))] for i in range(count)]
    else:
        picks = list(range(0, len(frames), max(1, len(frames) // count)))[:count]
    chosen = [frames[i] for i in picks]

    images, w, h = _render_frames(chosen, GEOMETRY, scale=4.0, dark=dark)
    cols = 4
    rows = int(math.ceil(len(images) / cols))
    space = CGColorSpaceCreateDeviceRGB()
    ctx = CGBitmapContextCreate(
        None, w * cols, h * rows, 8, w * cols * 4, space, kCGImageAlphaPremultipliedLast
    )
    CGContextSetRGBFillColor(ctx, 1, 1, 1, 1)
    CGContextFillRect(ctx, CGRectMake(0, 0, w * cols, h * rows))
    for index, image in enumerate(images):
        col = index % cols
        row = rows - 1 - index // cols
        CGContextDrawImage(ctx, CGRectMake(col * w, row * h, w, h), image)
    _write_png(CGBitmapContextCreateImage(ctx), out)
    print(f'{out}  {len(images)} frames  {w * cols}x{h * rows}px  from {os.path.basename(path)}')
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description='Overlay waveform engine bench')
    parser.add_argument('--stats', action='store_true', help='per-band statistics over the corpus')
    parser.add_argument('--gif', metavar='PATH', help='render an animated GIF')
    parser.add_argument('--strip', metavar='PATH', help='render a filmstrip PNG')
    parser.add_argument('--wav', metavar='PATH', help='clip to render (default: newest)')
    parser.add_argument('--limit', type=int, default=None, help='clips to include in --stats')
    parser.add_argument('--seconds', type=float, default=6.0, help='GIF length')
    parser.add_argument('--fps', type=float, default=30.0, help='GIF frame rate')
    parser.add_argument('--frames', type=int, default=12, help='filmstrip frame count')
    parser.add_argument('--dark', action='store_true', help='render the dark-appearance palette')
    parser.add_argument('--bands', type=int, default=None, help='override band count')
    parser.add_argument('--colw', type=float, default=None, help='override column width (pt)')
    parser.add_argument('--barh', type=float, default=None, help='override max bar height (pt)')
    parser.add_argument(
        '--set', action='append', default=[], metavar='NAME=VALUE',
        help='override a waveform.py constant for this run (repeatable)',
    )
    args = parser.parse_args()

    for assignment in args.set:
        name, _, value = assignment.partition('=')
        setattr(waveform, name.strip(), float(value))
    if args.bands:
        waveform.BAND_COUNT = args.bands
    global GEOMETRY
    GEOMETRY = GlyphGeometry(
        bands=args.bands or waveform.BAND_COUNT,
        column_width=args.colw if args.colw else GlyphGeometry.column_width,
        bar_height=args.barh if args.barh else GlyphGeometry.bar_height,
    )

    if args.stats:
        return cmd_stats(args.limit)
    if args.gif:
        return cmd_gif(args.gif, args.wav, args.seconds, args.fps, args.dark)
    if args.strip:
        return cmd_strip(args.strip, args.wav, args.frames, args.dark)
    parser.print_help()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
