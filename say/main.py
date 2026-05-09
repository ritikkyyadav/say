#!/usr/bin/env python3
"""Terminal/debug entrypoint for Say."""

from __future__ import annotations

import signal
import sys
import threading

import settings
import single_instance
from controller import DictationController
from device_watcher import DeviceWatcher
from listener_factory import make_listener
from logging_setup import configure_logging, install_crash_handler
from permissions import check_environment
from validation import validate_config

configure_logging()
install_crash_handler()
settings.apply_to_config()


BANNER = """
============================================================
 Say
 On-device push-to-talk dictation for macOS
 100% local after model download; no audio is written to disk
============================================================
"""


def _print_startup_checks() -> None:
    for check in check_environment():
        marker = 'OK' if check.ok else 'WARN'
        print(f'  {marker:4} {check.name}: {check.message}')


def main() -> int:
    print(BANNER)

    if not single_instance.acquire():
        print('[!] Another Say instance is already running. Exiting.')
        return 0

    errors = validate_config()
    if errors:
        print('[config error]')
        for error in errors:
            print(f'  - {error}')
        return 2

    print('Environment:')
    _print_startup_checks()
    print()

    try:
        from audio import AudioRecorder
        from transcriber import Transcriber
    except Exception as exc:
        print(f'[startup error] Missing runtime dependency: {exc}')
        print('Install dependencies with: pip install -r requirements.txt')
        return 2

    controller = DictationController(
        recorder=AudioRecorder(),
        transcriber=Transcriber(),
        logger=print,
    )

    if not controller.load_model():
        return 1

    stop_event = threading.Event()
    listener_holder = {'listener': None}

    def stop() -> None:
        stop_event.set()
        listener_obj = listener_holder['listener']
        if listener_obj is not None:
            listener_obj.stop()

    signal.signal(signal.SIGINT, lambda *_: stop())
    signal.signal(signal.SIGTERM, lambda *_: stop())

    def on_start():
        listener_obj = listener_holder['listener']
        mode = listener_obj.mode if listener_obj is not None else 'ptt'
        controller.start_recording(mode=mode)

    try:
        info = make_listener(
            on_start=on_start,
            on_stop=controller.stop_recording,
            on_cancel=lambda: (controller.cancel_recording(), stop()),
        )
    except RuntimeError as exc:
        print(f'[✗] {exc}')
        return 1

    listener_holder['listener'] = info.listener

    device_watcher = DeviceWatcher(
        on_change=lambda dev: controller._on_audio_event(
            'device_changed',
            f'Default input device changed to {dev or "(none)"}',
        )
    )
    device_watcher.start()

    print()
    if info.fell_back:
        print(f'[⚠] Fn unavailable — falling back to {info.label}.')
    print(f'Ready. Hotkey: {info.label}. Esc or Ctrl+C to quit.')

    # Keep the main thread alive until stopped
    try:
        stop_event.wait()
    except KeyboardInterrupt:
        pass

    device_watcher.stop()
    info.listener.stop()
    try:
        controller.recorder.shutdown()
    except Exception:
        pass
    single_instance.release()
    print('Say stopped.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
