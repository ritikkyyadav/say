"""Exercise real Qt MIME and lock-notification contracts on the native runner."""
from __future__ import annotations

import sys
import time
from pathlib import Path

from PySide6 import QtCore, QtDBus

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'say'))
from linux_desktop import LinuxDesktop


def wait_for(app: QtCore.QCoreApplication, predicate) -> None:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError('Qt did not deliver the desktop lock notification.')


def main() -> None:
    app = QtCore.QCoreApplication(['say-qt-contract-probe'])
    original = {'text/plain': b'old text', 'text/html': b'<b>old text</b>',
                'application/octet-stream': b'\x00\x01\xff'}
    mime = QtCore.QMimeData()
    for name, value in original.items():
        mime.setData(name, value)
    restored = {name: mime.data(name).data() for name in mime.formats()}
    assert restored == original, 'The real Qt MIME snapshot changed clipboard bytes.'

    bus = QtDBus.QDBusConnection.sessionBus()
    assert bus.isConnected(), 'Run this probe with dbus-run-session.'
    assert bus.registerService('org.freedesktop.ScreenSaver')
    desktop = LinuxDesktop.__new__(LinuxDesktop)
    desktop._locked = set()
    cancellations: list[bool] = []
    assert desktop.watch_system_events(lambda: cancellations.append(True))
    for active in (True, False):
        signal = QtDBus.QDBusMessage.createSignal(
            '/org/freedesktop/ScreenSaver', 'org.freedesktop.ScreenSaver', 'ActiveChanged',
        )
        signal.setArguments([active])
        assert bus.send(signal)
        wait_for(app, lambda: ('screen' in desktop._locked) == active)
    assert cancellations == [True], 'The lock signal did not cancel exactly once.'
    bus.unregisterService('org.freedesktop.ScreenSaver')
    print('Native Qt MIME bytes, D-Bus slots, lock and unlock passed.')


if __name__ == '__main__':
    main()
