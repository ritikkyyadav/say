# overlay.py — Floating dictation overlay for Say.
#
# A two-state floating window at bottom-center of the screen:
#
#   DORMANT — collapsed to a small glowing dot when the app is idle.
#   ACTIVE  — a dark charcoal pill with a clean white waveform that
#             ripples with mic amplitude, plus a ghost cancel (✕) and a
#             solid-white finish (✓) button. Monochrome on dark, per the
#             design system (see brand.OVERLAY_*).
#
# Uses PyObjC (AppKit/Cocoa) for native macOS rendering.

from __future__ import annotations

import math
import os
import threading
from collections import deque
from collections.abc import Callable
from enum import Enum

import brand
import objc
from AppKit import (
    NSBezierPath,
    NSButton,
    NSColor,
    NSFont,
    NSMakeRect,
    NSScreen,
    NSSound,
    NSTimer,
    NSView,
    NSWindow,
    NSWindowStyleMaskBorderless,
)
from logging_setup import get_logger
from Quartz import CGColorCreateGenericRGB

_log = get_logger('overlay')

# ── Overlay sizing ──────────────────────────────────────────────
# Compact pill matching the reference: a tight capsule that hugs the
# waveform, flanked by two small circular controls. Half the footprint
# of the old 280×76 slab so it reads as a polished HUD, not a banner.
OVERLAY_WIDTH = 178
OVERLAY_HEIGHT = 46
CORNER_RADIUS = 23.0          # pill — radius == half height
BUTTON_SIZE = 26
ANIMATION_FPS = 60            # 60fps for buttery-smooth motion
AMPLITUDE_BUFFER_SIZE = 18

# ── Dormant dot mode ────────────────────────────────────────────
DORMANT_DOT_RADIUS = 4.5
DORMANT_BREATHE_RATE = 1.4
TRANSITION_DURATION = 0.30
COLLAPSE_DURATION = 0.42

# ── Waveform bars ───────────────────────────────────────────────
# More, thinner bars packed tightly = a denser, more "voice-like"
# signature. Heights are driven by a critically-damped spring (see
# _WaveformView.tick) so each bar settles with an organic, fluid bounce
# rather than a flat exponential glide.
WAVE_BAR_COUNT = 18
WAVE_BAR_WIDTH = 2.4
WAVE_BAR_GAP = 2.7
WAVE_BAR_MIN_H = 3.0          # smallest visible bar
WAVE_BAR_MAX_H = 26.0         # tallest possible
WAVE_BAR_RADIUS = 1.2         # rounded bar tips

# Spring constants for per-bar height. Tuned for a lively, slightly
# under-damped settle — enough overshoot to feel alive, not jittery.
BAR_SPRING_STIFFNESS = 220.0
BAR_SPRING_DAMPING = 24.0

# ── Overlay palette — dark charcoal pill, monochrome controls ────
# The overlay "stays dark per design choice" (mirrors brand.OVERLAY_*).
# A near-black pill carries a white waveform. The finish button is a
# solid white disc (primary affordance); cancel is a translucent ghost
# disc (secondary) — exactly the reference look, no rose anywhere.
PILL_BG    = (0.137, 0.141, 0.157)   # #232428  charcoal pill body
PILL_RIM   = (1.000, 1.000, 1.000)   # faint top rim (used at low alpha)
WAVE       = (0.965, 0.969, 0.980)   # #F6F7FA  near-white waveform bars
GHOST      = (1.000, 1.000, 1.000)   # cancel disc (low alpha) + its ✕ glyph
FINISH     = (0.965, 0.969, 0.980)   # finish disc — solid white
GLYPH_DARK = (0.094, 0.098, 0.114)   # #18191D  dark ✓ on the white disc
TEXT_DIM   = (0.831, 0.875, 0.937)   # mode text on the pill

# A short, soft built-in macOS chime.
WAKE_SOUND_PATH = '/System/Library/Sounds/Tink.aiff'

# Window levels
NSFloatingWindowLevel = 5


class OverlayState(str, Enum):
    HIDDEN = 'hidden'
    DORMANT = 'dormant'
    RECORDING = 'recording'
    PROCESSING = 'processing'


