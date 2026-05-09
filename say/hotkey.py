# hotkey.py - Configurable global hotkey listener with tap-toggle.
#
# Two gestures share one hotkey (default: ctrl+space):
#   - Hold the combo (>= TAP_WINDOW): push-to-talk. Recording ends on
#     release.
#   - Tap the combo (< TAP_WINDOW press+release): toggles hands-free.
#     First tap starts recording; recording continues after release.
#     A second tap of the combo stops and transcribes.
#
# Esc cancels at any active state.

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from enum import Enum

import config
from logging_setup import get_logger

_log = get_logger('hotkey')

# A press+release of the full combo within this window counts as a tap
# (and toggles hands-free); longer is push-to-talk.
TAP_WINDOW = 0.30


class _Mode(Enum):
    IDLE = 'idle'
    PRESSING = 'pressing'      # combo held, recording, tap-vs-hold undecided
    HANDS_FREE = 'hands_free'  # recording, no keys held; next tap stops


_ALIASES = {
    'control': 'ctrl',
    'ctrl_l': 'ctrl',
    'ctrl_r': 'ctrl',
    'key.ctrl_l': 'ctrl',
    'key.ctrl_r': 'ctrl',
    'command': 'cmd',
    'cmd_l': 'cmd',
    'cmd_r': 'cmd',
    'key.cmd': 'cmd',
    'option': 'alt',
    'opt': 'alt',
    'alt_l': 'alt',
    'alt_r': 'alt',
    'key.alt': 'alt',
    'key.alt_l': 'alt',
    'key.alt_r': 'alt',
    'shift_l': 'shift',
    'shift_r': 'shift',
    'key.shift': 'shift',
    'key.shift_l': 'shift',
    'key.shift_r': 'shift',
    'return': 'enter',
    'key.enter': 'enter',
    'escape': 'esc',
    'key.esc': 'esc',
    'key.space': 'space',
    'key.tab': 'tab',
    'delete': 'backspace',
    'key.backspace': 'backspace',
}

_SPECIAL_KEYS = {
    'ctrl',
    'cmd',
    'alt',
    'shift',
    'space',
    'enter',
    'esc',
    'tab',
    'backspace',
    *{f'f{i}' for i in range(1, 21)},
}


def _normalise_token(token: str) -> str:
    token = token.strip().lower()
    if token.startswith('<') and token.endswith('>'):
        token = token[1:-1].strip()
    return _ALIASES.get(token, token)


def parse_hotkey(hotkey: str) -> tuple[str, ...]:
    """Parse and validate a hotkey string into canonical token names."""
    if not isinstance(hotkey, str) or not hotkey.strip():
        raise ValueError('HOTKEY must be a non-empty string.')

    tokens: list[str] = []
    seen: set[str] = set()
    for raw_token in hotkey.split('+'):
        token = _normalise_token(raw_token)
        if not token:
            raise ValueError(f'Invalid empty token in hotkey: {hotkey!r}')
        if token not in _SPECIAL_KEYS and len(token) != 1:
            raise ValueError(f'Unsupported hotkey token: {raw_token!r}')
        if token in seen:
            raise ValueError(f'Duplicate hotkey token: {raw_token!r}')
        tokens.append(token)
        seen.add(token)

    return tuple(tokens)


def format_hotkey(tokens: tuple[str, ...] | list[str] | set[str]) -> str:
    """Return a user-facing hotkey label."""
    return '+'.join(tokens)


