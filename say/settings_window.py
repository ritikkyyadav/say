"""Settings window for Say.

Replaces the "edit config.py by hand" instruction with a real preferences
pane. Every control is bound to one persistable key in `settings.py` and
saves *live* — there is no Save/Cancel button. This matches modern macOS
System Settings conventions and removes a class of "I forgot to save"
bugs.

Layout (top → bottom):

    Header  — sky band, mascot + "Say Settings"
    Section — Transcription (Model, Language, Pre-roll, Vocabulary)
    Section — Text Injection (Method)
    Section — Interface (Show overlay, Show notifications, Position)
    Footer  — small grey print: where the file lives.

The window is non-modal: the menu-bar app stays interactive while it's
open. Multiple invocations focus the existing window instead of
spawning a second one.
"""

from __future__ import annotations

import objc
from AppKit import (
    NSBackingStoreBuffered,
    NSBezelStyleRounded,
    NSImageView,
    NSMakeRect,
    NSPopUpButton,
    NSSegmentStyleRounded,
    NSSegmentedControl,
    NSSlider,
    NSSwitch,
    NSTextField,
    NSView,
    NSWindow,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskFullSizeContentView,
    NSWindowStyleMaskTitled,
)
from logging_setup import get_logger
from Quartz import CGColorCreateGenericRGB

import brand
import config
import model_catalog
import model_manager
import settings as settings_mod

_log = get_logger('settings_window')

WIN_W = 540
WIN_H = 620
HERO_H = 110
PADDING = 28


def _model_options() -> list[tuple[str, str]]:
    """(repo_id, label) pairs for the Model popup, sourced from the catalog."""
    opts: list[tuple[str, str]] = []
    for info in model_catalog.catalog():
        suffix = ' (recommended)' if info.recommended else ''
        opts.append((info.id, f'{info.name}{suffix}'))
    return opts


_MODEL_OPTIONS = _model_options()

_INJECT_LABELS = ['Auto', 'Accessibility', 'Clipboard']
_INJECT_VALUES = ['auto', 'accessibility', 'clipboard']

_POSITION_LABELS = ['Bottom', 'Top']
_POSITION_VALUES = ['bottom', 'top']


# ── Public API ───────────────────────────────────────────────────

_singleton: '_SettingsController | None' = None


def open_settings() -> None:
    """Show or focus the Settings window."""
    global _singleton
    try:
        if _singleton is None:
            _singleton = _SettingsController.alloc().init()
        _singleton.show()
    except Exception:
        _log.exception('Failed to open Settings window')


# ── Implementation ───────────────────────────────────────────────

