# overlay.py — Floating dictation overlay for Say.
#
# A two-state floating window at bottom-center of the screen:
#
#   DORMANT — collapsed to a small glowing dot when the app is idle
#             (mirrors Wispr Flow's "shrink to dot" idle behavior).
#   ACTIVE  — pill-shaped with a dotted halftone sphere that ripples
#             with mic amplitude, plus cancel (✕) / finish (✓) buttons.
#
# The dot blooms into the sphere with a fluid ease-out and a soft wake
# chime; on completion it collapses back to the dot.
#
# Uses PyObjC (AppKit/Cocoa) for native macOS rendering.

from __future__ import annotations

import math
import os
import threading
from collections import deque
from collections.abc import Callable
from enum import Enum

import numpy as np
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

import brand

_log = get_logger('overlay')

# ── Overlay sizing ──────────────────────────────────────────────
OVERLAY_WIDTH = 320
OVERLAY_HEIGHT = 120
CORNER_RADIUS = 22.0
BUTTON_SIZE = 28
ANIMATION_FPS = 30
AMPLITUDE_BUFFER_SIZE = 28

# ── Dormant dot mode ────────────────────────────────────────────
DORMANT_DOT_RADIUS = 5.5     # rendered dot radius when fully collapsed
DORMANT_BREATHE_RATE = 1.4   # rad/sec for the slow idle pulse
TRANSITION_DURATION = 0.32   # seconds — wake-up bloom time
COLLAPSE_DURATION = 0.45     # seconds — settling back to dot is slower

# ── Orb (dotted halftone sphere) ────────────────────────────────
SPHERE_DOT_COUNT = 520
SPHERE_RADIUS_PX = 44.0       # base radius at full bloom
SPHERE_AMP_GAIN = 0.16        # radial expansion at full amplitude
SPHERE_RIPPLE_GAIN = 0.10     # surface ripple amplitude
SPHERE_ROT_SPEED_Y = 0.014    # rad/frame around y-axis
SPHERE_WOBBLE_SPEED = 0.006   # rad/frame x-axis wobble
SPHERE_WOBBLE_RANGE = 0.20    # peak x-axis tilt (rad)

# Three-stop color gradient running top→bottom across the sphere.
ORB_COLOR_TOP = np.array([0.96, 0.42, 0.72])  # bright pink
ORB_COLOR_MID = np.array([0.70, 0.25, 0.82])  # magenta
ORB_COLOR_BOT = np.array([0.30, 0.18, 0.66])  # deep purple/indigo

# A short, soft built-in macOS chime. Tink/Pop both work; Tink is
# the gentlest. Falls back silently if the file is missing.
WAKE_SOUND_PATH = '/System/Library/Sounds/Tink.aiff'

# Window levels
NSFloatingWindowLevel = 5  # CGWindowLevelKey = kCGFloatingWindowLevelKey


class OverlayState(str, Enum):
    HIDDEN = 'hidden'        # off-screen entirely
    DORMANT = 'dormant'      # small idle dot
    RECORDING = 'recording'  # full pill, mic-driven orb
    PROCESSING = 'processing'  # full pill, idle-pulse orb


def _fibonacci_sphere(n: int) -> np.ndarray:
    """Evenly distribute `n` points on a unit sphere via Fibonacci spiral.

    Returns an (n, 3) float array of unit-sphere xyz coordinates. Even
    coverage matters here — clustered points show as visible streaks
    when the sphere rotates.
    """
    indices = np.arange(0, n, dtype=np.float64) + 0.5
    phi = np.arccos(1.0 - 2.0 * indices / n)
    theta = math.pi * (1.0 + 5.0 ** 0.5) * indices
    x = np.cos(theta) * np.sin(phi)
    y = np.sin(theta) * np.sin(phi)
    z = np.cos(phi)
    return np.column_stack([x, y, z])


