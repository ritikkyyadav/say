"""The floating dictation glyph.

A 90×19 pt mark that appears at the bottom of the screen the instant the
trigger key goes down: one continuous base with twelve frequency columns
standing on it, redrawn every display frame from live microphone audio.

Three decisions carry the whole thing:

* **The window is built once, at launch.** Showing it later is an
  `orderFrontRegardless()` and nothing else. Creating an NSWindow, a layer tree
  and a CGColor on the keypress is exactly how an overlay ends up feeling a
  frame or two late no matter how fast the animation is.

* **It is one path, not twelve layers.** `waveform.silhouette()` returns a
  single closed polygon for the whole mark, so the columns share edges, can
  never disagree about a sub-pixel boundary, and read as one object standing on
  one base rather than as a row of separate meters.

* **The clock is the display, not a timer.** A `CADisplayLink` fires in step
  with the panel's actual screen — 60 Hz, 120 Hz, or whatever it changes to when
  the window moves between displays — and every value is integrated against the
  measured dt, so a late frame changes nothing about where the motion is.

The panel is a non-activating borderless `NSPanel` that ignores mouse events:
it can never take focus, which matters because focus is where the transcript is
about to be typed.
"""

from __future__ import annotations

import time
from typing import Any

import config
import objc
import overlay_theme
from AppKit import (
    NSApp,
    NSAppearanceNameAqua,
    NSAppearanceNameDarkAqua,
    NSBackingStoreBuffered,
    NSColor,
    NSEvent,
    NSMakeRect,
    NSPanel,
    NSScreen,
    NSStatusWindowLevel,
    NSThread,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowCollectionBehaviorIgnoresCycle,
    NSWindowCollectionBehaviorStationary,
    NSWindowStyleMaskBorderless,
    NSWindowStyleMaskNonactivatingPanel,
)
from Foundation import (
    NSNull,
    NSObject,
    NSRunLoop,
    NSRunLoopCommonModes,
    NSTimer,
)
from logging_setup import get_logger
from PyObjCTools import AppHelper
from Quartz import (
    CALayer,
    CAShapeLayer,
    CATransaction,
    CGColorCreateGenericRGB,
    CGPathAddLineToPoint,
    CGPathCloseSubpath,
    CGPathCreateMutable,
    CGPathMoveToPoint,
    CGSizeMake,
)
from waveform import BandAnalyzer, BarMotion, GlyphGeometry, Spring, silhouette

_log = get_logger('overlay')

# ── Placement ───────────────────────────────────────────────────
# The window is larger than the mark so the entry animation and the shadow have
# somewhere to live. Everything outside the glyph is fully transparent and
# ignores the mouse.
WINDOW_PAD_X = 13.0
WINDOW_PAD_Y = 8.5
BOTTOM_MARGIN = 26.0          # above the Dock / screen edge

# ── Entry and exit ──────────────────────────────────────────────
# Fast enough that the mark is simply *there* on the keypress, slow enough that
# it is a movement and not a pop. Opacity leads the width so the shape is
# already legible while it is still opening.
REVEAL_OMEGA = 44.0
REVEAL_ZETA = 1.0
DISMISS_OMEGA = 34.0
OPACITY_LEAD = 1.6
HIDE_BELOW = 0.02

# ── Palette ─────────────────────────────────────────────────────
# The ink follows the system appearance, but the system appearance is a poor
# proxy for what is actually *behind* the glyph: people run macOS dark and read
# white web pages all day, and then near-white ink lands on a white page and
# disappears. A soft shadow does not save it — at 0.22 alpha under near-white
# ink on white, there is nothing to see.
#
# So the mark is drawn twice: the silhouette in ink, and beneath it the same
# silhouette fattened by RIM_PT in the opposite tone. This is the subtitle
# trick — white glyph, dark outline — and it holds on light, dark, mid-grey and
# photographic backdrops alike, because one of the two tones always has
# contrast. It needs no screen sampling, and therefore no Screen Recording
# grant, which this app has no business asking for.
INK_LIGHT = (0.063, 0.071, 0.094)     # brand INK
INK_DARK = (0.965, 0.969, 0.980)
RIM_PT = 0.75                         # visible rim width outside the silhouette
                                      # 0.5 is a hairline, 1.5 reads as an outline