def _ease_out_cubic(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1.0 - (1.0 - t) ** 3


class _WaveformView(NSView):
    """Custom NSView that draws either a small dormant dot or a row of
    rounded vertical bars whose heights respond to mic amplitude.

    Class name intentionally mirrors the previous `_OrbView` so the rest
    of the file (and any external references) keep working without a
    rename cascade.
    """

    def initWithFrame_(self, frame):
        self = objc.super(_WaveformView, self).initWithFrame_(frame)
        if self is None:
            return None
        self._amplitude = 0.0
        self._target_amplitude = 0.0
        self._breathe_phase = 0.0
        self._processing_phase = 0.0
        self._bloom = 0.0
        self._state = OverlayState.HIDDEN
        # Per-bar state: spring-driven height (0..1), its velocity, and a
        # small phase offset so bars don't all peak in lockstep.
        self._bar_levels = [0.0] * WAVE_BAR_COUNT
        self._bar_vel = [0.0] * WAVE_BAR_COUNT
        self._bar_phase = [i * 0.42 for i in range(WAVE_BAR_COUNT)]
        # Center-weighted envelope: bars taller in the middle, tapering to
        # the edges — the natural bell shape of the reference waveform.
        center = (WAVE_BAR_COUNT - 1) / 2.0
        self._bar_env = [
            0.45 + 0.55 * (1.0 - (abs(i - center) / center) ** 1.6)
            for i in range(WAVE_BAR_COUNT)
        ]
        return self

    def isFlipped(self):
        return False

    def set_amplitude(self, amplitude: float) -> None:
        self._target_amplitude = max(0.0, min(1.0, amplitude))

    def set_state(self, state: OverlayState) -> None:
        self._state = state
        self.setNeedsDisplay_(True)

    def set_bloom(self, bloom: float) -> None:
        self._bloom = max(0.0, min(1.0, bloom))
        self.setNeedsDisplay_(True)

    def tick(self, dt: float = 1.0 / ANIMATION_FPS) -> None:
        self._breathe_phase += DORMANT_BREATHE_RATE * dt
        self._processing_phase += 2.4 * dt
        # Smooth amplitude with an EMA so peaks decay naturally. Slightly
        # snappier attack than decay so the wave "jumps" to your voice.
        amp_k = 0.34 if self._target_amplitude > self._amplitude else 0.18
        self._amplitude += (self._target_amplitude - self._amplitude) * amp_k

        amp = self._amplitude
        for i in range(WAVE_BAR_COUNT):
            self._bar_phase[i] += dt * (3.0 + 0.18 * i)
            wobble = 0.5 + 0.5 * math.sin(self._bar_phase[i])
            # Voice-driven height, shaped by the center-weighted envelope.
            target = amp * (0.55 + 0.45 * wobble) * self._bar_env[i]
            # When processing, run a travelling wave across the bars.
            if self._state == OverlayState.PROCESSING:
                target = (0.16 + 0.12 * math.sin(self._processing_phase + i * 0.55)) \
                    * self._bar_env[i]
            elif self._state != OverlayState.RECORDING:
                # Idle: a barely-there breathing shimmer.
                target = (0.05 + 0.04 * math.sin(self._breathe_phase + i * 0.7)) \
                    * self._bar_env[i]

            # Critically-damped spring → fluid, organic settle with a hint
            # of overshoot. Far nicer than a flat exponential lerp.
            accel = (target - self._bar_levels[i]) * BAR_SPRING_STIFFNESS \
                - self._bar_vel[i] * BAR_SPRING_DAMPING
            self._bar_vel[i] += accel * dt
            self._bar_levels[i] += self._bar_vel[i] * dt
            # Clamp so overshoot never clips outside the pill.
            if self._bar_levels[i] < 0.0:
                self._bar_levels[i] = 0.0
                self._bar_vel[i] *= -0.3
            elif self._bar_levels[i] > 1.0:
                self._bar_levels[i] = 1.0
                self._bar_vel[i] *= -0.3

        self.setNeedsDisplay_(True)

    def drawRect_(self, rect):
        bounds = self.bounds()
        cx = bounds.size.width / 2.0
        cy = bounds.size.height / 2.0
        eased_bloom = _ease_out_cubic(self._bloom)

        # Dormant accent dot — visible when collapsed, fades out as we bloom.
        dormant_alpha = 1.0 - eased_bloom
        if dormant_alpha > 0.01:
            self._draw_dormant_dot(cx, cy, dormant_alpha)

        if eased_bloom < 0.05:
            return

        total_w = (
            WAVE_BAR_COUNT * WAVE_BAR_WIDTH
            + (WAVE_BAR_COUNT - 1) * WAVE_BAR_GAP
        )
        left = cx - total_w / 2.0

        wave_color = NSColor.colorWithCalibratedRed_green_blue_alpha_(
            WAVE[0], WAVE[1], WAVE[2], 0.98 * eased_bloom,
        )
        wave_color.setFill()

        for i in range(WAVE_BAR_COUNT):
            level = self._bar_levels[i]
            h = WAVE_BAR_MIN_H + (WAVE_BAR_MAX_H - WAVE_BAR_MIN_H) * level
            x = left + i * (WAVE_BAR_WIDTH + WAVE_BAR_GAP)
            y = cy - h / 2.0
            bar_rect = NSMakeRect(x, y, WAVE_BAR_WIDTH, h)
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                bar_rect, WAVE_BAR_RADIUS, WAVE_BAR_RADIUS,
            ).fill()

    def _draw_dormant_dot(self, cx: float, cy: float, alpha: float) -> None:
        """A small white dot ringed by a soft dark halo, so the idle
        indicator stays legible on light or dark desktops alike."""
        amp = self._amplitude
        radius = DORMANT_DOT_RADIUS * (1.0 + 0.18 * amp)

        halo_r = radius * 2.4
        halo = NSColor.colorWithCalibratedRed_green_blue_alpha_(
            PILL_BG[0], PILL_BG[1], PILL_BG[2], 0.40 * alpha,
        )
        halo.setFill()
        NSBezierPath.bezierPathWithOvalInRect_(
            NSMakeRect(cx - halo_r, cy - halo_r, halo_r * 2.0, halo_r * 2.0)
        ).fill()

        core = NSColor.colorWithCalibratedRed_green_blue_alpha_(
            WAVE[0], WAVE[1], WAVE[2], alpha,
        )
        core.setFill()
        NSBezierPath.bezierPathWithOvalInRect_(
            NSMakeRect(cx - radius, cy - radius, radius * 2.0, radius * 2.0)
        ).fill()


