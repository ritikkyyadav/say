"""First-run wizard window.

Branded onboarding flow shown the first time Say launches. Replaces the
chain of `rumps.alert` modals with a single window that presents the
mascot, three permission cards, and a Get-Started CTA.

The window is *non-modal* — the rumps run loop keeps spinning so the
menu-bar item remains live while onboarding is open. On the final Get
Started click we call `on_finish()` (writes the first-run marker) and
order the window out.

Permission state is *not* read from macOS — there's no public API for
that — so each card just deep-links to its System Settings pane.

Falls back gracefully: if window creation fails (e.g. CI / headless),
`run_wizard` returns False and the caller can use the alert path.
"""

from __future__ import annotations

from collections.abc import Callable

import objc
from AppKit import (
    NSBackingStoreBuffered,
    NSBezierPath,
    NSButton,
    NSColor,
    NSImageView,
    NSMakeRect,
    NSMakeSize,
    NSPoint,
    NSTextField,
    NSView,
    NSWindow,
    NSWindowStyleMaskFullSizeContentView,
    NSWindowStyleMaskTitled,
)
from logging_setup import get_logger
from permissions import open_privacy_settings
from Quartz import CGColorCreateGenericRGB

import brand

_log = get_logger('wizard')

WIN_W = 540
WIN_H = 600
HERO_H = 220
PADDING = 28


_PERMISSIONS = [
    (
        'Microphone',
        'microphone',
        'Record your voice. Audio is never written to disk.',
    ),
    (
        'Accessibility',
        'accessibility',
        'Insert transcribed text into the focused app.',
    ),
    (
        'Input Monitoring',
        'input-monitoring',
        'Detect the global Fn hotkey to start recording.',
    ),
]


# ── Public API ───────────────────────────────────────────────────

def run_wizard(on_finish: Callable[[], None]) -> bool:
    """Show the wizard. Returns True if the window was created."""
    try:
        controller = _WizardController.alloc().initWithFinish_(on_finish)
        controller.show()
        # Keep a reference so the controller isn't garbage-collected
        # while the window is open. rumps holds the main run loop.
        global _live_controller
        _live_controller = controller
        return True
    except Exception:
        _log.exception('Failed to open wizard window')
        return False


_live_controller = None  # populated by run_wizard


# ── Implementation ───────────────────────────────────────────────