SHADOW_ALPHA_LIGHT = 0.22
SHADOW_ALPHA_DARK = 0.55
SHADOW_RADIUS = 3.0
SHADOW_OFFSET = (0.0, -1.0)

_COLLECTION_BEHAVIOR = (
    NSWindowCollectionBehaviorCanJoinAllSpaces
    | NSWindowCollectionBehaviorStationary
    | NSWindowCollectionBehaviorIgnoresCycle
    | NSWindowCollectionBehaviorFullScreenAuxiliary
)


def contrast_scheme(
    mode: str, system_is_dark: bool
) -> tuple[tuple[float, float, float], tuple[float, float, float], bool]:
    """Return `(ink, rim, rimmed)` for a contrast mode and the current theme.

    'dark' and 'light' name the *ink*, not the theme — someone who picks
    'dark' wants a dark mark, which is the opposite of what dark mode gives.
    """
    mode = (mode or 'auto').lower()
    if mode == 'dark':
        dark_ink = False
    elif mode == 'light':
        dark_ink = True
    else:
        dark_ink = bool(system_is_dark)

    ink = INK_DARK if dark_ink else INK_LIGHT
    rim = INK_LIGHT if dark_ink else INK_DARK
    return ink, rim, mode != 'system'


class _GlyphPanel(NSPanel):
    """Borderless, non-activating, never key.

    Overridden rather than merely configured: a panel that can become key would
    move focus off whatever app is about to receive the transcript.
    """

    def canBecomeKeyWindow(self) -> bool:
        return False

    def canBecomeMainWindow(self) -> bool:
        return False


class _Ticker(NSObject):
    """Selector target for the display link (PyObjC needs a real NSObject)."""

    def initWithOwner_(self, owner):
        self = objc.super(_Ticker, self).init()
        if self is None:
            return None
        self._owner = owner
        return self

    def step_(self, _sender) -> None:
        owner = getattr(self, '_owner', None)
        if owner is not None:
            owner._frame()


