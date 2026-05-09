"""macOS system-event watchers for Say.

Subscribes to:
  - `com.apple.screenIsLocked` / `com.apple.screenIsUnlocked` from
    NSDistributedNotificationCenter.
  - `NSWorkspaceWillSleepNotification` /
    `NSWorkspaceDidWakeNotification` from NSWorkspace.

When any of these fire, the registered callback is invoked with one of:
    'screen_locked', 'screen_unlocked', 'will_sleep', 'did_wake'.

The callback runs on the AppKit main thread (PyObjC default for
notifications), so callees that touch shared state should not block.
"""

from __future__ import annotations

from collections.abc import Callable

import objc
from Foundation import NSObject
from logging_setup import get_logger

_log = get_logger('system_events')

EventCallback = Callable[[str], None]


class _Observer(NSObject):
    """ObjC observer that bridges notifications to a Python callback."""

    def initWithCallback_(self, callback):
        self = objc.super(_Observer, self).init()
        if self is None:
            return None
        self._callback = callback
        return self

    @objc.typedSelector(b'v@:@')
    def screenLocked_(self, _note):
        self._fire('screen_locked')

    @objc.typedSelector(b'v@:@')
    def screenUnlocked_(self, _note):
        self._fire('screen_unlocked')

    @objc.typedSelector(b'v@:@')
    def willSleep_(self, _note):
        self._fire('will_sleep')

    @objc.typedSelector(b'v@:@')
    def didWake_(self, _note):
        self._fire('did_wake')

    @objc.python_method
    def _fire(self, name: str) -> None:
        try:
            self._callback(name)
        except Exception:
            _log.exception('System-event callback raised for %s', name)


class SystemEventWatcher:
    """Subscribes to lock/unlock and sleep/wake notifications."""

    def __init__(self, callback: EventCallback):
        self._callback = callback
        self._observer = None
        self._registered = False

    def start(self) -> None:
        if self._registered:
            return
        try:
            from AppKit import (
                NSDistributedNotificationCenter,
                NSWorkspace,
            )
        except Exception as exc:
            _log.warning('AppKit unavailable: %s', exc)
            return

        self._observer = _Observer.alloc().initWithCallback_(self._callback)

        dnc = NSDistributedNotificationCenter.defaultCenter()
        dnc.addObserver_selector_name_object_(
            self._observer,
            objc.selector(self._observer.screenLocked_, signature=b'v@:@'),
            'com.apple.screenIsLocked',
            None,
        )
        dnc.addObserver_selector_name_object_(
            self._observer,
            objc.selector(self._observer.screenUnlocked_, signature=b'v@:@'),
            'com.apple.screenIsUnlocked',
            None,
        )

        wnc = NSWorkspace.sharedWorkspace().notificationCenter()
        wnc.addObserver_selector_name_object_(
            self._observer,
            objc.selector(self._observer.willSleep_, signature=b'v@:@'),
            'NSWorkspaceWillSleepNotification',
            None,
        )
        wnc.addObserver_selector_name_object_(
            self._observer,
            objc.selector(self._observer.didWake_, signature=b'v@:@'),
            'NSWorkspaceDidWakeNotification',
            None,
        )

        self._registered = True
        _log.info('System-event watcher registered')

    def stop(self) -> None:
        if not self._registered:
            return
        try:
            from AppKit import (
                NSDistributedNotificationCenter,
                NSWorkspace,
            )
            NSDistributedNotificationCenter.defaultCenter().removeObserver_(
                self._observer
            )
            NSWorkspace.sharedWorkspace().notificationCenter().removeObserver_(
                self._observer
            )
        except Exception:
            _log.exception('Failed to remove notification observers')
        self._registered = False
        self._observer = None