class _WizardController(NSView):
    """Owns the wizard window, its subviews, and the on_finish callback.

    Subclassing NSView is a convenient way to get an Objective-C-bridged
    instance with target/action support; the view itself is never put on
    screen.
    """

    _on_finish = None
    _window = None
    _action_targets: list = []

    def initWithFinish_(self, on_finish):
        self = objc.super(_WizardController, self).initWithFrame_(
            NSMakeRect(0, 0, 1, 1)
        )
        if self is None:
            return None
        self._on_finish = on_finish
        self._action_targets = []
        self._build_window()
        return self

    def show(self):
        self._window.center()
        self._window.makeKeyAndOrderFront_(None)
        # Bring the rumps app to the front so this isn't buried.
        from AppKit import NSApp, NSApplicationActivationPolicyAccessory
        try:
            NSApp.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
            NSApp.activateIgnoringOtherApps_(True)
        except Exception:
            pass

    # ── Window construction ──────────────────────────────────────

    def _build_window(self) -> None:
        style = NSWindowStyleMaskTitled | NSWindowStyleMaskFullSizeContentView
        win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, WIN_W, WIN_H), style, NSBackingStoreBuffered, False,
        )
        win.setTitle_('Welcome to Say')
        win.setTitlebarAppearsTransparent_(True)
        win.setMovableByWindowBackground_(True)
        win.setBackgroundColor_(brand.color(brand.MIST))
        win.setReleasedWhenClosed_(False)
        win.setDelegate_(self)  # so we catch the close button

        content = win.contentView()
        content.setWantsLayer_(True)
        content.layer().setBackgroundColor_(_cg(brand.MIST))

        self._build_hero(content)
        self._build_body(content)
        self._build_footer(content)

        self._window = win

    def _build_hero(self, content) -> None:
        hero = NSView.alloc().initWithFrame_(
            NSMakeRect(0, WIN_H - HERO_H, WIN_W, HERO_H)
        )
        hero.setWantsLayer_(True)
        hero.layer().setBackgroundColor_(_cg(brand.SKY_200))
        content.addSubview_(hero)

        # Logo (or fallback bear glyph)
        logo = brand.logo_image(size=104)
        if logo is not None:
            iv = NSImageView.alloc().initWithFrame_(
                NSMakeRect((WIN_W - 104) / 2, HERO_H - 132, 104, 104)
            )
            iv.setImage_(logo)
            hero.addSubview_(iv)
        else:
            placeholder = _BearPlaceholder.alloc().initWithFrame_(
                NSMakeRect((WIN_W - 104) / 2, HERO_H - 132, 104, 104)
            )
            hero.addSubview_(placeholder)

        title = _label(
            NSMakeRect(PADDING, 50, WIN_W - 2 * PADDING, 36),
            'Welcome to Say', brand.font(26, 'semibold'), brand.color(brand.INK),
            align='center',
        )
        hero.addSubview_(title)

        subtitle = _label(
            NSMakeRect(PADDING, 24, WIN_W - 2 * PADDING, 22),
            'Local voice dictation for macOS.',
            brand.font(13), brand.color(brand.SLATE),
            align='center',
        )
        hero.addSubview_(subtitle)

    def _build_body(self, content) -> None:
        body_top = WIN_H - HERO_H - 24
        section = _label(
            NSMakeRect(PADDING, body_top - 22, WIN_W - 2 * PADDING, 20),
            'Three permissions and you’re set.',
            brand.font(13, 'medium'), brand.color(brand.SLATE),
            align='left',
        )
        content.addSubview_(section)

        card_h = 64
        card_gap = 10
        cards_top = body_top - 50

        for i, (name, section_id, desc) in enumerate(_PERMISSIONS):
            y = cards_top - (i + 1) * card_h - i * card_gap
            self._build_permission_card(content, name, section_id, desc,
                                        NSMakeRect(PADDING, y,
                                                   WIN_W - 2 * PADDING, card_h))

    def _build_permission_card(self, parent, name: str, section_id: str,
                               desc: str, frame) -> None:
        card = NSView.alloc().initWithFrame_(frame)
        card.setWantsLayer_(True)
        layer = card.layer()
        layer.setBackgroundColor_(_cg(brand.MIST))
        layer.setBorderWidth_(1.0)
        layer.setBorderColor_(_cg(brand.LINE))
        layer.setCornerRadius_(10.0)
        parent.addSubview_(card)

        # Numbered chip on the left, sky-tinted
        chip = NSView.alloc().initWithFrame_(NSMakeRect(14, (frame.size.height - 28) / 2, 28, 28))
        chip.setWantsLayer_(True)
        chip.layer().setBackgroundColor_(_cg(brand.SKY_100))
        chip.layer().setCornerRadius_(14.0)
        card.addSubview_(chip)

        idx = {'microphone': '1', 'accessibility': '2', 'input-monitoring': '3'}.get(section_id, '•')
        chip_label = _label(
            NSMakeRect(0, 0, 28, 28), idx,
            brand.font(13, 'semibold'), brand.color(brand.SKY_700),
            align='center',
        )
        # vertically center the label inside chip
        chip_label.setFrame_(NSMakeRect(0, 5, 28, 18))
        chip.addSubview_(chip_label)

        title = _label(
            NSMakeRect(56, frame.size.height - 36, 280, 22), name,
            brand.font(14, 'semibold'), brand.color(brand.INK),
            align='left',
        )
        card.addSubview_(title)

        sub = _label(
            NSMakeRect(56, 12, 320, 18), desc,
            brand.font(12), brand.color(brand.SLATE),
            align='left',
        )
        card.addSubview_(sub)

        btn = _PillButton.alloc().initWithFrame_andStyle_(
            NSMakeRect(frame.size.width - 130, (frame.size.height - 30) / 2, 116, 30),
            'secondary',
        )
        btn.setTitle_('Open Settings')
        self._wire_button(btn, lambda _: open_privacy_settings(section_id))
        card.addSubview_(btn)

    def _build_footer(self, content) -> None:
        # Get Started CTA on the right
        cta = _PillButton.alloc().initWithFrame_andStyle_(
            NSMakeRect(WIN_W - PADDING - 160, 24, 160, 38),
            'primary',
        )
        cta.setTitle_('Get Started')
        self._wire_button(cta, lambda _: self._finish())
        content.addSubview_(cta)

        # Skip / Later, text-only
        skip = _PillButton.alloc().initWithFrame_andStyle_(
            NSMakeRect(PADDING, 24, 130, 38),
            'ghost',
        )
        skip.setTitle_('Skip for now')
        self._wire_button(skip, lambda _: self._finish())
        content.addSubview_(skip)

    # ── Actions ──────────────────────────────────────────────────

    def _wire_button(self, btn, callback) -> None:
        target = _WizardActionTarget.alloc().init()
        target._callback = callback
        self._action_targets.append(target)
        btn.setTarget_(target)
        btn.setAction_(objc.selector(target.handleClick_, signature=b'v@:@'))

    def _finish(self) -> None:
        cb = self._on_finish
        self._on_finish = None  # idempotent — close button + button-click race
        try:
            if self._window is not None:
                self._window.orderOut_(None)
        except Exception:
            pass
        if cb is not None:
            try:
                cb()
            except Exception:
                _log.exception('on_finish callback failed')

    # NSWindowDelegate informal protocol: also treat the close button
    # as a finish so we don't re-prompt on next launch.
    def windowWillClose_(self, _notification):
        self._finish()


# ── Helper subviews ──────────────────────────────────────────────

