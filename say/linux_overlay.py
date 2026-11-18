"""Non-activating X11 glyph, using the same pure motion engine as macOS."""
from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from typing import Any

import config
import overlay_theme
from waveform import BandAnalyzer, BarMotion, GlyphGeometry, Spring, shake_offset, silhouette


def qt_silhouette(
    heights: Sequence[float], geometry: GlyphGeometry, reveal: float, padding: float = 7.0,
) -> list[tuple[float, float]]:
    return [(x + padding, geometry.height - y + padding)
            for x, y in silhouette(heights, geometry, reveal=reveal)]


def light_ink(mode: str, desktop_dark: bool) -> bool:
    return mode == 'light' or (mode not in {'dark', 'light'} and desktop_dark)


class LinuxOverlay:
    def __init__(self, dispatch: Callable[[Callable[[], Any]], None]) -> None:
        from PySide6 import QtCore, QtGui, QtWidgets

        self._qt = (QtCore, QtGui, QtWidgets)
        self._dispatch = dispatch
        self._analyzer = BandAnalyzer()
        self._motion = BarMotion()
        self._geometry = GlyphGeometry()
        self._alpha = Spring()
        self._state = 'hidden'
        self._preview_until = 0.0
        self._failure_at = 0.0
        self._last_frame = time.monotonic()
        self._origin = (0, 0)
        owner = self

        class Surface(QtWidgets.QWidget):
            def paintEvent(self, event: Any) -> None:
                owner._paint(self)

        self._window = Surface()
        flags = (QtCore.Qt.WindowType.Tool | QtCore.Qt.WindowType.FramelessWindowHint
                 | QtCore.Qt.WindowType.WindowStaysOnTopHint
                 | QtCore.Qt.WindowType.WindowDoesNotAcceptFocus
                 | QtCore.Qt.WindowType.WindowTransparentForInput)
        self._window.setWindowFlags(flags)
        self._window.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        self._window.setAttribute(QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self._window.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._window.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self._window.resize(round(self._geometry.width + 14), round(self._geometry.height + 14))
        self._timer = QtCore.QTimer(self._window)
        self._timer.setTimerType(QtCore.Qt.TimerType.PreciseTimer)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)

    def prewarm(self) -> None:
        self._dispatch(lambda: self._window.winId())

    def feed_audio(self, block: Any) -> None:
        self._analyzer.feed(block)

    def _show(self, force: bool = False) -> None:
        if not force and not getattr(config, 'SHOW_OVERLAY', True):
            return
        _, gui, widgets = self._qt
        screen = widgets.QApplication.screenAt(gui.QCursor.pos())
        screen = screen or widgets.QApplication.primaryScreen()
        if screen is None:
            return
        rect = screen.availableGeometry()
        self._origin = (rect.x() + (rect.width() - self._window.width()) // 2,
                        rect.bottom() - self._window.height() - 16)
        self._window.move(*self._origin)
        self._window.show()
        self._window.raise_()
        self._alpha.target = 1.0
        self._last_frame = time.monotonic()
        self._timer.start()

    def _set(self, state: str) -> None:
        if self._state == 'hidden':
            self._analyzer.rearm()
            self._motion.reset()
        self._state = state
        self._preview_until = 0.0
        self._failure_at = 0.0
        self._show()

    def arm(self) -> None:
        self._dispatch(lambda: self._set(BarMotion.REST))

    def listen(self, mode: str = 'ptt') -> None:
        self._dispatch(lambda: self._set(BarMotion.LISTEN))

    def process(self) -> None:
        self._dispatch(lambda: self._set(BarMotion.WORK))

    def dismiss(self) -> None:
        self._dispatch(self._dismiss)

    def _dismiss(self) -> None:
        self._state = 'hidden'
        self._alpha.target = 0.0
        self._preview_until = 0.0

    def release_arm(self) -> None:
        self._dispatch(lambda: self._dismiss() if self._state == BarMotion.REST else None)

    def fail(self) -> None:
        def apply() -> None:
            self._state = BarMotion.REST
            self._failure_at = time.monotonic()
            self._show()
        self._dispatch(apply)

    def preview(self, seconds: float = 2.0) -> None:
        def apply() -> None:
            if self._state != 'hidden' and not self._preview_until:
                return
            self._state = BarMotion.REST
            self._preview_until = time.monotonic() + min(max(seconds, 0.1), 10.0)
            self._show(force=True)
        self._dispatch(apply)

    def set_theme(self) -> None:
        self._dispatch(self._window.update)

    def close(self) -> None:
        def apply() -> None:
            self._timer.stop()
            self._window.hide()
        self._dispatch(apply)

    def _tick(self) -> None:
        now = time.monotonic()
        dt = min(now - self._last_frame, 0.1)
        self._last_frame = now
        if self._preview_until and now >= self._preview_until:
            self._dismiss()
        if self._failure_at and now - self._failure_at >= 0.45:
            self._failure_at = 0.0
            self._dismiss()
        levels, gain = self._analyzer.snapshot()
        self._motion.tick(dt, levels, gain, self._state if self._state != 'hidden' else BarMotion.REST)
        self._alpha.tick(dt)
        if self._state == 'hidden' and self._alpha.settled:
            self._window.hide()
            self._timer.stop()
            return
        shift = shake_offset(now - self._failure_at) if self._failure_at else 0.0
        self._window.move(self._origin[0] + round(shift), self._origin[1])
        self._window.update()

    def _paint(self, surface: Any) -> None:
        core, gui, widgets = self._qt
        painter = gui.QPainter(surface)
        painter.setRenderHint(gui.QPainter.RenderHint.Antialiasing)
        points = qt_silhouette(self._motion.heights, self._geometry, self._alpha.value)
        path = gui.QPainterPath()
        path.moveTo(*points[0])
        for point in points[1:]:
            path.lineTo(*point)
        path.closeSubpath()
        resolved = overlay_theme.resolve(config.OVERLAY_THEME, config.OVERLAY_CUSTOM_COLORS)
        if resolved:
            colors, rim = resolved
        else:
            desktop_dark = widgets.QApplication.palette().window().color().lightness() < 128
            dark = light_ink(getattr(config, 'OVERLAY_CONTRAST', 'auto'), desktop_dark)
            colors = [overlay_theme.TONE_LIGHT if dark else overlay_theme.TONE_DARK]
            rim = overlay_theme.TONE_DARK if dark else overlay_theme.TONE_LIGHT
        indices = overlay_theme.column_colors(config.OVERLAY_PATTERN, len(colors), self._motion.heights,
                                               time.monotonic())
        painter.save()
        painter.setClipPath(path)
        scale = self._geometry.collapsed_scale + (1 - self._geometry.collapsed_scale) * self._alpha.value
        centre = self._geometry.width / 2
        for i, index in enumerate(indices):
            left = 7 + centre + (i * self._geometry.column_width - centre) * scale
            color = gui.QColor.fromRgbF(*colors[index])
            painter.fillRect(core.QRectF(left, 0, self._geometry.column_width * scale + 0.1,
                                        surface.height()), color)
        painter.restore()
        if resolved or getattr(config, 'OVERLAY_CONTRAST', 'auto') != 'system':
            painter.setPen(gui.QPen(gui.QColor.fromRgbF(*rim), 1.0))
            painter.setBrush(core.Qt.BrushStyle.NoBrush)
            painter.drawPath(path)
        painter.end()