class HotkeyListener:
    """Global push-to-talk listener for a configurable key combination."""

    def __init__(
        self,
        on_press_cb: Callable[[], None],
        on_release_cb: Callable[[], None],
        hotkey: str | None = None,
        on_cancel_cb: Callable[[], None] | None = None,
    ):
        from pynput import keyboard

        self._keyboard = keyboard
        self._target_tokens = parse_hotkey(hotkey or config.HOTKEY)
        self._target_keys = self._to_pynput_keys(self._target_tokens)
        self._held_keys: set[object] = set()
        self._mode = _Mode.IDLE
        self._combo_satisfied = False  # all target keys currently held
        self._press_time = 0.0
        self._lock = threading.Lock()
        self._on_press_cb = on_press_cb
        self._on_release_cb = on_release_cb
        self._on_cancel_cb = on_cancel_cb
        self._listener = None

    @property
    def hotkey_label(self) -> str:
        return f'{format_hotkey(self._target_tokens)} (hold or tap)'

    def _to_pynput_keys(self, tokens: tuple[str, ...]) -> set[object]:
        keyboard = self._keyboard
        mapping = {
            'ctrl': keyboard.Key.ctrl_l,
            'cmd': keyboard.Key.cmd,
            'alt': keyboard.Key.alt,
            'shift': keyboard.Key.shift,
            'space': keyboard.Key.space,
            'enter': keyboard.Key.enter,
            'esc': keyboard.Key.esc,
            'tab': keyboard.Key.tab,
            'backspace': keyboard.Key.backspace,
        }
        for i in range(1, 21):
            key_name = f'f{i}'
            if hasattr(keyboard.Key, key_name):
                mapping[key_name] = getattr(keyboard.Key, key_name)

        keys: set[object] = set()
        for token in tokens:
            if token in mapping:
                keys.add(mapping[token])
            else:
                keys.add(keyboard.KeyCode.from_char(token))
        return keys

    def _normalise_key(self, key):
        keyboard = self._keyboard
        normalised = {}
        for source, target in (
            ('ctrl_r', 'ctrl_l'),
            ('alt_l', 'alt'),
            ('alt_r', 'alt'),
            ('shift_l', 'shift'),
            ('shift_r', 'shift'),
            ('cmd_l', 'cmd'),
            ('cmd_r', 'cmd'),
        ):
            if hasattr(keyboard.Key, source) and hasattr(keyboard.Key, target):
                normalised[getattr(keyboard.Key, source)] = getattr(keyboard.Key, target)
        return normalised.get(key, key)

    @staticmethod
    def _fire(callback: Callable[[], None]) -> None:
        threading.Thread(target=callback, daemon=True).start()

    def _on_press(self, key) -> None:
        key = self._normalise_key(key)
        with self._lock:
            self._held_keys.add(key)
            satisfied_now = self._target_keys.issubset(self._held_keys)

            # Edge: combo just transitioned from "not all held" to "all held".
            if satisfied_now and not self._combo_satisfied:
                self._combo_satisfied = True

                if self._mode == _Mode.IDLE:
                    self._mode = _Mode.PRESSING
                    self._press_time = time.time()
                    self._fire(self._on_press_cb)

                elif self._mode == _Mode.HANDS_FREE:
                    # Second tap during hands-free → stop & transcribe.
                    # Fire stop now; the upcoming release of these same
                    # keys will then do nothing (combo no longer
                    # satisfied AND mode already IDLE).
                    self._mode = _Mode.IDLE
                    self._fire(self._on_release_cb)

    def _on_release(self, key) -> None:
        key = self._normalise_key(key)
        with self._lock:
            if key == self._keyboard.Key.esc and self._on_cancel_cb is not None:
                if self._mode != _Mode.IDLE:
                    self._mode = _Mode.IDLE
                    self._combo_satisfied = False
                    self._fire(self._on_cancel_cb)
                return

            self._held_keys.discard(key)
            satisfied_now = self._target_keys.issubset(self._held_keys)

            # Edge: combo just transitioned from "all held" to "not all held".
            if not satisfied_now and self._combo_satisfied:
                self._combo_satisfied = False

                if self._mode == _Mode.PRESSING:
                    held_for = time.time() - self._press_time
                    if held_for < TAP_WINDOW:
                        # Quick tap → switch to hands-free, keep recording.
                        self._mode = _Mode.HANDS_FREE
                    else:
                        # Hold → push-to-talk done; stop & transcribe.
                        self._mode = _Mode.IDLE
                        self._fire(self._on_release_cb)
                # HANDS_FREE: stop already fired on the second-tap press;
                # this release is the trailing edge of that tap and is a
                # no-op. IDLE: nothing to do.

    def start(self, timeout: float = 2.0) -> bool:
        """Start listening. Returns True on success.

        `timeout` is accepted for parity with FnHotkeyListener; pynput
        listeners come up synchronously so it is unused.
        """
        self._listener = self._keyboard.Listener(
            on_press=self._on_press,
            on_release=self._on_release,
        )
        try:
            self._listener.start()
        except Exception:
            _log.exception('pynput listener failed to start')
            return False
        return True

    def join(self) -> None:
        if self._listener is not None:
            self._listener.join()

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
        with self._lock:
            self._held_keys.clear()
            self._mode = _Mode.IDLE
            self._combo_satisfied = False

    def force_idle(self) -> None:
        """Snap to idle without firing callbacks. Used when recording is
        ended out-of-band (overlay buttons, controller error). Idempotent."""
        with self._lock:
            self._held_keys.clear()
            self._mode = _Mode.IDLE
            self._combo_satisfied = False

    def current_state(self) -> str:
        with self._lock:
            if self._mode == _Mode.PRESSING:
                return 'ptt'
            if self._mode == _Mode.HANDS_FREE:
                return 'hands_free'
            return 'idle'

    @property
    def is_recording(self) -> bool:
        return self._mode != _Mode.IDLE

    @property
    def mode(self) -> str:
        return self.current_state()