class _PillButton(NSButton):
    """NSButton subclass with a layer-backed pill shape and 3 styles."""

    _style = 'primary'

    def initWithFrame_andStyle_(self, frame, style):
        self = objc.super(_PillButton, self).initWithFrame_(frame)
        if self is None:
            return None
        self._style = style
        self.setBordered_(False)
        self.setWantsLayer_(True)
        layer = self.layer()
        layer.setCornerRadius_(frame.size.height / 2.0)

        if style == 'primary':
            layer.setBackgroundColor_(_cg(brand.PINK_600))
            self._set_title_color(brand.MIST, weight='semibold', size=13.5)
        elif style == 'secondary':
            layer.setBackgroundColor_(_cg(brand.SKY_100))
            layer.setBorderWidth_(1.0)
            layer.setBorderColor_(_cg(brand.LINE))
            self._set_title_color(brand.SKY_700, weight='medium', size=12.5)
        else:  # ghost
            layer.setBackgroundColor_(_cg(brand.MIST, alpha=0.0))
            self._set_title_color(brand.SLATE, weight='medium', size=12.5)

        return self

    def _set_title_color(self, rgb, weight: str, size: float) -> None:
        from AppKit import (
            NSAttributedString,
            NSCenterTextAlignment,
            NSForegroundColorAttributeName,
            NSFontAttributeName,
            NSMutableParagraphStyle,
            NSParagraphStyleAttributeName,
        )
        ps = NSMutableParagraphStyle.alloc().init()
        ps.setAlignment_(NSCenterTextAlignment)
        attrs = {
            NSForegroundColorAttributeName: brand.color(rgb),
            NSFontAttributeName: brand.font(size, weight),
            NSParagraphStyleAttributeName: ps,
        }
        s = NSAttributedString.alloc().initWithString_attributes_(
            self.title() or '', attrs,
        )
        self.setAttributedTitle_(s)

    def setTitle_(self, t):
        objc.super(_PillButton, self).setTitle_(t)
        # Re-apply attributed style with new text
        if self._style == 'primary':
            self._set_title_color(brand.MIST, 'semibold', 13.5)
        elif self._style == 'secondary':
            self._set_title_color(brand.SKY_700, 'medium', 12.5)
        else:
            self._set_title_color(brand.SLATE, 'medium', 12.5)


class _BearPlaceholder(NSView):
    """Three-circle bear silhouette used when no logo asset is present.

    Not a substitute for the real mark, but cute enough to ship with.
    """

    def drawRect_(self, _rect):
        b = self.bounds()
        w, h = b.size.width, b.size.height

        # Outline
        NSColor.blackColor().setStroke()
        brand.color(brand.BROWN_500).setFill()

        head = NSBezierPath.bezierPathWithOvalInRect_(
            NSMakeRect(w * 0.12, h * 0.10, w * 0.76, h * 0.74)
        )
        head.fill()
        head.setLineWidth_(2.5)
        head.stroke()

        ear_l = NSBezierPath.bezierPathWithOvalInRect_(
            NSMakeRect(w * 0.06, h * 0.62, w * 0.32, h * 0.32)
        )
        ear_l.fill()
        ear_l.setLineWidth_(2.5)
        ear_l.stroke()

        ear_r = NSBezierPath.bezierPathWithOvalInRect_(
            NSMakeRect(w * 0.62, h * 0.62, w * 0.32, h * 0.32)
        )
        ear_r.fill()
        ear_r.setLineWidth_(2.5)
        ear_r.stroke()

        # Pink cheeks
        brand.color(brand.PINK_500).setFill()
        cheek_l = NSBezierPath.bezierPathWithOvalInRect_(
            NSMakeRect(w * 0.18, h * 0.30, w * 0.16, h * 0.13)
        )
        cheek_l.fill()
        cheek_r = NSBezierPath.bezierPathWithOvalInRect_(
            NSMakeRect(w * 0.66, h * 0.30, w * 0.16, h * 0.13)
        )
        cheek_r.fill()

        # Eyes — small black dots
        NSColor.blackColor().setFill()
        for ex in (0.34, 0.66):
            NSBezierPath.bezierPathWithOvalInRect_(
                NSMakeRect(w * ex - 3, h * 0.50, 6, 6)
            ).fill()

        # Nose
        NSBezierPath.bezierPathWithOvalInRect_(
            NSMakeRect(w * 0.50 - 4, h * 0.40, 8, 5)
        ).fill()


class _WizardActionTarget(NSView):
    """Bridges NSButton target/action to a Python callable."""

    _callback = None

    @objc.typedSelector(b'v@:@')
    def handleClick_(self, sender):
        if self._callback is not None:
            self._callback(sender)


# ── tiny utilities ───────────────────────────────────────────────

def _label(frame, text: str, font, color, align: str = 'left') -> NSTextField:
    f = NSTextField.alloc().initWithFrame_(frame)
    f.setStringValue_(text)
    f.setBezeled_(False)
    f.setDrawsBackground_(False)
    f.setEditable_(False)
    f.setSelectable_(False)
    f.setFont_(font)
    f.setTextColor_(color)
    f.setAlignment_({'left': 0, 'right': 1, 'center': 2}[align])
    return f


def _cg(rgb, alpha: float = 1.0):
    r, g, b = rgb
    return CGColorCreateGenericRGB(r, g, b, alpha)


# Suppress unused-import warnings from a couple of sentinels we
# referenced only via attribute access.
_ = NSPoint
_ = NSMakeSize