class _SettingsController(NSView):
    """Owns the settings window and its bindings to settings.py."""

    _window = None
    _action_targets: list = []

    def init(self):
        self = objc.super(_SettingsController, self).initWithFrame_(
            NSMakeRect(0, 0, 1, 1)
        )
        if self is None:
            return None
        self._action_targets = []
        self._build_window()
        return self

    def show(self):
        self._window.center()
        self._window.makeKeyAndOrderFront_(None)
        try:
            from AppKit import NSApp
            NSApp.activateIgnoringOtherApps_(True)
        except Exception:
            pass

    # ── Construction ─────────────────────────────────────────────

    def _build_window(self) -> None:
        style = (NSWindowStyleMaskTitled
                 | NSWindowStyleMaskClosable
                 | NSWindowStyleMaskFullSizeContentView)
        win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, WIN_W, WIN_H), style, NSBackingStoreBuffered, False,
        )
        win.setTitle_('Say Settings')
        win.setTitlebarAppearsTransparent_(True)
        win.setMovableByWindowBackground_(True)
        win.setReleasedWhenClosed_(False)

        content = win.contentView()
        content.setWantsLayer_(True)
        content.layer().setBackgroundColor_(_cg(brand.MIST))

        self._build_hero(content)
        self._build_sections(content)
        self._build_footer(content)

        self._window = win

    def _build_hero(self, content) -> None:
        hero = NSView.alloc().initWithFrame_(
            NSMakeRect(0, WIN_H - HERO_H, WIN_W, HERO_H)
        )
        hero.setWantsLayer_(True)
        hero.layer().setBackgroundColor_(_cg(brand.SKY_200))
        content.addSubview_(hero)

        logo = brand.logo_image(size=64)
        if logo is not None:
            iv = NSImageView.alloc().initWithFrame_(
                NSMakeRect(PADDING, (HERO_H - 64) / 2, 64, 64)
            )
            iv.setImage_(logo)
            hero.addSubview_(iv)

        title = _label(
            NSMakeRect(PADDING + 80, HERO_H / 2 + 4, WIN_W - PADDING - 100, 26),
            'Say Settings', brand.font(20, 'semibold'), brand.color(brand.INK),
        )
        hero.addSubview_(title)

        sub = _label(
            NSMakeRect(PADDING + 80, HERO_H / 2 - 22, WIN_W - PADDING - 100, 18),
            'Saved automatically. Model changes apply right away.',
            brand.font(12), brand.color(brand.SLATE),
        )
        hero.addSubview_(sub)

    def _build_sections(self, content) -> None:
        # We layout sections from the top of the body downward.
        y = WIN_H - HERO_H - 24

        y = self._build_section(
            content, y, 'TRANSCRIPTION',
            [
                self._row_model,
                self._row_language,
                self._row_pre_roll,
                self._row_vocab,
            ],
        )
        y -= 18
        y = self._build_section(
            content, y, 'TEXT INJECTION',
            [self._row_inject_method],
        )
        y -= 18
        y = self._build_section(
            content, y, 'INTERFACE',
            [
                self._row_show_overlay,
                self._row_show_notifications,
                self._row_overlay_position,
            ],
        )

    def _build_section(self, content, y_top: int, title: str, row_builders) -> int:
        # Section header
        header = _label(
            NSMakeRect(PADDING, y_top - 16, WIN_W - 2 * PADDING, 16),
            title,
            brand.font(10.5, 'semibold'), brand.color(brand.SLATE),
        )
        content.addSubview_(header)

        # Card
        row_h = 44
        card_h = row_h * len(row_builders) + 1  # +1 for last divider
        card_top = y_top - 22
        card_y = card_top - card_h
        card = NSView.alloc().initWithFrame_(
            NSMakeRect(PADDING, card_y, WIN_W - 2 * PADDING, card_h)
        )
        card.setWantsLayer_(True)
        card.layer().setBackgroundColor_(_cg(brand.MIST))
        card.layer().setBorderWidth_(1.0)
        card.layer().setBorderColor_(_cg(brand.LINE))
        card.layer().setCornerRadius_(10.0)
        content.addSubview_(card)

        for i, builder in enumerate(row_builders):
            row_top = card_h - (i + 1) * row_h
            builder(card, row_top, row_h)
            # Divider between rows (skip after last)
            if i < len(row_builders) - 1:
                divider = NSView.alloc().initWithFrame_(
                    NSMakeRect(14, row_top, card.frame().size.width - 28, 1)
                )
                divider.setWantsLayer_(True)
                divider.layer().setBackgroundColor_(_cg(brand.LINE))
                card.addSubview_(divider)

        return card_y

    # ── Row builders ─────────────────────────────────────────────

    def _row_model(self, card, top: int, h: int) -> None:
        self._row_label(card, top, h, 'Model')

        popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(
            NSMakeRect(card.frame().size.width - 280, top + (h - 26) / 2, 266, 26),
            False,
        )
        for value, label in _MODEL_OPTIONS:
            popup.addItemWithTitle_(label)
            item = popup.lastItem()
            if item is not None:
                item.setRepresentedObject_(value)

        current = settings_mod.get('MODEL', config.MODEL)
        for i, (value, _) in enumerate(_MODEL_OPTIONS):
            if value == current:
                popup.selectItemAtIndex_(i)
                break

        self._wire_action(popup, lambda sender: self._on_model(sender))
        card.addSubview_(popup)

    def _row_language(self, card, top: int, h: int) -> None:
        self._row_label(card, top, h, 'Language')

        field = NSTextField.alloc().initWithFrame_(
            NSMakeRect(card.frame().size.width - 200, top + (h - 24) / 2, 186, 24)
        )
        current = settings_mod.get('LANGUAGE', config.LANGUAGE)
        field.setStringValue_('' if current is None else str(current))
        field.setPlaceholderString_('en (auto if blank)')
        field.setBezeled_(True)
        field.setBezelStyle_(NSBezelStyleRounded)
        field.setEditable_(True)
        field.setSelectable_(True)
        self._wire_action(field, lambda sender: self._on_language(sender))
        card.addSubview_(field)

    def _row_pre_roll(self, card, top: int, h: int) -> None:
        self._row_label(card, top, h, 'Pre-roll')

        slider = NSSlider.alloc().initWithFrame_(
            NSMakeRect(card.frame().size.width - 240, top + (h - 22) / 2, 180, 22)
        )
        slider.setMinValue_(0.0)
        slider.setMaxValue_(2.0)
        slider.setNumberOfTickMarks_(9)
        slider.setAllowsTickMarkValuesOnly_(False)
        current = float(settings_mod.get('PRE_ROLL_SECONDS', config.PRE_ROLL_SECONDS))
        slider.setDoubleValue_(current)
        card.addSubview_(slider)

        readout = _label(
            NSMakeRect(card.frame().size.width - 56, top + (h - 18) / 2, 44, 18),
            f'{current:.1f}s',
            brand.mono(11.5), brand.color(brand.SLATE),
            align='right',
        )
        card.addSubview_(readout)

        self._wire_action(slider, lambda sender: self._on_pre_roll(sender, readout))

    def _row_vocab(self, card, top: int, h: int) -> None:
        self._row_label(card, top, h, 'Vocabulary')

        field = NSTextField.alloc().initWithFrame_(
            NSMakeRect(card.frame().size.width - 320, top + (h - 24) / 2, 306, 24)
        )
        current = settings_mod.get('INITIAL_PROMPT', config.INITIAL_PROMPT) or ''
        field.setStringValue_(current)
        field.setPlaceholderString_('Optional jargon, names — biases Whisper')
        field.setBezeled_(True)
        field.setBezelStyle_(NSBezelStyleRounded)
        field.setEditable_(True)
        field.setSelectable_(True)
        self._wire_action(field, lambda sender: self._on_vocab(sender))
        card.addSubview_(field)

    def _row_inject_method(self, card, top: int, h: int) -> None:
        self._row_label(card, top, h, 'Method')

        seg = NSSegmentedControl.alloc().initWithFrame_(
            NSMakeRect(card.frame().size.width - 320, top + (h - 26) / 2, 306, 26)
        )
        seg.setSegmentCount_(len(_INJECT_LABELS))
        seg.setSegmentStyle_(NSSegmentStyleRounded)
        for i, lbl in enumerate(_INJECT_LABELS):
            seg.setLabel_forSegment_(lbl, i)
            seg.setWidth_forSegment_(98, i)

        current = settings_mod.get('INJECT_METHOD', config.INJECT_METHOD)
        if current in _INJECT_VALUES:
            seg.setSelectedSegment_(_INJECT_VALUES.index(current))
        else:
            seg.setSelectedSegment_(0)

        self._wire_action(seg, lambda sender: self._on_inject_method(sender))
        card.addSubview_(seg)

    def _row_show_overlay(self, card, top: int, h: int) -> None:
        self._row_label(card, top, h, 'Show overlay')
        sw = self._switch_at(card, top, h)
        sw.setState_(1 if settings_mod.get('SHOW_OVERLAY', config.SHOW_OVERLAY) else 0)
        self._wire_action(sw, lambda sender: settings_mod.set_value(
            'SHOW_OVERLAY', bool(sender.state())))

    def _row_show_notifications(self, card, top: int, h: int) -> None:
        self._row_label(card, top, h, 'Show notifications')
        sw = self._switch_at(card, top, h)
        sw.setState_(1 if settings_mod.get('SHOW_NOTIFICATIONS', config.SHOW_NOTIFICATIONS) else 0)
        self._wire_action(sw, lambda sender: settings_mod.set_value(
            'SHOW_NOTIFICATIONS', bool(sender.state())))

    def _row_overlay_position(self, card, top: int, h: int) -> None:
        self._row_label(card, top, h, 'Overlay position')

        seg = NSSegmentedControl.alloc().initWithFrame_(
            NSMakeRect(card.frame().size.width - 220, top + (h - 26) / 2, 200, 26)
        )
        seg.setSegmentCount_(len(_POSITION_LABELS))
        seg.setSegmentStyle_(NSSegmentStyleRounded)
        for i, lbl in enumerate(_POSITION_LABELS):
            seg.setLabel_forSegment_(lbl, i)
            seg.setWidth_forSegment_(96, i)

        current = settings_mod.get('OVERLAY_POSITION', config.OVERLAY_POSITION)
        if current in _POSITION_VALUES:
            seg.setSelectedSegment_(_POSITION_VALUES.index(current))
        else:
            seg.setSelectedSegment_(0)

        self._wire_action(seg, lambda sender: settings_mod.set_value(
            'OVERLAY_POSITION', _POSITION_VALUES[sender.selectedSegment()]))
        card.addSubview_(seg)

    # ── Row helpers ──────────────────────────────────────────────

    def _row_label(self, card, top: int, h: int, text: str) -> None:
        lbl = _label(
            NSMakeRect(16, top + (h - 18) / 2, 200, 18),
            text, brand.font(13), brand.color(brand.INK),
        )
        card.addSubview_(lbl)

    def _switch_at(self, card, top: int, h: int) -> NSSwitch:
        sw = NSSwitch.alloc().initWithFrame_(
            NSMakeRect(card.frame().size.width - 56, top + (h - 22) / 2, 42, 22)
        )
        card.addSubview_(sw)
        return sw

    # ── Footer ───────────────────────────────────────────────────

    def _build_footer(self, content) -> None:
        footer = _label(
            NSMakeRect(PADDING, 14, WIN_W - 2 * PADDING, 18),
            f'Settings file · {settings_mod.SETTINGS_FILE.replace(_home(), "~")}',
            brand.mono(10.5), brand.color(brand.SLATE),
            align='center',
        )
        content.addSubview_(footer)

    # ── Action plumbing ──────────────────────────────────────────

    def _wire_action(self, control, callback) -> None:
        target = _SettingsActionTarget.alloc().init()
        target._callback = callback
        self._action_targets.append(target)
        control.setTarget_(target)
        control.setAction_(objc.selector(target.handleClick_, signature=b'v@:@'))
        # NSTextField wants action on Enter; for live-save behavior we
        # also call setContinuous_(True) on slider so dragging saves.
        try:
            from AppKit import NSSlider as _NSSlider
            if isinstance(control, _NSSlider):
                control.setContinuous_(True)
        except Exception:
            pass

    # ── Handlers (only the non-trivial ones) ─────────────────────

    def _on_model(self, sender) -> None:
        item = sender.selectedItem()
        if item is None:
            return
        value = item.representedObject()
        if value:
            # Route through the manager so the running engine swaps to the new
            # model immediately (and the choice is persisted) — no relaunch.
            try:
                model_manager.get_manager().set_active(str(value))
            except Exception:
                _log.exception('Live model switch failed; persisting only')
                settings_mod.set_value('MODEL', str(value))

    def _on_language(self, sender) -> None:
        text = (sender.stringValue() or '').strip()
        settings_mod.set_value('LANGUAGE', text or None)

    def _on_pre_roll(self, sender, readout) -> None:
        v = round(float(sender.doubleValue()), 1)
        readout.setStringValue_(f'{v:.1f}s')
        settings_mod.set_value('PRE_ROLL_SECONDS', v)

    def _on_vocab(self, sender) -> None:
        settings_mod.set_value('INITIAL_PROMPT', sender.stringValue() or '')

    def _on_inject_method(self, sender) -> None:
        idx = sender.selectedSegment()
        if 0 <= idx < len(_INJECT_VALUES):
            settings_mod.set_value('INJECT_METHOD', _INJECT_VALUES[idx])


# ── Action target & utilities ────────────────────────────────────

class _SettingsActionTarget(NSView):
    _callback = None

    @objc.typedSelector(b'v@:@')
    def handleClick_(self, sender):
        if self._callback is not None:
            try:
                self._callback(sender)
            except Exception:
                _log.exception('Settings action failed')


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


def _home() -> str:
    import os
    return os.path.expanduser('~')