class DictationOverlay:
    """The glyph's lifecycle. Every public method is safe from any thread.

    `arm()` / `listen()` / `process()` / `dismiss()` are called from the hotkey
    tap, the controller's status callback and the audio thread — all of which
    are somewhere other than the main thread — so each one hops to the main
    thread itself instead of making four callers remember to.
    """

    def __init__(self, geometry: GlyphGeometry | None = None) -> None:
        self._geometry = geometry or GlyphGeometry()
        self._analyzer = BandAnalyzer(bands=self._geometry.bands)
        self._motion = BarMotion(bands=self._geometry.bands)
        self._reveal = Spring(0.0, omega=REVEAL_OMEGA, zeta=REVEAL_ZETA)
        self._window_w = self._geometry.width + 2 * WINDOW_PAD_X
        self._window_h = self._geometry.height + 2 * WINDOW_PAD_Y

        # PyObjC objects: `Any` because their real types only exist at runtime.
        self._panel: Any = None
        self._shape: Any = None
        self._rim: Any = None
        # Multi-colour painting. `_paint` holds one solid layer per column and
        # is clipped by `_mask`, which carries the *same* silhouette path the
        # single-colour `_shape` layer uses. The mark is therefore still one
        # path — only the fill varies across it — so adjacent columns cannot
        # disagree about a sub-pixel edge. Exactly one of `_shape` / `_paint`
        # is ever visible.
        self._paint: Any = None
        self._mask: Any = None
        self._columns: list[Any] = []
        # Resolved CGColors for the active theme, plus the index each column
        # currently shows. The pattern is evaluated every frame, so the cache
        # is what keeps that to a handful of property writes instead of twelve
        # CGColor allocations sixty times a second.
        self._palette: list[Any] = []
        self._col_idx: list[int] = []
        self._pattern = overlay_theme.DEFAULT_PATTERN
        self._themed = False
        self._preview_timer: Any = None
        self._ticker: Any = None
        self._link: Any = None
        self._timer: Any = None
        self._snap = 0.5
        self._visible = False
        self._recording = False
        self._motion_state = BarMotion.LISTEN
        self._last_frame_at = 0.0

    # ── lifecycle ───────────────────────────────────────────────

    def prewarm(self) -> None:
        """Build the window now so showing it later costs nothing. Main thread."""
        self._on_main(self._build)

    def arm(self) -> None:
        """Trigger key went down. Show the resting mark immediately."""
        self._on_main(self._show, BarMotion.LISTEN, True)

    def listen(self, mode: str = 'ptt') -> None:
        """Recording started. Visually identical to `arm()` on purpose: there is
        no discontinuity at the moment the gesture resolves into a recording."""
        self._on_main(self._mark_recording, True)
        self._on_main(self._show, BarMotion.LISTEN, False)

    def process(self) -> None:
        """Recording ended, the model is decoding — sweep instead of spectrum."""
        self._on_main(self._show, BarMotion.WORK, False)

    def dismiss(self) -> None:
        """Retract the mark. Safe whatever state it is in."""
        self._on_main(self._hide)

    def release_arm(self) -> None:
        """Retract only if the gesture never became a recording."""
        self._on_main(self._hide_if_armed)

    def set_theme(self) -> None:
        """Re-read `OVERLAY_THEME` and repaint. Cheap, and safe at any time."""
        self._on_main(self._apply_appearance)

    def preview(self, seconds: float = 2.6) -> None:
        """Show the glyph for a moment so a colour can be judged for real.

        Refused while a dictation is live: the mark belongs to the recording
        then, and taking it away on a preview timer would pull feedback out
        from under someone mid-sentence.
        """
        self._on_main(self._preview, float(seconds))

    def close(self) -> None:
        self._on_main(self._teardown)

    # ── audio thread ────────────────────────────────────────────

    def feed_audio(self, block) -> None:
        """One microphone block, straight off the sounddevice callback.

        Runs on the audio thread and must never raise or block: it does a single
        rFFT and one attribute assignment, and nothing else in this class is
        allowed to reach back into it.
        """
        if not self._visible:
            return
        try:
            self._analyzer.feed(block)
        except Exception:
            pass

    @property
    def is_visible(self) -> bool:
        return self._visible

    # ── main-thread implementation ──────────────────────────────

    @staticmethod
    def _on_main(fn, *args) -> None:
        try:
            if NSThread.isMainThread():
                fn(*args)
            else:
                AppHelper.callAfter(fn, *args)
        except Exception:
            _log.exception('Overlay call could not be dispatched')

    def _build(self) -> None:
        if self._panel is not None:
            return
        try:
            frame = NSMakeRect(0, 0, self._window_w, self._window_h)
            panel = _GlyphPanel.alloc().initWithContentRect_styleMask_backing_defer_(
                frame,
                NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel,
                NSBackingStoreBuffered,
                False,
            )
            panel.setOpaque_(False)
            panel.setBackgroundColor_(NSColor.clearColor())
            panel.setHasShadow_(False)          # the glyph carries its own
            panel.setIgnoresMouseEvents_(True)
            panel.setHidesOnDeactivate_(False)
            panel.setFloatingPanel_(True)
            panel.setCollectionBehavior_(_COLLECTION_BEHAVIOR)
            panel.setAlphaValue_(1.0)           # the layer's opacity does the fade

            # LAST, and it must stay last. `setFloatingPanel_` is not just a
            # behaviour flag — AppKit implements it by *assigning the window
            # level*, so it silently overwrites whatever was set before it.
            # This call used to sit six lines higher, which meant the glyph
            # asked for NSStatusWindowLevel (25) and spent its whole life at
            # NSFloatingWindowLevel (3): still above ordinary windows, so it
            # looked correct in casual use, but low enough to be buried by
            # full-screen apps and by other apps' floating panels — i.e. it
            # vanished exactly where dictation actually happens.
            panel.setLevel_(NSStatusWindowLevel)
            if panel.level() != NSStatusWindowLevel:
                # Never observed, but this is precisely the failure that hid
                # for so long by being invisible, so it says so out loud
                # rather than degrading quietly.
                _log.error(
                    'Overlay window level is %s, expected %s — the glyph will '
                    'be occluded by full-screen apps',
                    panel.level(), NSStatusWindowLevel,
                )

            content = panel.contentView()
            content.setWantsLayer_(True)
            root = content.layer()
            root.setMasksToBounds_(False)

            # Beneath the ink: the same path, fattened. Stroke straddles the
            # path, so lineWidth is doubled to leave RIM_PT showing outside;
            # the interior is filled too, which the ink layer then covers
            # exactly, so there is never a seam between them.
            rim = CAShapeLayer.layer()
            rim.setFrame_(NSMakeRect(0, 0, self._window_w, self._window_h))
            rim.setMasksToBounds_(False)
            rim.setOpacity_(0.0)
            rim.setLineJoin_('miter')   # the silhouette's corners are square
            rim.setShadowRadius_(SHADOW_RADIUS)
            rim.setShadowOffset_(CGSizeMake(*SHADOW_OFFSET))
            rim.setShadowColor_(CGColorCreateGenericRGB(0.0, 0.0, 0.0, 1.0))

            shape = CAShapeLayer.layer()
            shape.setFrame_(NSMakeRect(0, 0, self._window_w, self._window_h))
            shape.setMasksToBounds_(False)
            shape.setOpacity_(0.0)
            shape.setShadowRadius_(SHADOW_RADIUS)
            shape.setShadowOffset_(CGSizeMake(*SHADOW_OFFSET))
            shape.setShadowColor_(CGColorCreateGenericRGB(0.0, 0.0, 0.0, 1.0))
            # Implicit animation is the enemy here: Core Animation would happily
            # interpolate every path we set and fight the physics that produced
            # it. Disabled per-key as well as per-transaction.
            null = NSNull.null()
            shape.setActions_({
                'path': null,
                'shadowPath': null,
                'opacity': null,
                'position': null,
                'bounds': null,
                'fillColor': null,
            })
            null_actions = {
                'path': null,
                'shadowPath': null,
                'opacity': null,
                'position': null,
                'bounds': null,
                'fillColor': null,
                'strokeColor': null,
                'lineWidth': null,
            }
            rim.setActions_(null_actions)

            paint = CALayer.layer()
            paint.setFrame_(NSMakeRect(0, 0, self._window_w, self._window_h))
            paint.setMasksToBounds_(False)
            paint.setOpacity_(0.0)
            paint.setHidden_(True)
            paint.setActions_(null_actions)

            mask = CAShapeLayer.layer()
            mask.setFrame_(NSMakeRect(0, 0, self._window_w, self._window_h))
            mask.setFillColor_(CGColorCreateGenericRGB(0.0, 0.0, 0.0, 1.0))
            mask.setActions_(null_actions)
            paint.setMask_(mask)

            # One opaque column per band, spanning the full window height. The
            # mask trims each of them to the silhouette, so a column's colour
            # runs all the way down through the base and the bars read as
            # slashes rather than as separate meters sitting on a slab.
            geo = self._geometry
            origin_x = (self._window_w - geo.width) / 2.0
            self._columns = []
            for i in range(geo.bands):
                col = CALayer.layer()
                col.setFrame_(NSMakeRect(
                    origin_x + i * geo.column_width, 0.0,
                    geo.column_width, self._window_h,
                ))
                col.setActions_(null_actions)
                paint.addSublayer_(col)
                self._columns.append(col)

            root.addSublayer_(rim)       # beneath
            root.addSublayer_(paint)     # multi-colour fill
            root.addSublayer_(shape)     # single-colour fill, on top

            self._panel = panel
            self._rim = rim
            self._shape = shape
            self._paint = paint
            self._mask = mask
            self._ticker = _Ticker.alloc().initWithOwner_(self)
            self._apply_appearance()
            self._apply(self._motion.heights, 0.0)

            # Force the window server to allocate the backing surface now
            # rather than on the first keypress.
            panel.orderFrontRegardless()
            panel.orderOut_(None)
            _log.info('Overlay ready (%.0f×%.0f pt glyph)',
                      self._geometry.width, self._geometry.height)
        except Exception:
            _log.exception('Overlay could not be built; dictation is unaffected')
            self._panel = None
            self._shape = None
            self._rim = None
            self._paint = None
            self._mask = None
            self._columns = []

    def _system_is_dark(self) -> bool:
        try:
            app = NSApp()
            appearance = app.effectiveAppearance() if app is not None else None
            if appearance is None:
                return False
            match = appearance.bestMatchFromAppearancesWithNames_(
                [NSAppearanceNameAqua, NSAppearanceNameDarkAqua]
            )
            return bool(match == NSAppearanceNameDarkAqua)
        except Exception:
            return False

    def _apply_appearance(self) -> None:
        """Pick the ink, and the rim that keeps it legible on any backdrop.

        `OVERLAY_CONTRAST` decides:
          'auto'   ink follows the system, plus the contrast rim (default)
          'system' ink follows the system, no rim — the pre-rim behaviour
          'dark'   dark ink, light rim, regardless of the system
          'light'  light ink, dark rim, regardless of the system
        """
        if self._shape is None:
            return

        # A theme with colours wins outright: OVERLAY_CONTRAST adapts the glyph
        # to the system, and someone who has picked red-on-black has said they
        # do not want it adapted. The default 'ink' theme resolves to None and
        # falls through to exactly the behaviour that shipped before themes.
        try:
            resolved = overlay_theme.resolve(
                str(getattr(config, 'OVERLAY_THEME', 'ink')),
                getattr(config, 'OVERLAY_CUSTOM_COLORS', None),
            )
        except Exception:
            _log.debug('Overlay theme could not be resolved; using the system ink',
                       exc_info=True)
            resolved = None

        self._themed = resolved is not None
        if resolved is not None:
            colors, rim_rgb = resolved
            self._pattern = str(
                getattr(config, 'OVERLAY_PATTERN', overlay_theme.DEFAULT_PATTERN)
            ).lower()
            self._palette = [
                CGColorCreateGenericRGB(r, g, b, 1.0) for (r, g, b) in colors
            ]
            # Force the next frame to assign every column, whatever it was
            # showing before the theme changed.
            self._col_idx = [-1] * len(self._columns)
            self._paint_columns(self._motion.heights)
            if self._paint is not None:
                self._paint.setHidden_(False)
            self._shape.setHidden_(True)
            self._shape.setShadowOpacity_(0.0)
            if self._rim is not None:
                rim_color = CGColorCreateGenericRGB(*rim_rgb, 1.0)
                self._rim.setFillColor_(rim_color)
                self._rim.setStrokeColor_(rim_color)
                # Not optional here: a two-tone mark has a colour that will
                # vanish against *some* backdrop, so the rim is the only thing
                # guaranteeing the shape stays readable.
                self._rim.setLineWidth_(2.0 * RIM_PT)
                self._rim.setHidden_(False)
                self._rim.setShadowOpacity_(
                    SHADOW_ALPHA_DARK if rim_rgb is overlay_theme.TONE_LIGHT
                    else SHADOW_ALPHA_LIGHT
                )
            return

        if self._paint is not None:
            self._paint.setHidden_(True)
        self._shape.setHidden_(False)
        self._palette = []
        self._col_idx = []

        mode = str(getattr(config, 'OVERLAY_CONTRAST', 'auto')).lower()
        ink, rim, rimmed = contrast_scheme(mode, self._system_is_dark())
        dark_ink = ink is INK_DARK

        self._shape.setFillColor_(CGColorCreateGenericRGB(*ink, 1.0))

        # The shadow belongs to whichever layer is outermost, or it would fall
        # across the rim and muddy it.
        shadow = SHADOW_ALPHA_DARK if dark_ink else SHADOW_ALPHA_LIGHT
        self._shape.setShadowOpacity_(0.0 if rimmed else shadow)
        if self._rim is not None:
            rim_color = CGColorCreateGenericRGB(*rim, 1.0)
            self._rim.setFillColor_(rim_color)
            self._rim.setStrokeColor_(rim_color)
            self._rim.setLineWidth_(2.0 * RIM_PT if rimmed else 0.0)
            self._rim.setHidden_(not rimmed)
            self._rim.setShadowOpacity_(shadow if rimmed else 0.0)

    def _show(self, motion_state: str, fresh: bool) -> None:
        # Checked here rather than only at construction so the Preferences
        # toggle takes effect on the next dictation instead of the next launch.
        if not getattr(config, 'SHOW_OVERLAY', True):
            if self._visible:
                self._hide()
            return
        if self._panel is None:
            self._build()
            if self._panel is None:
                return
        self._motion_state = motion_state
        if fresh:
            # A new gesture, whatever the last one did. Clearing this
            # unconditionally matters: re-arming while the previous dictation
            # is still fading out would otherwise leave the flag set, and a
            # then-abandoned gesture could not retract its own glyph.
            self._recording = False
        if fresh and not self._visible:
            # A dictation always starts from the same frame: flat base, closed
            # up, no leftover velocity. That is what makes the entry identical
            # every time instead of depending on how the last one ended. Skipped
            # when it is still on screen, so a fast second press continues the
            # motion rather than restarting it.
            self._motion.reset()
            self._analyzer.rearm()
            self._reveal.snap(0.0)
        self._reveal.omega = REVEAL_OMEGA
        self._reveal.target = 1.0
        if not self._visible:
            self._visible = True
            self._reposition()
            self._apply_appearance()
            self._apply(self._motion.heights, self._reveal.value)
            self._panel.orderFrontRegardless()
        self._last_frame_at = time.perf_counter()
        self._start_clock()

    def _hide(self) -> None:
        if self._panel is None or not self._visible:
            return
        self._reveal.omega = DISMISS_OMEGA
        self._reveal.target = 0.0
        self._start_clock()

    def _mark_recording(self, value: bool) -> None:
        self._recording = value

    def _preview(self, seconds: float) -> None:
        if self._recording:
            return
        self._apply_appearance()
        self._cancel_preview_timer()
        # WORK is the sweep: it is the only state that animates without a
        # microphone, which is exactly what a colour preview needs.
        self._show(BarMotion.WORK, True)
        try:
            timer = NSTimer.timerWithTimeInterval_repeats_block_(
                max(0.5, seconds), False, lambda _t: self._end_preview()
            )
            NSRunLoop.mainRunLoop().addTimer_forMode_(timer, NSRunLoopCommonModes)
            self._preview_timer = timer
        except Exception:
            _log.debug('Preview timer unavailable; hiding immediately', exc_info=True)
            self._hide()

    def _end_preview(self) -> None:
        self._preview_timer = None
        # A real dictation may have started while the preview was up; it owns
        # the glyph now.
        if not self._recording:
            self._hide()

    def _cancel_preview_timer(self) -> None:
        timer, self._preview_timer = self._preview_timer, None
        if timer is not None:
            try:
                timer.invalidate()
            except Exception:
                pass

    def _hide_if_armed(self) -> None:
        # Disarm and "recording started" race on separate threads; hiding a
        # live recording's glyph because a stale disarm arrived is the one
        # failure this path can actually produce.
        if not self._recording:
            self._hide()

    def _finish_hide(self) -> None:
        self._visible = False
        self._recording = False
        self._stop_clock()
        self._reveal.snap(0.0)
        self._motion.reset()
        if self._shape is not None:
            self._apply(self._motion.heights, 0.0)
        if self._panel is not None:
            self._panel.orderOut_(None)

    def _teardown(self) -> None:
        self._stop_clock()
        if self._panel is not None:
            self._panel.orderOut_(None)
        self._visible = False

    def _reposition(self) -> None:
        screen = self._active_screen()
        if screen is None or self._panel is None:
            return
        visible = screen.visibleFrame()
        x = visible.origin.x + (visible.size.width - self._window_w) / 2.0
        y = visible.origin.y + BOTTOM_MARGIN
        self._panel.setFrameOrigin_((x, y))
        try:
            scale = float(screen.backingScaleFactor()) or 1.0
        except Exception:
            scale = 1.0
        self._snap = 1.0 / scale

    @staticmethod
    def _active_screen():
        """The screen the user is looking at — where the pointer is."""
        try:
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
        try:
            return NSScreen.mainScreen()
        except Exception:
            return None

    # ── the frame clock ─────────────────────────────────────────

    def _start_clock(self) -> None:
        if self._link is not None or self._timer is not None:
            return
        view = self._panel.contentView() if self._panel is not None else None
        if view is not None:
            try:
                link = view.displayLinkWithTarget_selector_(self._ticker, b'step:')
                link.addToRunLoop_forMode_(NSRunLoop.currentRunLoop(), NSRunLoopCommonModes)
                self._link = link
                return
            except Exception:
                _log.debug('CADisplayLink unavailable; using a timer', exc_info=True)
        try:
            timer = NSTimer.timerWithTimeInterval_repeats_block_(
                1.0 / 60.0, True, lambda _t: self._frame()
            )
            NSRunLoop.mainRunLoop().addTimer_forMode_(timer, NSRunLoopCommonModes)
            self._timer = timer
        except Exception:
            _log.exception('Overlay has no frame clock; it will not animate')

    def _stop_clock(self) -> None:
        link, self._link = self._link, None
        timer, self._timer = self._timer, None
        for handle in (link, timer):
            if handle is not None:
                try:
                    handle.invalidate()
                except Exception:
                    pass

    def _frame(self) -> None:
        """One display frame. Never raises — this is driven by the run loop."""
        try:
            now = time.perf_counter()
            dt = now - self._last_frame_at
            self._last_frame_at = now
            # A dropped frame, a wake from sleep or a slow first tick must not
            # teleport the physics; every value is integrated against real time
            # and clamped to something a spring can survive.
            if dt <= 0.0 or dt > 0.25:
                dt = 1.0 / 60.0

            reveal = self._reveal.tick(dt)
            if self._motion_state == BarMotion.WORK:
                heights = self._motion.tick(dt, None, 0.0, BarMotion.WORK)
            else:
                levels, gain = self._analyzer.snapshot()
                heights = self._motion.tick(dt, levels, gain, BarMotion.LISTEN)
            self._apply(heights, reveal)

            if self._reveal.target <= 0.0 and reveal <= HIDE_BELOW:
                self._finish_hide()
        except Exception:
            _log.exception('Overlay frame failed; stopping the clock')
            try:
                self._stop_clock()
            except Exception:
                pass

    def _paint_columns(self, heights) -> None:
        """Recolour the columns for this frame.

        The colour comes from the *audio*, not from the column index — see
        `overlay_theme.column_colors`. Only columns whose colour actually
        changed are written, because most frames move only one or two of them
        and a CALayer property assignment is not free.
        """
        palette = self._palette
        if not palette or not self._columns:
            return
        try:
            indices = overlay_theme.column_colors(
                self._pattern, len(palette), heights, time.monotonic()
            )
        except Exception:
            # Visualisation must never be able to break a dictation.
            return
        cache = self._col_idx
        if len(cache) != len(self._columns):
            cache = self._col_idx = [-1] * len(self._columns)
        for i, layer in enumerate(self._columns):
            idx = indices[i] if i < len(indices) else 0
            if cache[i] != idx:
                cache[i] = idx
                layer.setBackgroundColor_(palette[idx])

    def _apply(self, heights, reveal: float) -> None:
        shape = self._shape
        if shape is None:
            return
        geometry = self._geometry
        origin = (
            (self._window_w - geometry.width) / 2.0,
            (self._window_h - geometry.height) / 2.0,
        )
        points = silhouette(heights, geometry, reveal=reveal, origin=origin, snap=self._snap)

        path = CGPathCreateMutable()
        CGPathMoveToPoint(path, None, points[0][0], points[0][1])
        for x, y in points[1:]:
            CGPathAddLineToPoint(path, None, x, y)
        CGPathCloseSubpath(path)

        opacity = min(1.0, reveal * OPACITY_LEAD)
        CATransaction.begin()
        CATransaction.setDisableActions_(True)
        shape.setPath_(path)
        # Handing Core Animation the shadow path saves it re-deriving one from
        # the shape on every single frame.
        shape.setShadowPath_(path)
        shape.setOpacity_(opacity)
        rim = self._rim
        if rim is not None:
            rim.setPath_(path)
            rim.setShadowPath_(path)
            rim.setOpacity_(opacity)
        # Same path, used as a clip: this is what keeps a multi-colour glyph
        # one shape instead of twelve rectangles that disagree at their edges.
        if self._mask is not None:
            self._mask.setPath_(path)
        if self._paint is not None:
            self._paint.setOpacity_(opacity if self._themed else 0.0)
            if self._themed:
                self._paint_columns(heights)
        CATransaction.commit()


def make_overlay(geometry: GlyphGeometry | None = None) -> DictationOverlay | None:
    """Build and prewarm the overlay. None if it could not be built at all.

    Built even when `SHOW_OVERLAY` is off: an idle hidden panel costs a few KB
    and no frames (the analyzer is gated on visibility), and building it anyway
    is what lets the Preferences toggle work without a relaunch.

    Returns None rather than raising — the overlay is feedback, and losing it
    must never be able to stop someone dictating.
    """
    try:
        overlay = DictationOverlay(geometry)
        overlay.prewarm()
        return overlay
    except Exception:
        _log.exception('Overlay unavailable; continuing without it')
        return None