def _ease_out_cubic(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1.0 - (1.0 - t) ** 3


class _OrbView(NSView):
    """Custom NSView that draws an animated dotted sphere whose size
    blends from a single glowing dot to a full sphere via `bloom`,
    and whose surface ripples with microphone amplitude."""

    def initWithFrame_(self, frame):
        self = objc.super(_OrbView, self).initWithFrame_(frame)
        if self is None:
            return None
        self._amplitude = 0.0
        self._target_amplitude = 0.0
        self._phase = 0.0
        self._wobble_phase = 0.0
        self._breathe_phase = 0.0
        self._bloom = 0.0   # 0 = dot, 1 = full sphere
        self._state = OverlayState.HIDDEN
        self._unit_sphere = _fibonacci_sphere(SPHERE_DOT_COUNT)
        return self

    def isFlipped(self):
        return False

    def set_amplitude(self, amplitude: float) -> None:
        """Set target amplitude (0.0–1.0). Actual value smooths toward it."""
        self._target_amplitude = max(0.0, min(1.0, amplitude))

    def set_state(self, state: OverlayState) -> None:
        self._state = state
        self.setNeedsDisplay_(True)

    def set_bloom(self, bloom: float) -> None:
        """Set the dot↔sphere blend. 0 = pure dot, 1 = full sphere."""
        self._bloom = max(0.0, min(1.0, bloom))
        self.setNeedsDisplay_(True)

    def tick(self, dt: float = 1.0 / ANIMATION_FPS) -> None:
        """Advance animation by one frame."""
        self._phase += SPHERE_ROT_SPEED_Y
        self._wobble_phase += SPHERE_WOBBLE_SPEED
        self._breathe_phase += DORMANT_BREATHE_RATE * dt
        # Smooth amplitude via exponential moving average.
        self._amplitude += (self._target_amplitude - self._amplitude) * 0.20
        self.setNeedsDisplay_(True)

    def drawRect_(self, rect):
        bounds = self.bounds()
        cx = bounds.size.width / 2.0
        cy = bounds.size.height / 2.0

        bloom = self._bloom

        if self._state == OverlayState.RECORDING:
            amp = self._amplitude
        elif self._state == OverlayState.PROCESSING:
            amp = 0.18 + 0.10 * math.sin(self._phase * 2.5)
        else:
            # DORMANT / HIDDEN — slow ambient breathe so the dot feels alive.
            amp = 0.10 + 0.07 * math.sin(self._breathe_phase)

        # Radius blends from dormant dot up to full sphere.
        eased_bloom = _ease_out_cubic(bloom)
        effective_radius = (
            DORMANT_DOT_RADIUS
            + (SPHERE_RADIUS_PX - DORMANT_DOT_RADIUS) * eased_bloom
        )

        # Rotate around Y, then a small wobble around X for life.
        cos_y = math.cos(self._phase)
        sin_y = math.sin(self._phase)
        wobble = SPHERE_WOBBLE_RANGE * math.sin(self._wobble_phase)
        cos_x = math.cos(wobble)
        sin_x = math.sin(wobble)

        unit = self._unit_sphere
        ux = unit[:, 0]
        uy = unit[:, 1]
        uz = unit[:, 2]

        x1 = ux * cos_y + uz * sin_y
        y1 = uy
        z1 = -ux * sin_y + uz * cos_y

        x2 = x1
        y2 = y1 * cos_x - z1 * sin_x
        z2 = y1 * sin_x + z1 * cos_x

        # Surface ripple — only meaningful once bloomed, scaled by bloom.
        ripple = (
            SPHERE_RIPPLE_GAIN * amp * eased_bloom
            * np.sin(y2 * 6.0 + self._phase * 4.0)
        )
        radius = effective_radius * (1.0 + SPHERE_AMP_GAIN * amp + ripple)

        screen_x = cx + x2 * radius
        screen_y = cy + y2 * radius

        # Vertical color gradient driven by rotated y.
        gradient_t = (y2 + 1.0) * 0.5  # 0 bottom → 1 top
        t1 = np.clip(gradient_t * 2.0, 0.0, 1.0)
        t2 = np.clip((gradient_t - 0.5) * 2.0, 0.0, 1.0)
        below = gradient_t < 0.5

        r_lower = ORB_COLOR_BOT[0] + (ORB_COLOR_MID[0] - ORB_COLOR_BOT[0]) * t1
        g_lower = ORB_COLOR_BOT[1] + (ORB_COLOR_MID[1] - ORB_COLOR_BOT[1]) * t1
        b_lower = ORB_COLOR_BOT[2] + (ORB_COLOR_MID[2] - ORB_COLOR_BOT[2]) * t1
        r_upper = ORB_COLOR_MID[0] + (ORB_COLOR_TOP[0] - ORB_COLOR_MID[0]) * t2
        g_upper = ORB_COLOR_MID[1] + (ORB_COLOR_TOP[1] - ORB_COLOR_MID[1]) * t2
        b_upper = ORB_COLOR_MID[2] + (ORB_COLOR_TOP[2] - ORB_COLOR_MID[2]) * t2
        r_col = np.where(below, r_lower, r_upper)
        g_col = np.where(below, g_lower, g_upper)
        b_col = np.where(below, b_lower, b_upper)

        # Depth shading.
        depth = (z2 + 1.0) * 0.5
        dot_radius = 0.45 + 1.05 * depth
        alpha = 0.10 + 0.78 * depth
        alpha += 0.18 * amp * depth  # mic boost on the lit hemisphere
        # Cluster fully fades out at bloom=0 so the dormant accent dot
        # below isn't drowned by 520 stacked points.
        alpha = alpha * eased_bloom
        np.clip(alpha, 0.0, 1.0, out=alpha)

        # Dormant accent dot — a single soft gradient dot that's visible
        # when collapsed and crossfades out as we bloom into the sphere.
        dormant_alpha = 1.0 - eased_bloom
        if dormant_alpha > 0.01:
            self._draw_accent_dot(cx, cy, amp, dormant_alpha)

        if eased_bloom < 0.02:
            return  # nothing more to draw — pure dormant mode

        order = np.argsort(z2)

        for i in order:
            if z2[i] < -0.15:
                # Cull deep-back dots — invisible behind the front face.
                continue
            sx = float(screen_x[i])
            sy = float(screen_y[i])
            r = float(dot_radius[i])
            color = NSColor.colorWithCalibratedRed_green_blue_alpha_(
                float(r_col[i]),
                float(g_col[i]),
                float(b_col[i]),
                float(alpha[i]),
            )
            color.setFill()
            dot_rect = NSMakeRect(sx - r, sy - r, r * 2.0, r * 2.0)
            NSBezierPath.bezierPathWithOvalInRect_(dot_rect).fill()

    def _draw_accent_dot(self, cx: float, cy: float, amp: float, alpha: float) -> None:
        """Single gradient dot used in dormant mode (and crossfading
        out as the sphere blooms in). Halo + bright pink core."""
        # Slight pulse with the breathe phase / mic amplitude.
        radius = DORMANT_DOT_RADIUS * (1.0 + 0.20 * amp)

        halo_r = radius * 2.0
        halo_color = NSColor.colorWithCalibratedRed_green_blue_alpha_(
            float(ORB_COLOR_TOP[0]),
            float(ORB_COLOR_TOP[1]),
            float(ORB_COLOR_TOP[2]),
            0.30 * alpha,
        )
        halo_color.setFill()
        NSBezierPath.bezierPathWithOvalInRect_(
            NSMakeRect(cx - halo_r, cy - halo_r, halo_r * 2.0, halo_r * 2.0)
        ).fill()

        core_color = NSColor.colorWithCalibratedRed_green_blue_alpha_(
            0.96, 0.58, 0.80, alpha,
        )
        core_color.setFill()
        NSBezierPath.bezierPathWithOvalInRect_(
            NSMakeRect(cx - radius, cy - radius, radius * 2.0, radius * 2.0)
        ).fill()


class DictationOverlay:
    """Floating macOS overlay window for dictation feedback.

    Lives at bottom-center as a small dot when idle and blooms into a
    full pill with the dotted-sphere visualization while recording.
    """

    def __init__(
        self,
        on_cancel: Callable[[], None] | None = None,
        on_finish: Callable[[], None] | None = None,
    ):
        self._on_cancel = on_cancel
        self._on_finish = on_finish
        self._window = None
        self._orb_view = None
        self._bg_view = None
        self._cancel_btn = None
        self._finish_btn = None
        self._mode_label = None
        self._timer = None
        self._state = OverlayState.HIDDEN
        # Bloom drives the visual transition: 0 = dot, 1 = full pill.
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
        """Return the NSScreen the user is currently looking at.

        We use the cursor position as a proxy for "active display" — a
        polished alternative would be to ask the frontmost app for its
        key-window screen, but that requires an entitlement on macOS 14+.
        Falls back to mainScreen() if no screen contains the cursor.
        """
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
        y = screen_frame.origin.y + 40  # 40px from bottom
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
        """Create the overlay window and its subviews."""
        screen = self._active_screen()
        if screen is None:
            _log.error('No screen found for overlay.')
            return

        frame = self._frame_for_screen(screen)

        self._window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            frame,
            NSWindowStyleMaskBorderless,
            2,  # NSBackingStoreBuffered
            False,
        )
        self._window.setLevel_(NSFloatingWindowLevel + 10)
        self._window.setOpaque_(False)
        self._window.setBackgroundColor_(NSColor.clearColor())
        self._window.setHasShadow_(False)
        # Mouse events flip on/off based on mode. Start ignored — when
        # we're hidden no clicks should land here.
        self._window.setIgnoresMouseEvents_(True)
        self._window.setCollectionBehavior_(
            (1 << 0) | (1 << 4)  # canJoinAllSpaces | fullScreenAuxiliary
        )
        self._window.setAlphaValue_(0.0)

        content = self._window.contentView()

        # Background view with rounded corners — fades in as we bloom.
        bg_view = NSView.alloc().initWithFrame_(
            NSMakeRect(0, 0, OVERLAY_WIDTH, OVERLAY_HEIGHT),
        )
        content.addSubview_(bg_view)
        bg_view.setWantsLayer_(True)
        layer = bg_view.layer()
        if layer:
            bg_r, bg_g, bg_b = brand.OVERLAY_BG
            br_r, br_g, br_b = brand.OVERLAY_BORDER
            layer.setBackgroundColor_(
                CGColorCreateGenericRGB(bg_r, bg_g, bg_b, 0.93)
            )
            layer.setCornerRadius_(CORNER_RADIUS)
            layer.setBorderWidth_(0.5)
            layer.setBorderColor_(
                CGColorCreateGenericRGB(br_r, br_g, br_b, 0.45)
            )
            layer.setOpacity_(0.0)
        self._bg_view = bg_view

        # Orb view — fills the central column between the two buttons.
        side_pad = BUTTON_SIZE + 18
        orb_x = side_pad
        orb_w = OVERLAY_WIDTH - 2 * side_pad
        orb_y = 14
        orb_h = OVERLAY_HEIGHT - orb_y - 18
        self._orb_view = _OrbView.alloc().initWithFrame_(
            NSMakeRect(orb_x, orb_y, orb_w, orb_h),
        )
        content.addSubview_(self._orb_view)

        # Cancel button (vertically centered, left edge)
        self._cancel_btn = self._make_button(
            NSMakeRect(
                10,
                (OVERLAY_HEIGHT - BUTTON_SIZE) / 2,
                BUTTON_SIZE,
                BUTTON_SIZE,
            ),
            title='✕',
            action=self._cancel_clicked,
            bg_color=brand.color(brand.OVERLAY_RED, alpha=0.72),
        )
        content.addSubview_(self._cancel_btn)

        # Finish button (vertically centered, right edge)
        self._finish_btn = self._make_button(
            NSMakeRect(
                OVERLAY_WIDTH - BUTTON_SIZE - 10,
                (OVERLAY_HEIGHT - BUTTON_SIZE) / 2,
                BUTTON_SIZE,
                BUTTON_SIZE,
            ),
            title='✓',
            action=self._finish_clicked,
            bg_color=brand.color(brand.OVERLAY_GREEN, alpha=0.72),
        )
        content.addSubview_(self._finish_btn)

        # Mode indicator label (bottom strip under the orb)
        self._mode_label = self._make_label(
            NSMakeRect(orb_x, 2, orb_w, 12),
            text='',
            font_size=9.0,
            color=brand.color(brand.OVERLAY_TEXT, alpha=0.70),
        )
        content.addSubview_(self._mode_label)

        # Buttons + label fade in once bloomed past a threshold.
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
                sound.setVolume_(0.45)
                self._wake_sound = sound
        except Exception:
            _log.debug('Wake sound load failed', exc_info=True)

    def _make_button(self, frame, title: str, action, bg_color) -> NSButton:
        btn = NSButton.alloc().initWithFrame_(frame)
        btn.setTitle_(title)
        btn.setBordered_(False)
        btn.setWantsLayer_(True)

        layer = btn.layer()
        if layer:
            layer.setBackgroundColor_(
                CGColorCreateGenericRGB(
                    bg_color.redComponent(),
                    bg_color.greenComponent(),
                    bg_color.blueComponent(),
                    bg_color.alphaComponent(),
                )
            )
            layer.setCornerRadius_(frame.size.width / 2.0)

        btn.setFont_(NSFont.systemFontOfSize_(14.0))

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
        label.setAlignment_(1)  # NSCenterTextAlignment
        return label

    def _cancel_clicked(self, sender=None) -> None:
        if self._on_cancel:
            threading.Thread(target=self._on_cancel, daemon=True).start()

    def _finish_clicked(self, sender=None) -> None:
        if self._on_finish:
            threading.Thread(target=self._on_finish, daemon=True).start()

    # ── Public API ──────────────────────────────────────────────

    def show_idle_dot(self) -> None:
        """Sit at bottom-center as a small glowing dot."""
        self._reposition_for_active_screen()
        self._state = OverlayState.DORMANT
        if self._orb_view:
            self._orb_view.set_state(OverlayState.DORMANT)
        self._set_mode_text('')
        self._target_bloom = 0.0
        # Window stays interactive only when bloomed (so the dot doesn't
        # eat clicks across a 320px-wide invisible region).
        if self._window is not None:
            self._window.setIgnoresMouseEvents_(True)
            self._window.setHasShadow_(False)
        self._reveal_window()
        self._start_animation_timer()

    def show_recording(self, mode: str = 'ptt') -> None:
        """Bloom into the full pill and start mic-driven orb rendering."""
        self._reposition_for_active_screen()
        was_dormant = self._state in (OverlayState.DORMANT, OverlayState.HIDDEN)
        self._state = OverlayState.RECORDING
        if self._orb_view:
            self._orb_view.set_state(OverlayState.RECORDING)

        mode_text = (
            '🎙 Hold to talk' if mode == 'ptt'
            else '🎙 Hands-free — press Fn to finish'
        )
        self._set_mode_text(mode_text)

        self._target_bloom = 1.0
        if self._window is not None:
            self._window.setIgnoresMouseEvents_(False)
            self._window.setHasShadow_(True)

        self._reveal_window()
        self._start_animation_timer()
        if was_dormant:
            self._play_wake_sound()

    def show_processing(self) -> None:
        """Stay bloomed; switch the orb to its idle-pulse animation."""
        self._state = OverlayState.PROCESSING
        if self._orb_view:
            self._orb_view.set_state(OverlayState.PROCESSING)
        self._set_mode_text('⏳ Processing…')
        self._target_bloom = 1.0
        if self._window is not None:
            self._window.setIgnoresMouseEvents_(False)
        self._start_animation_timer()

    def hide(self) -> None:
        """Fully hide the overlay (e.g. screen lock or fatal error)."""
        self._state = OverlayState.HIDDEN
        self._target_bloom = 0.0
        self._stop_animation_timer()
        self._bloom = 0.0
        if self._orb_view:
            self._orb_view.set_bloom(0.0)
        self._apply_bloom(0.0)
        if self._window is not None:
            self._window.setAlphaValue_(0.0)
            self._window.orderOut_(None)

    def push_amplitude(self, amplitude: float) -> None:
        """Push a new amplitude value (called from audio callback thread)."""
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
            # Stop+play resets so rapid wake events don't queue.
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
        self._timer_target = target  # Prevent garbage collection
        return target

    def _step_bloom(self, dt: float) -> None:
        """Move bloom toward its target with framerate-aware easing."""
        target = self._target_bloom
        delta = target - self._bloom
        if abs(delta) < 0.002:
            self._bloom = target
            return
        # Choose a duration based on direction so the collapse feels less
        # snappy than the wake-up.
        duration = TRANSITION_DURATION if delta > 0 else COLLAPSE_DURATION
        # Exponential approach: per-frame factor that crosses ~95% over
        # `duration` seconds.
        k = 1.0 - math.exp(-3.0 * dt / duration)
        self._bloom += delta * k

    def _apply_bloom(self, bloom: float) -> None:
        """Push the current bloom value into all view properties."""
        if self._orb_view is not None:
            self._orb_view.set_bloom(bloom)

        # Background pill fades in once we're past 5% to avoid a flash
        # when we're nearly fully collapsed.
        bg_opacity = max(0.0, (bloom - 0.05) / 0.95)
        if self._bg_view is not None:
            layer = self._bg_view.layer()
            if layer is not None:
                layer.setOpacity_(float(bg_opacity))

        # Buttons + label come in late so they don't crowd the bloom.
        btn_alpha = max(0.0, (bloom - 0.45) / 0.55)
        if self._cancel_btn is not None:
            self._cancel_btn.setAlphaValue_(float(btn_alpha))
        if self._finish_btn is not None:
            self._finish_btn.setAlphaValue_(float(btn_alpha))

        label_alpha = max(0.0, (bloom - 0.65) / 0.35)
        if self._mode_label is not None:
            self._mode_label.setAlphaValue_(float(label_alpha))

    def _tick(self) -> None:
        """Driven by NSTimer at ANIMATION_FPS — advance bloom + orb."""
        if self._orb_view is None or self._window is None:
            return

        dt = 1.0 / ANIMATION_FPS

        # Feed mic amplitude into the orb during recording.
        if self._state == OverlayState.RECORDING:
            with self._lock:
                amplitudes = list(self._amplitude_buffer)
            recent = amplitudes[-6:] if amplitudes else [0.0]
            self._orb_view.set_amplitude(max(recent))

        self._step_bloom(dt)
        self._apply_bloom(self._bloom)
        self._orb_view.tick(dt)


class _ActionTarget(NSView):
    """Tiny Objective-C class that bridges NSButton target-action to Python."""

    _callback = None

    @objc.typedSelector(b'v@:@')
    def handleClick_(self, sender):
        if self._callback is not None:
            self._callback(sender)


class _TimerTarget(NSView):
    """Bridges NSTimer target-action to the overlay's tick method."""

    _overlay = None

    @objc.typedSelector(b'v@:@')
    def tick_(self, timer):
        if self._overlay is not None:
            self._overlay._tick()
