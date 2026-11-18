import queue
import sys
import threading
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import linux_desktop
from linux_desktop import LinuxDesktop, _Request
from linux_overlay import light_ink, qt_silhouette
from waveform import BAND_COUNT, GlyphGeometry


def bare(session='x11'):
    desktop = LinuxDesktop.__new__(LinuxDesktop)
    desktop.session = session
    desktop._closed = False
    desktop._locked = set()
    desktop._active = []
    desktop._requests = queue.Queue()
    desktop._request_lock = threading.Lock()
    return desktop


def test_wayland_never_attempts_background_global_insertion():
    desktop = bare('wayland')
    assert not desktop.inject('Private words.').success
    assert not desktop.copy_to_clipboard('Private words.')
    assert desktop.frontmost_app() is None


def test_cancelled_main_thread_request_cannot_inject_later():
    desktop = bare()
    action = Mock()
    request = _Request(action)
    request.cancelled.set()
    desktop._requests.put(request)
    desktop._drain()
    action.assert_not_called()
    assert request.done.is_set()
    assert request.error is not None


def test_locked_desktop_refuses_manual_and_shortcut_recording():
    from linux_app import LinuxControls

    controller = Mock()
    desktop = SimpleNamespace(_locked={'screen'}, listener=Mock())
    bridge = Mock()
    controls = LinuxControls(controller, bridge, desktop, threading.Event())
    assert not controls('start')
    controls.hotkey_start()
    controller.start_recording.assert_not_called()
    bridge.on_start.assert_not_called()
    bridge.on_disarm.assert_called_once()
    desktop.listener.force_idle.assert_called_once()


def test_system_cancel_disarms_audio_after_serialized_start():
    from linux_app import LinuxControls

    finished = threading.Event()
    controller = Mock()
    bridge = Mock()
    desktop = SimpleNamespace(_locked={'sleep'}, listener=Mock())
    desktop.listener.force_idle.side_effect = finished.set
    controls = LinuxControls(controller, bridge, desktop, threading.Event())
    controls.cancel_for_system()
    assert finished.wait(1)
    controller.cancel_recording.assert_called_once_with(reason='system')
    bridge.on_disarm.assert_called_once()


def test_qt_glyph_preserves_shared_geometry_upright():
    geometry = GlyphGeometry()
    points = qt_silhouette([0.8] * BAND_COUNT, geometry, 1.0)
    assert points[0] == (7.0, geometry.height + 7)
    assert points[-1] == (geometry.width + 7, geometry.height + 7)
    assert min(x for x, y in points) >= 7
    assert max(x for x, y in points) <= geometry.width + 7
    assert min(y for x, y in points) >= 7
    assert max(y for x, y in points) <= geometry.height + 7
    assert points[0][1] > min(y for x, y in points)


def test_cli_refuses_an_unsupported_model_before_download(monkeypatch):
    import config
    import platform_support
    import pytest
    import settings
    from linux_app import configure_model

    monkeypatch.setattr(config, 'MODEL', config.MODEL)
    monkeypatch.setattr(settings, 'apply_to_config', lambda: None)
    monkeypatch.setattr(platform_support, 'supports_backend', lambda backend: False)
    with pytest.raises(ValueError, match='unavailable on this platform'):
        configure_model('mlx-community/whisper-small-mlx', None)


def test_overlay_explicit_contrast_overrides_desktop_palette():
    assert light_ink('light', False)
    assert not light_ink('dark', True)
    assert light_ink('auto', True)
    assert not light_ink('system', False)


def test_default_microphone_supports_sounddevice_indexable_pair(monkeypatch):
    from device_watcher import DeviceWatcher

    class InputOutputPair:
        def __getitem__(self, index):
            return (3, 8)[index]
    module = ModuleType('sounddevice')
    module.default = SimpleNamespace(device=InputOutputPair())
    module.query_devices = Mock(return_value={'name': 'USB input'})
    monkeypatch.setitem(sys.modules, 'sounddevice', module)
    assert DeviceWatcher._current_default_input() == 'USB input'
    module.query_devices.assert_called_once_with(3)


class Mime:
    def __init__(self, data=None):
        self.values = dict(data or {})
    def formats(self):
        return list(self.values)
    def data(self, name):
        return SimpleNamespace(data=lambda: self.values.get(name, b''))
    def setData(self, name, data):
        self.values[name] = bytes(data)
    def setText(self, text):
        self.values['text/plain'] = text.encode('utf-8')


class Clipboard:
    def __init__(self, data):
        self.mime = Mime(data)
        self.owned = False
    def mimeData(self):
        return self.mime
    def setMimeData(self, mime):
        self.mime = mime
        self.owned = True
    def ownsClipboard(self):
        return self.owned


def paste_environment(monkeypatch, *, during_settle=None, held=False):
    desktop = bare()
    original = {'text/plain': b'old', 'text/html': b'<b>old</b>', 'image/png': b'png bytes'}
    clipboard = Clipboard(original)
    events = []
    class Keyboard:
        def press(self, key):
            events.append(('press', key))
        def release(self, key):
            events.append(('release', key))
    class Loop:
        def quit(self):
            pass
        def exec(self):
            if during_settle:
                during_settle(clipboard)
    desktop._core = SimpleNamespace(
        QMimeData=Mime, QEventLoop=Loop,
        QTimer=SimpleNamespace(singleShot=lambda delay, callback: callback()),
    )
    desktop._app = SimpleNamespace(clipboard=lambda: clipboard)
    monkeypatch.setattr(linux_desktop, 'x11_context', lambda: ('Editor', held))
    module = ModuleType('pynput')
    module.keyboard = SimpleNamespace(Controller=Keyboard, Key=SimpleNamespace(ctrl='ctrl', shift='shift'))
    monkeypatch.setitem(sys.modules, 'pynput', module)
    return desktop, clipboard, events, original


def test_x11_paste_restores_all_mime_formats(monkeypatch):
    desktop, clipboard, events, original = paste_environment(monkeypatch)
    result = desktop._paste('New words.', 'Editor', threading.Event())
    assert result.success
    assert clipboard.mime.values == original
    assert events == [('press', 'ctrl'), ('press', 'v'), ('release', 'v'), ('release', 'ctrl')]


def test_restore_never_overwrites_a_new_user_copy(monkeypatch):
    def user_copy(clipboard):
        clipboard.mime = Mime({'text/plain': b'new user copy'})
        clipboard.owned = False
    desktop, clipboard, events, original = paste_environment(monkeypatch, during_settle=user_copy)
    assert desktop._paste('Dictation.', 'Editor', threading.Event()).success
    assert clipboard.mime.values == {'text/plain': b'new user copy'}


def test_held_modifiers_refuse_without_touching_clipboard(monkeypatch):
    desktop, clipboard, events, original = paste_environment(monkeypatch, held=True)
    assert not desktop._paste('Dictation.', 'Editor', threading.Event()).success
    assert clipboard.mime.values == original
    assert not events


def test_oversized_clipboard_is_not_destroyed(monkeypatch):
    desktop, clipboard, events, original = paste_environment(monkeypatch)
    monkeypatch.setattr(linux_desktop, '_MAX_CLIPBOARD_BYTES', 1)
    assert not desktop._paste('Dictation.', 'Editor', threading.Event()).success
    assert clipboard.mime.values == original
    assert not events
