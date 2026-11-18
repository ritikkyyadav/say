"""Linux desktop services. Importing this module never opens a display."""
from __future__ import annotations

import queue
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from gestures import ListenerCallbacks

from injector import InjectionResult
from logging_setup import get_logger

_log = get_logger('linux-desktop')
_MAX_CLIPBOARD_BYTES = 16 * 1024 * 1024
_OWNER_MIME = 'application/x-say-clipboard-owner'


def x11_context() -> tuple[str | None, bool]:
    from Xlib import XK, display

    connection = display.Display()
    try:
        window = connection.get_input_focus().focus
        app = None
        for _ in range(32):
            if not hasattr(window, 'get_wm_class'):
                break
            names = window.get_wm_class()
            if names:
                app = str(names[-1])
                break
            parent = window.query_tree().parent
            if parent.id == window.id:
                break
            window = parent
        pressed = bytes(connection.query_keymap())
        held = False
        for name in ('Control_L', 'Control_R', 'Shift_L', 'Shift_R', 'Alt_L', 'Alt_R',
                     'Super_L', 'Super_R', 'Meta_L', 'Meta_R'):
            code = connection.keysym_to_keycode(XK.string_to_keysym(name))
            if code and pressed[code // 8] & (1 << (code % 8)):
                held = True
        return app, held
    finally:
        connection.close()


@dataclass
class _Request:
    function: Callable[[threading.Event], Any]
    done: threading.Event = field(default_factory=threading.Event)
    cancelled: threading.Event = field(default_factory=threading.Event)
    result: Any = None
    error: Exception | None = None


class LinuxDesktop:
    def __init__(self, session: str) -> None:
        from PySide6 import QtCore, QtWidgets

        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError('The Linux desktop must be created on the main thread.')
        self.session = session
        self._core = QtCore
        existing = QtWidgets.QApplication.instance()
        if existing is not None and not isinstance(existing, QtWidgets.QApplication):
            raise RuntimeError('A graphical QApplication is required for the Linux desktop.')
        self._app = existing if existing is not None else QtWidgets.QApplication(['SayEngine'])
        self._app.setQuitOnLastWindowClosed(False)
        self._app.setApplicationName('SayEngine')
        self._requests: queue.Queue[_Request] = queue.Queue()
        self._active: list[_Request] = []
        self._stop_timer: Any = None
        self._request_lock = threading.Lock()
        self._closed = False
        self._locked: set[str] = set()
        self.listener: Any = None
        self.listener_error: str | None = None
        self._watcher: Any = None
        self._poll = QtCore.QTimer()
        self._poll.setInterval(10)
        self._poll.timeout.connect(self._drain)
        self._poll.start()
        self.overlay: Any = None
        if session == 'x11':
            from linux_overlay import LinuxOverlay
            self.overlay = LinuxOverlay(self.post)
            self.overlay.prewarm()

    def post(self, function: Callable[[], Any]) -> None:
        if threading.current_thread() is threading.main_thread():
            if not self._closed:
                function()
            return
        with self._request_lock:
            if not self._closed:
                self._requests.put(_Request(lambda _: function()))

    def _call(self, function: Callable[[threading.Event], Any]) -> Any:
        request = _Request(function)
        with self._request_lock:
            if self._closed:
                raise RuntimeError('The desktop is shutting down.')
            if threading.current_thread() is not threading.main_thread():
                self._requests.put(request)
        if threading.current_thread() is threading.main_thread():
            return function(request.cancelled)
        if not request.done.wait(3):
            request.cancelled.set()
            raise TimeoutError('The Linux desktop did not respond in time.')
        if request.error is not None:
            raise request.error
        return request.result

    def _drain(self) -> None:
        while True:
            try:
                request = self._requests.get_nowait()
            except queue.Empty:
                return
            self._active.append(request)
            try:
                if request.cancelled.is_set() or self._closed:
                    raise RuntimeError('Desktop operation was cancelled.')
                request.result = request.function(request.cancelled)
            except Exception as exc:
                request.error = exc
                _log.warning('Desktop operation failed: %s', exc)
            finally:
                self._active.remove(request)
                request.done.set()

    def start_listener(
        self, callbacks: ListenerCallbacks, hotkey: str | None = None,
    ) -> bool:
        if self.session != 'x11':
            return False
        import config
        from hotkey import HotkeyListener

        def start() -> None:
            if self._locked or self._closed:
                if self.listener is not None:
                    self.listener.force_idle()
                return
            callbacks['on_arm']()
            callbacks['on_start']()

        def stop() -> None:
            callbacks['on_stop']()
            callbacks['on_disarm']()

        def cancel() -> None:
            callbacks['on_cancel']()
            callbacks['on_disarm']()

        try:
            x11_context()
            self.listener = HotkeyListener(
                start, stop, hotkey=hotkey or config.HOTKEY_LEGACY, on_cancel_cb=cancel,
            )
            if not self.listener.start():
                raise RuntimeError('Could not register the X11 shortcut.')
            return True
        except Exception as exc:
            self.listener_error = str(exc)
            _log.warning('Global shortcut unavailable: %s', exc)
            if self.listener is not None:
                self.listener.stop()
                self.listener = None
            return False

    def frontmost_app(self) -> str | None:
        if self.session != 'x11':
            return None
        try:
            return x11_context()[0]
        except Exception:
            return None

    def inject(self, text: str, expected_app: str | None = None) -> InjectionResult:
        if self.session != 'x11':
            return InjectionResult(False, 'manual', 'Copy the transcript and paste it manually.')
        if not text:
            return InjectionResult(False, 'clipboard', 'No text to insert.')
        try:
            return self._call(lambda cancelled: self._paste(text, expected_app, cancelled))
        except Exception as exc:
            return InjectionResult(False, 'clipboard', str(exc))

    def copy_to_clipboard(self, text: str) -> bool:
        if self.session != 'x11':
            return False
        try:
            self._call(lambda cancelled: self._app.clipboard().setText(text)
                       if not cancelled.is_set() else None)
            return True
        except Exception:
            return False

    def _paste(
        self, text: str, expected_app: str | None, cancelled: threading.Event,
    ) -> InjectionResult:
        if self._locked:
            return InjectionResult(False, 'clipboard', 'The desktop is locked or sleeping.')
        app, held = x11_context()
        if not app or (expected_app is not None and app != expected_app):
            return InjectionResult(False, 'clipboard', 'Focus changed; your transcript was saved.')
        if held:
            return InjectionResult(False, 'clipboard', 'Release modifier keys before inserting text.')
        clipboard = self._app.clipboard()
        source = clipboard.mimeData()
        formats = source.formats() if source is not None else []
        if len(formats) > 64:
            return InjectionResult(False, 'clipboard', 'Clipboard is too complex to preserve safely.')
        snapshot: dict[str, bytes] = {}
        size = 0
        for name in formats:
            data = source.data(name).data()
            size += len(data)
            if size > _MAX_CLIPBOARD_BYTES or cancelled.is_set():
                return InjectionResult(False, 'clipboard', 'Clipboard could not be preserved safely.')
            snapshot[name] = data
        app_now, held_now = x11_context()
        if cancelled.is_set() or self._locked or app_now != app or held_now:
            return InjectionResult(False, 'clipboard', 'Input changed; your transcript was saved.')
        from pynput import keyboard

        if cancelled.is_set() or self._closed:
            return InjectionResult(False, 'clipboard', 'Insertion was cancelled.')
        marker = uuid.uuid4().hex.encode()
        replacement = self._core.QMimeData()
        replacement.setText(text)
        replacement.setData(_OWNER_MIME, marker)
        clipboard.setMimeData(replacement)
        try:
            controller = keyboard.Controller()
            terminal = any(name in app.lower() for name in (
                'terminal', 'xterm', 'konsole', 'kitty', 'alacritty', 'wezterm', 'tilix', 'rxvt',
            ))
            keys = [keyboard.Key.ctrl, keyboard.Key.shift] if terminal else [keyboard.Key.ctrl]
            try:
                for key in keys:
                    controller.press(key)
                controller.press('v')
                controller.release('v')
            finally:
                for key in reversed(keys):
                    try:
                        controller.release(key)
                    except Exception:
                        _log.warning('Could not release a paste modifier.')
            settle = self._core.QEventLoop()
            self._core.QTimer.singleShot(250, settle.quit)
            settle.exec()
        finally:
            current = clipboard.mimeData()
            if (clipboard.ownsClipboard() and current is not None
                    and current.data(_OWNER_MIME).data() == marker):
                restored = self._core.QMimeData()
                for name, data in snapshot.items():
                    restored.setData(name, data)
                clipboard.setMimeData(restored)
        return InjectionResult(True, 'clipboard')

    def watch_system_events(self, on_cancel: Callable[[], Any]) -> bool:
        try:
            from PySide6 import QtCore, QtDBus

            desktop = self

            class Watcher(QtCore.QObject):
                def changed(self, source: str, active: bool) -> None:
                    if active:
                        desktop._locked.add(source)
                        on_cancel()
                    else:
                        desktop._locked.discard(source)

                @QtCore.Slot(bool)
                def screenChanged(self, active: bool) -> None:
                    self.changed('screen', active)

                @QtCore.Slot(bool)
                def sleepChanged(self, active: bool) -> None:
                    self.changed('sleep', active)

            self._watcher = Watcher()
            session = QtDBus.QDBusConnection.sessionBus()
            system = QtDBus.QDBusConnection.systemBus()
            results = []
            for service, path in [
                ('org.freedesktop.ScreenSaver', '/org/freedesktop/ScreenSaver'),
                ('org.freedesktop.ScreenSaver', '/ScreenSaver'),
                ('org.gnome.ScreenSaver', '/org/gnome/ScreenSaver'),
            ]:
                results.append(session.connect(
                    service, path, service, 'ActiveChanged', self._watcher,
                    QtCore.SLOT('screenChanged(bool)').encode(),
                ))
            results.append(system.connect(
                'org.freedesktop.login1', '/org/freedesktop/login1',
                'org.freedesktop.login1.Manager', 'PrepareForSleep', self._watcher,
                QtCore.SLOT('sleepChanged(bool)').encode(),
            ))
            return any(results)
        except Exception as exc:
            _log.warning('Desktop lock/sleep notifications unavailable: %s', exc)
            return False

    def run(self, stop: threading.Event) -> None:
        self._stop_timer = self._core.QTimer()
        self._stop_timer.setInterval(100)
        self._stop_timer.timeout.connect(lambda: self.close() if stop.is_set() else None)
        self._stop_timer.start()
        self._app.exec()

    def close(self) -> None:
        if threading.current_thread() is not threading.main_thread():
            self.post(self.close)
            return
        if self._closed:
            return
        if self.overlay is not None:
            self.overlay.close()
        with self._request_lock:
            self._closed = True
            pending = list(self._active)
            while not self._requests.empty():
                pending.append(self._requests.get_nowait())
            for request in pending:
                request.cancelled.set()
                request.error = RuntimeError('The desktop is shutting down.')
                request.done.set()
        self._poll.stop()
        if self._stop_timer is not None:
            self._stop_timer.stop()
        if self.listener is not None:
            self.listener.stop()
            self.listener = None
        self._app.quit()
