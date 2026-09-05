#!/usr/bin/env python3
"""Put the real dictation overlay on screen, without running the engine.

    python say/tools/overlay_preview.py                 # newest stored clip
    python say/tools/overlay_preview.py --mic           # live microphone
    python say/tools/overlay_preview.py --wav path.wav  # a specific clip
    python say/tools/overlay_preview.py --hold          # sit in the armed state

This is the same `overlay.DictationOverlay` the engine builds, on the same
window, driven through the same `feed_audio()` tap — so what you see here is
what a dictation looks like. It walks the full lifecycle (arm → listen →
process → dismiss) so the entry and exit can be judged too, not just the
spectrum.
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
import wave

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from AppKit import (  # noqa: E402
    NSApplication,
    NSApplicationActivationPolicyAccessory,
)
from Foundation import NSTimer  # noqa: E402
from overlay import DictationOverlay  # noqa: E402
from PyObjCTools import AppHelper  # noqa: E402

BLOCK = 1024
RATE = 16_000
RECORDINGS_DIR = os.path.expanduser('~/Library/Application Support/Say/recordings')


def newest_clip() -> str | None:
    if not os.path.isdir(RECORDINGS_DIR):
        return None
    clips = [
        os.path.join(RECORDINGS_DIR, name)
        for name in os.listdir(RECORDINGS_DIR)
        if name.endswith('.wav')
    ]
    if not clips:
        return None
    return max(clips, key=os.path.getmtime)


def load_wav(path: str) -> np.ndarray:
    with wave.open(path, 'rb') as wf:
        width, channels = wf.getsampwidth(), wf.getnchannels()
        frames = wf.readframes(wf.getnframes())
    if width == 2:
        data = np.frombuffer(frames, dtype='<i2').astype('float32') / 32768.0
    else:
        data = np.frombuffer(frames, dtype='<f4').astype('float32')
    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    return data


def play_wav(overlay: DictationOverlay, audio: np.ndarray, stop: threading.Event) -> None:
    """Feed a clip at true speaking speed, exactly as the mic would."""
    period = BLOCK / float(RATE)
    next_at = time.perf_counter()
    for start in range(0, len(audio) - BLOCK + 1, BLOCK):
        if stop.is_set():
            return
        overlay.feed_audio(audio[start:start + BLOCK])
        next_at += period
        time.sleep(max(0.0, next_at - time.perf_counter()))


def synthetic(overlay: DictationOverlay, stop: threading.Event) -> None:
    """A voice-shaped stand-in: a swept formant under a syllable envelope."""
    period = BLOCK / float(RATE)
    phase = 0.0
    t = 0.0
    next_at = time.perf_counter()
    rng = np.random.default_rng(7)
    while not stop.is_set():
        n = np.arange(BLOCK) / RATE
        pitch = 130.0 + 40.0 * np.sin(t * 1.1)
        block = np.zeros(BLOCK, dtype='float32')
        for harmonic in range(1, 14):
            weight = np.exp(-harmonic / 4.5) * (1.0 + 0.8 * np.sin(t * 2.3 + harmonic))
            block += weight * np.sin(2 * np.pi * pitch * harmonic * n + phase * harmonic)
        block += 0.05 * rng.standard_normal(BLOCK).astype('float32')
        envelope = max(0.0, np.sin(t * 3.4)) ** 1.5 * 0.35
        overlay.feed_audio((block * envelope).astype('float32'))
        phase = (phase + 2 * np.pi * pitch * period) % (2 * np.pi)
        t += period
        next_at += period
        time.sleep(max(0.0, next_at - time.perf_counter()))


def _is_dark() -> bool:
    from AppKit import NSApp, NSAppearanceNameAqua, NSAppearanceNameDarkAqua

    try:
        appearance = NSApp().effectiveAppearance()
        match = appearance.bestMatchFromAppearancesWithNames_(
            [NSAppearanceNameAqua, NSAppearanceNameDarkAqua]
        )
        return match == NSAppearanceNameDarkAqua
    except Exception:
        return False


def _render_layer(overlay: DictationOverlay, scale: float = 3.0):
    """Snapshot what the overlay is drawing, straight from its own layer tree.

    Sidesteps Screen Recording permission entirely: the pixels come from the
    same CALayer the window server composites, not from the screen.
    """
    from Quartz import (
        CGBitmapContextCreate,
        CGBitmapContextCreateImage,
        CGColorSpaceCreateDeviceRGB,
        CGContextFillRect,
        CGContextScaleCTM,
        CGContextSetRGBFillColor,
        CGRectMake,
        kCGImageAlphaPremultipliedLast,
    )

    panel = overlay._panel
    if panel is None:
        return None
    layer = panel.contentView().layer()
    w_pt, h_pt = overlay._window_w, overlay._window_h
    w_px, h_px = int(w_pt * scale), int(h_pt * scale)
    ctx = CGBitmapContextCreate(
        None, w_px, h_px, 8, w_px * 4, CGColorSpaceCreateDeviceRGB(),
        kCGImageAlphaPremultipliedLast,
    )
    CGContextScaleCTM(ctx, scale, scale)
    # Stand the capture on whichever ground the overlay just picked, otherwise
    # a dark-mode (light ink) glyph is invisible on a light card.
    bg = (0.09, 0.10, 0.12) if _is_dark() else (0.957, 0.949, 0.937)
    CGContextSetRGBFillColor(ctx, bg[0], bg[1], bg[2], 1.0)
    CGContextFillRect(ctx, CGRectMake(0, 0, w_pt, h_pt))
    layer.renderInContext_(ctx)
    return CGBitmapContextCreateImage(ctx)


def main() -> int:
    parser = argparse.ArgumentParser(description='Preview the dictation overlay')
    parser.add_argument('--wav', help='clip to drive it with (default: newest stored)')
    parser.add_argument('--mic', action='store_true', help='use the live microphone')
    parser.add_argument('--synthetic', action='store_true', help='use a generated voice')
    parser.add_argument('--seconds', type=float, default=14.0, help='how long to run')
    parser.add_argument('--hold', action='store_true',
                        help='stay in the armed state (no processing / dismiss)')
    parser.add_argument('--capture', metavar='PATH.gif',
                        help='record the live layer tree to a GIF (no screen recording needed)')
    parser.add_argument('--capture-fps', type=float, default=30.0,
                        help='capture rate; 60 to inspect the entry frame by frame')
    parser.add_argument('--report', action='store_true',
                        help='print frame-interval statistics on exit')
    parser.add_argument('--appearance', choices=('system', 'light', 'dark'), default='system',
                        help='force the palette (the overlay follows the system by default)')
    args = parser.parse_args()

    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    if args.appearance != 'system':
        from AppKit import NSAppearance, NSAppearanceNameAqua, NSAppearanceNameDarkAqua

        name = NSAppearanceNameDarkAqua if args.appearance == 'dark' else NSAppearanceNameAqua
        app.setAppearance_(NSAppearance.appearanceNamed_(name))

    overlay = DictationOverlay()
    overlay.prewarm()

    stop = threading.Event()
    source: threading.Thread | None = None
    recorder = None

    if args.mic:
        from audio import AudioRecorder

        recorder = AudioRecorder()
        recorder.set_monitor_callback(overlay.feed_audio)
        recorder.arm()
        print('microphone armed', flush=True)
    elif args.synthetic:
        source = threading.Thread(target=synthetic, args=(overlay, stop), daemon=True)
    else:
        path = args.wav or newest_clip()
        if path is None:
            print('No stored clip found — use --synthetic or --mic.')
            return 1
        print(f'driving from {os.path.basename(path)}', flush=True)
        audio = load_wav(path)
        source = threading.Thread(target=play_wav, args=(overlay, audio, stop), daemon=True)

    frame_times: list[float] = []
    if args.report:
        # Instance attribute shadows the bound method, so the display link's
        # selector target picks the wrapper up without touching overlay.py.
        original_frame = overlay._frame

        def timed_frame() -> None:
            frame_times.append(time.perf_counter())
            original_frame()

        overlay._frame = timed_frame

    captured: list = []
    if args.capture:
        capture_every = 1.0 / args.capture_fps

        def grab(_timer=None) -> None:
            image = _render_layer(overlay)
            if image is not None:
                captured.append(image)

        NSTimer.scheduledTimerWithTimeInterval_repeats_block_(capture_every, True, grab)

    started = time.perf_counter()

    def finish() -> None:
        """Everything has to happen *before* stopping the loop.

        PyObjC's `runEventLoop()` terminates the process instead of returning,
        so anything left after it — including writing the capture — never runs.
        """
        stop.set()
        overlay.close()
        if recorder is not None:
            try:
                recorder.shutdown()
            except Exception:
                pass
        if args.capture and captured:
            from waveform_bench import _write_gif

            _write_gif(captured, args.capture, args.capture_fps)
            print(f'{args.capture}  {len(captured)} frames', flush=True)
        if args.report and len(frame_times) > 2:
            gaps = np.diff(np.array(frame_times)) * 1000.0
            late = int((gaps > 2.2 * np.percentile(gaps, 50)).sum())
            print(
                f'frames {len(frame_times)}  interval ms: '
                f'mean {gaps.mean():.2f}  p50 {np.percentile(gaps, 50):.2f}  '
                f'p99 {np.percentile(gaps, 99):.2f}  max {gaps.max():.2f}  '
                f'late frames {late}',
                flush=True,
            )
        AppHelper.stopEventLoop()

    def script(_timer=None) -> None:
        elapsed = time.perf_counter() - started
        if elapsed >= args.seconds:
            finish()
        elif not args.hold and elapsed >= args.seconds - 2.0:
            overlay.dismiss()
        elif not args.hold and elapsed >= args.seconds - 4.0:
            overlay.process()

    print('arming overlay…', flush=True)
    overlay.arm()
    overlay.listen()
    if source is not None:
        source.start()

    NSTimer.scheduledTimerWithTimeInterval_repeats_block_(0.05, True, script)
    AppHelper.runEventLoop(installInterrupt=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