class DictationOverlay:
    """Floating macOS overlay window for dictation feedback.

    Lives at bottom-center as a small dot when idle and blooms into a
    dark charcoal pill with a white waveform while recording.
    """

    def __init__(
        self,
        on_cancel: Callable[[], None] | None = None,
        on_finish: Callable[[], None] | None = None,
    ):
        self._on_cancel = on_cancel
        self._on_finish = on_finish
        self._window = None
        self._wave_view = None
        self._bg_view = None
        self._cancel_btn = None
        self._finish_btn = None
        self._mode_label = None
        self._timer = None
        self._state = OverlayState.HIDDEN
        self._bloom = 0.0
        self._target_bloom = 0.0
        self._wake_sound = None
        self._amplitude_buffer = deque(
            [0.0] * AMPLITUDE_BUFFER_SIZE, maxlen=AMPLITUDE_BUFFER_SIZE,
        )
        self._lock = threading.Lock()

        self._build_window()
        self._load_wake_sound()

    # ── Setup ───────────────────────────────────────────────────

    @staticmethod
    def _active_screen():
        try:
            from AppKit import NSEvent
            cursor = NSEvent.mouseLocation()
            for screen in NSScreen.screens():
                frame = screen.frame()
                if (
                    frame.origin.x <= cursor.x < frame.origin.x + frame.size.width
                    and frame.origin.y <= cursor.y < frame.origin.y + frame.size.height
                ):
                    return screen
        except Exception:
            pass
        return NSScreen.mainScreen()

    @staticmethod
    def _frame_for_screen(screen):
        screen_frame = screen.visibleFrame()
        x = (screen_frame.size.width - OVERLAY_WIDTH) / 2.0 + screen_frame.origin.x
        y = screen_frame.origin.y + 36
        return NSMakeRect(x, y, OVERLAY_WIDTH, OVERLAY_HEIGHT)

    def _reposition_for_active_screen(self) -> None:
        if self._window is None:
            return
        screen = self._active_screen()
        if screen is None:
            return
        frame = self._frame_for_screen(screen)
        self._window.setFrame_display_(frame, False)

    def _build_window(self) -> None:
        screen = self._active_screen()
        if screen is None:
            _log.error('No screen found for overlay.')
            return

        frame = self._frame_for_screen(screen)

        self._window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            frame,
            NSWindowStyleMaskBorderless,
            2,
            False,
        )
        self._window.setLevel_(NSFloatingWindowLevel + 10)
        self._window.setOpaque_(False)
        self._window.setBackgroundColor_(NSColor.clearColor())
        self._window.setHasShadow_(False)
        self._window.setIgnoresMouseEvents_(True)
        self._window.setCollectionBehavior_(
            (1 << 0) | (1 << 4)
        )
        self._window.setAlphaValue_(0.0)

        content = self._window.contentView()

        # Dark charcoal pill background — fades in as we bloom.
        bg_view = NSView.alloc().initWithFrame_(
            NSMakeRect(0, 0, OVERLAY_WIDTH, OVERLAY_HEIGHT),
        )
        content.addSubview_(bg_view)
        bg_view.setWantsLayer_(True)
        layer = bg_view.layer()
        if layer:
            layer.setBackgroundColor_(
                CGColorCreateGenericRGB(
                    PILL_BG[0], PILL_BG[1], PILL_BG[2], 0.98,
                ),
            )
            layer.setCornerRadius_(CORNER_RADIUS)
            layer.setBorderWidth_(0.8)
            layer.setBorderColor_(
                CGColorCreateGenericRGB(
                    PILL_RIM[0], PILL_RIM[1], PILL_RIM[2], 0.08,
                ),
            )
            layer.setOpacity_(0.0)
        self._bg_view = bg_view

        # Waveform view — fills the central column between the buttons.
        side_pad = BUTTON_SIZE + 12
        wave_x = side_pad
        wave_w = OVERLAY_WIDTH - 2 * side_pad
        wave_y = 5
        wave_h = OVERLAY_HEIGHT - wave_y - 5
        self._wave_view = _WaveformView.alloc().initWithFrame_(
            NSMakeRect(wave_x, wave_y, wave_w, wave_h),
        )
        content.addSubview_(self._wave_view)

        # Cancel button — translucent ghost disc with a white ✕.
        cancel_y = (OVERLAY_HEIGHT - BUTTON_SIZE) / 2
        self._cancel_btn = self._make_button(
            NSMakeRect(8, cancel_y, BUTTON_SIZE, BUTTON_SIZE),
            title='✕',
            action=self._cancel_clicked,
            fill_rgb=GHOST,
            fill_alpha=0.14,
            glyph_rgb=GHOST,
            rim_alpha=0.18,
        )
        content.addSubview_(self._cancel_btn)

        # Finish button — solid white disc with a dark ✓ (primary action).
        finish_y = (OVERLAY_HEIGHT - BUTTON_SIZE) / 2
        self._finish_btn = self._make_button(
            NSMakeRect(
                OVERLAY_WIDTH - BUTTON_SIZE - 8,
                finish_y,
                BUTTON_SIZE,
                BUTTON_SIZE,
            ),
            title='✓',
            action=self._finish_clicked,
            fill_rgb=FINISH,
            fill_alpha=1.0,
            glyph_rgb=GLYPH_DARK,
            rim_alpha=0.0,
        )
        content.addSubview_(self._finish_btn)

        self._mode_label = self._make_label(
            NSMakeRect(wave_x, 0, wave_w, 0),
            text='',
            font_size=9.5,
            color=brand.color(TEXT_DIM, alpha=0.78),
        )
        content.addSubview_(self._mode_label)

        self._cancel_btn.setAlphaValue_(0.0)
        self._finish_btn.setAlphaValue_(0.0)
        self._mode_label.setAlphaValue_(0.0)

    def _load_wake_sound(self) -> None:
        if not os.path.exists(WAKE_SOUND_PATH):
            return
        try:
            sound = NSSound.alloc().initWithContentsOfFile_byReference_(
                WAKE_SOUND_PATH, True,
            )
            if sound is not None:
                sound.setVolume_(0.4)
                self._wake_sound = sound
        except Exception:
            _log.debug('Wake sound load failed', exc_info=True)

    def _make_button(
        self,
        frame,
        title: str,
        action,
        *,
        fill_rgb,
        fill_alpha: float,
        glyph_rgb,
        rim_alpha: float = 0.0,
    ) -> NSButton:
        btn = NSButton.alloc().initWithFrame_(frame)
        btn.setTitle_(title)
        btn.setBordered_(False)
        btn.setWantsLayer_(True)

        # Round disc — solid white for finish, translucent ghost for cancel.
        layer = btn.layer()
        if layer:
            layer.setBackgroundColor_(
                CGColorCreateGenericRGB(
                    fill_rgb[0], fill_rgb[1], fill_rgb[2], fill_alpha,
                )
            )
            layer.setCornerRadius_(frame.size.width / 2.0)
            if rim_alpha > 0.0:
                # Hairline rim so the ghost disc reads against the pill.
                layer.setBorderWidth_(0.75)
                layer.setBorderColor_(
                    CGColorCreateGenericRGB(1.0, 1.0, 1.0, rim_alpha)
                )

        # Glyph via attributed title so we can recolour ✕ / ✓.
        from AppKit import NSAttributedString, NSForegroundColorAttributeName
        glyph_color = NSColor.colorWithCalibratedRed_green_blue_alpha_(
            glyph_rgb[0], glyph_rgb[1], glyph_rgb[2], 1.0,
        )
        font = NSFont.systemFontOfSize_(11.0)
        attrs = {
            NSForegroundColorAttributeName: glyph_color,
            'NSFont': font,
        }
        attr_title = NSAttributedString.alloc().initWithString_attributes_(title, attrs)
        btn.setAttributedTitle_(attr_title)

        target = self._make_action_target(action)
        if not hasattr(self, '_action_targets'):
            self._action_targets = []
        self._action_targets.append(target)

        btn.setTarget_(target)
        btn.setAction_(objc.selector(target.handleClick_, signature=b'v@:@'))

        return btn

    def _make_action_target(self, callback):
        target = _ActionTarget.alloc().init()
        target._callback = callback
        return target

    def _make_label(self, frame, text: str, font_size: float, color) -> NSView:
        from AppKit import NSTextField
        label = NSTextField.alloc().initWithFrame_(frame)
        label.setStringValue_(text)
        label.setBezeled_(False)
        label.setDrawsBackground_(False)
        label.setEditable_(False)
        label.setSelectable_(False)
        label.setFont_(NSFont.systemFontOfSize_(font_size))
        label.setTextColor_(color)
        label.setAlignment_(1)
        return label

    def _cancel_clicked(self, sender=None) -> None:
        if self._on_cancel:
            threading.Thread(target=self._on_cancel, daemon=True).start()

    def _finish_clicked(self, sender=None) -> None:
        if self._on_finish:
            threading.Thread(target=self._on_finish, daemon=True).start()

    # ── Public API ──────────────────────────────────────────────

    def show_idle_dot(self) -> None:
        self._reposition_for_active_screen()
        self._state = OverlayState.DORMANT
        if self._wave_view:
            self._wave_view.set_state(OverlayState.DORMANT)
        self._set_mode_text('')
        self._target_bloom = 0.0
        if self._window is not None:
            self._window.setIgnoresMouseEvents_(True)
            self._window.setHasShadow_(False)
        self._reveal_window()
        self._start_animation_timer()

    def show_recording(self, mode: str = 'ptt') -> None:
        self._reposition_for_active_screen()
        was_dormant = self._state in (OverlayState.DORMANT, OverlayState.HIDDEN)
        self._state = OverlayState.RECORDING
        if self._wave_view:
            self._wave_view.set_state(OverlayState.RECORDING)
        self._set_mode_text('')   # the waveform reads as "recording" on its own
        self._target_bloom = 1.0
        if self._window is not None:
            self._window.setIgnoresMouseEvents_(False)
            self._window.setHasShadow_(True)
        self._reveal_window()
        self._start_animation_timer()
        if was_dormant:
            self._play_wake_sound()

    def show_processing(self) -> None:
        self._state = OverlayState.PROCESSING
        if self._wave_view:
            self._wave_view.set_state(OverlayState.PROCESSING)
        self._set_mode_text('')
        self._target_bloom = 1.0
        if self._window is not None:
            self._window.setIgnoresMouseEvents_(False)
        self._start_animation_timer()

    def hide(self) -> None:
        self._state = OverlayState.HIDDEN
        self._target_bloom = 0.0
        self._stop_animation_timer()
        self._bloom = 0.0
        if self._wave_view:
            self._wave_view.set_bloom(0.0)
        self._apply_bloom(0.0)
        if self._window is not None:
            self._window.setAlphaValue_(0.0)
            self._window.orderOut_(None)

    def push_amplitude(self, amplitude: float) -> None:
        with self._lock:
            self._amplitude_buffer.append(max(0.0, min(1.0, amplitude)))

    @property
    def is_visible(self) -> bool:
        return self._state != OverlayState.HIDDEN

    # ── Internal animation ──────────────────────────────────────

    def _set_mode_text(self, text: str) -> None:
        if self._mode_label is None:
            return
        try:
            self._mode_label.setStringValue_(text)
        except Exception:
            pass

    def _reveal_window(self) -> None:
        if self._window is None:
            return
        self._window.orderFront_(None)
        self._window.setAlphaValue_(1.0)

    def _play_wake_sound(self) -> None:
        if self._wake_sound is None:
            return
        try:
            self._wake_sound.stop()
            self._wake_sound.play()
        except Exception:
            _log.debug('Wake sound play failed', exc_info=True)

    def _start_animation_timer(self) -> None:
        if self._timer is not None:
            return
        interval = 1.0 / ANIMATION_FPS
        self._timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            interval, self._make_timer_target(), b'tick:', None, True
        )

    def _stop_animation_timer(self) -> None:
        if self._timer is not None:
            self._timer.invalidate()
            self._timer = None

    def _make_timer_target(self):
        target = _TimerTarget.alloc().init()
        target._overlay = self
        self._timer_target = target
        return target

    def _step_bloom(self, dt: float) -> None:
        target = self._target_bloom
        delta = target - self._bloom
        if abs(delta) < 0.002:
            self._bloom = target
            return
        duration = TRANSITION_DURATION if delta > 0 else COLLAPSE_DURATION
        k = 1.0 - math.exp(-3.0 * dt / duration)
        self._bloom += delta * k

    def _apply_bloom(self, bloom: float) -> None:
        if self._wave_view is not None:
            self._wave_view.set_bloom(bloom)

        bg_opacity = max(0.0, (bloom - 0.05) / 0.95)
        if self._bg_view is not None:
            layer = self._bg_view.layer()
            if layer is not None:
                layer.setOpacity_(float(bg_opacity))

        btn_alpha = max(0.0, (bloom - 0.40) / 0.60)
        if self._cancel_btn is not None:
            self._cancel_btn.setAlphaValue_(float(btn_alpha))
        if self._finish_btn is not None:
            self._finish_btn.setAlphaValue_(float(btn_alpha))

        if self._mode_label is not None:
            self._mode_label.setAlphaValue_(0.0)

    def _tick(self) -> None:
        if self._wave_view is None or self._window is None:
            return

        dt = 1.0 / ANIMATION_FPS

        if self._state == OverlayState.RECORDING:
            with self._lock:
                amplitudes = list(self._amplitude_buffer)
            recent = amplitudes[-6:] if amplitudes else [0.0]
            self._wave_view.set_amplitude(max(recent))
        else:
            self._wave_view.set_amplitude(0.0)

        self._step_bloom(dt)
        self._apply_bloom(self._bloom)
        self._wave_view.tick(dt)


class _ActionTarget(NSView):
    _callback = None

    @objc.typedSelector(b'v@:@')
    def handleClick_(self, sender):
        if self._callback is not None:
            self._callback(sender)


class _TimerTarget(NSView):
    _overlay = None

    @objc.typedSelector(b'v@:@')
    def tick_(self, timer):
        if self._overlay is not None:
            self._overlay._tick()
