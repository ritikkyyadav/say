"""Linux engine entry point; no Cocoa or menu-bar imports."""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import signal
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path
from typing import Any


def build_info() -> dict[str, Any]:
    base = Path(getattr(sys, '_MEIPASS', Path(__file__).parent.parent))
    manifest = base / 'linux-build.json'
    if manifest.is_file():
        return json.loads(manifest.read_text(encoding='utf-8'))
    if getattr(sys, 'frozen', False):
        raise RuntimeError('The packaged engine is missing its build manifest.')
    try:
        revision = subprocess.check_output(
            ['git', '-C', str(Path(__file__).parent.parent), 'rev-parse', 'HEAD'],
            text=True, stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        revision = 'unknown'
    return {'version': 'development', 'source_commit': revision, 'frozen': False}


def read_audio_wav(path: Path) -> Any:
    import numpy as np

    with wave.open(str(path), 'rb') as source:
        if (source.getnchannels(), source.getsampwidth(), source.getframerate()) != (1, 2, 16_000):
            raise ValueError('Expected a 16 kHz mono PCM16 WAV file.')
        if source.getnframes() > 16_000 * 1800:
            raise ValueError('Audio file exceeds the 30-minute batch limit.')
        audio = source.readframes(source.getnframes())
    return np.frombuffer(audio, dtype='<i2').astype('float32') / 32768.0


def configure_model(model: str | None, language: str | None) -> Any:
    import config
    import languages
    import model_catalog
    import settings
    from platform_support import supports_backend
    from transcriber import Transcriber
    from validation import validate_or_raise

    settings.apply_to_config()
    if model:
        config.MODEL = model
    if language is not None:
        config.LANGUAGE = languages.normalise(language)
    validate_or_raise()
    info = model_catalog.get(config.MODEL)
    if info is not None and not supports_backend(info.backend):
        raise ValueError(f'{info.name} is unavailable on this platform. Select a supported CPU model.')
    return Transcriber(model_path=config.MODEL)


def clear_own_runtime() -> None:
    from server.runtime import clear_runtime, read_runtime

    info = read_runtime()
    if isinstance(info, dict) and info.get('pid') == os.getpid():
        clear_runtime()


def arm_parent_death(owner_pid: int | None) -> None:
    if not owner_pid or not sys.platform.startswith('linux') or os.getppid() != owner_pid:
        return
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, signal.SIGTERM, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), 'Could not arm parent-death signal')
    if os.getppid() != owner_pid:
        raise RuntimeError('The desktop exited during engine startup.')


class LinuxControls:
    def __init__(self, controller: Any, bridge: Any, desktop: Any, stop: threading.Event) -> None:
        self.controller = controller
        self.bridge = bridge
        self.desktop = desktop
        self.stop = stop
        self._lock = threading.Lock()

    def reset_listener(self) -> None:
        if self.desktop.listener is not None:
            self.desktop.listener.force_idle()

    def __call__(self, action: str) -> bool:
        if self.stop.is_set() or not self._lock.acquire(blocking=False):
            return False
        try:
            if action == 'start':
                if self.desktop._locked or not self.controller.can_start_recording():
                    return False
                return bool(self.controller.start_recording(mode='manual'))
            if action in {'stop', 'cancel'} and self.controller._recording:
                self.reset_listener()
                if action == 'stop':
                    self.controller.stop_recording()
                else:
                    self.controller.cancel_recording(reason='user')
                return True
            return False
        finally:
            self._lock.release()

    def cancel_for_system(self) -> None:
        def cancel() -> None:
            with self._lock:
                self.controller.cancel_recording(reason='system')
                self.bridge.on_disarm()
                self.reset_listener()
        threading.Thread(target=cancel, name='say-system-cancel', daemon=True).start()

    def hotkey_start(self) -> None:
        if self.stop.is_set() or self.desktop._locked:
            self.bridge.on_disarm()
            self.reset_listener()
            return
        if self.controller._recording and self.controller._recording_mode == 'manual':
            self('stop')
            return
        if not self._lock.acquire(blocking=False):
            if not self.controller._recording:
                self.bridge.on_disarm()
            return
        try:
            if self.controller.can_start_recording():
                self.bridge.on_start()
            elif not self.controller._recording:
                self.bridge.on_disarm()
                self.reset_listener()
        finally:
            self._lock.release()

    def shortcut_started(self) -> bool:
        listener = self.desktop.listener
        native = getattr(listener, '_listener', None)
        return bool(native is not None and native.is_alive())


def wait_for_api(thread: threading.Thread, stop: threading.Event, timeout: float = 10.0) -> None:
    import urllib.request

    from server.runtime import read_runtime

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not stop.is_set():
        if not thread.is_alive():
            raise RuntimeError('The local API could not start; inspect the engine log.')
        info = read_runtime()
        if isinstance(info, dict) and info.get('pid') == os.getpid():
            try:
                url = f"http://127.0.0.1:{int(info['port'])}/health"
                with urllib.request.urlopen(url, timeout=0.5) as response:
                    if response.status == 200:
                        return
            except (OSError, ValueError):
                pass
        stop.wait(0.05)
    raise RuntimeError('The local API did not become ready in time.')


def run_desktop(model: str | None, owner_pid: int | None) -> int:
    if not sys.platform.startswith('linux'):
        raise RuntimeError('The Linux desktop entry point requires Linux.')
    from platform_support import linux_session

    session = linux_session()
    if session == 'headless':
        raise RuntimeError('A desktop session is required. Use --transcribe-file for batch audio.')
    from audio import AudioRecorder
    from controller import DictationController
    from device_watcher import DeviceWatcher
    from gestures import GestureBridge
    from linux_desktop import LinuxDesktop
    from logging_setup import configure_logging, get_logger, install_crash_handler
    from model_manager import get_manager
    from overlay_theme import register_overlay
    from owner_watch import OwnerWatchdog, arm_exit_backstop
    from platform_paths import application_data_dir, ensure_private_directory
    from server.api import serve_in_thread, set_listener_state
    from server.linux_api import register_linux_controls
    from single_instance import acquire, release

    ensure_private_directory(application_data_dir())
    configure_logging()
    install_crash_handler()
    log = get_logger('linux-app')
    if not acquire(strict=True):
        return 0
    stop = threading.Event()
    watchdog: Any = None
    desktop: Any = None
    recorder: Any = None
    controller: Any = None
    devices: Any = None

    def request_stop(*_: Any) -> None:
        stop.set()
        arm_exit_backstop(cleanup=clear_own_runtime)

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    try:
        arm_parent_death(owner_pid)
        if owner_pid:
            watchdog = OwnerWatchdog(owner_pid, request_stop)
            watchdog.start()
        transcriber = configure_model(model, None)
        desktop = LinuxDesktop(session)
        recorder = AudioRecorder()
        controller = DictationController(
            recorder, transcriber, logger=None,
            injector=lambda text: desktop.inject(text, expected_app=controller._target_app),
            frontmost=desktop.frontmost_app, clipboard_writer=lambda text: False,
            manual_delivery=session != 'x11', require_focus=session == 'x11',
        )
        bridge = GestureBridge(controller, recorder, overlay=lambda: desktop.overlay,
                               listener=lambda: desktop.listener)
        def on_status(event: Any) -> None:
            bridge.on_status(event)
            if (event.state.value in {'ready', 'error'} and not controller._recording
                    and desktop.listener is not None):
                desktop.listener.force_idle()

        controller.status_callback = on_status
        get_manager().register_engine(controller)
        if desktop.overlay is not None:
            register_overlay(desktop.overlay)
            recorder.set_monitor_callback(desktop.overlay.feed_audio)
        transcriber.predownload()
        controller.load_model()
        if stop.is_set():
            return 0
        controls = LinuxControls(controller, bridge, desktop, stop)
        thread = serve_in_thread(
            owner_pid=owner_pid,
            configure_app=lambda app: register_linux_controls(
                app, controller, controls, controls.shortcut_started,
            ),
        )
        wait_for_api(thread, stop)
        callbacks = bridge.listener_callbacks()
        def stop_from_key() -> None:
            controls('stop')

        def cancel_from_key() -> None:
            controls('cancel')

        callbacks['on_start'] = controls.hotkey_start
        callbacks['on_stop'] = stop_from_key
        callbacks['on_cancel'] = cancel_from_key
        hotkey = os.environ.get('SAY_HOTKEY')
        started = desktop.start_listener(callbacks, hotkey=hotkey)
        set_listener_state(
            kind='x11' if session == 'x11' else 'manual',
            label=(desktop.listener.hotkey_label if started else 'Record here; paste manually'),
            fell_back=False, fn_error=desktop.listener_error, started=started,
        )
        desktop.watch_system_events(controls.cancel_for_system)
        devices = DeviceWatcher(lambda name: controller._on_audio_event('device_changed', name))
        devices.start()
        desktop.run(stop)
        return 0
    except Exception:
        log.exception('Linux engine startup or runtime failed')
        raise
    finally:
        stop.set()
        arm_exit_backstop(cleanup=clear_own_runtime)
        if controller is not None:
            controller.cancel_recording(reason='system')
            controller._cancel_idle_unload()
        if recorder is not None:
            recorder.shutdown()
        if devices is not None:
            devices.stop()
        if desktop is not None:
            desktop.close()
        if watchdog is not None:
            watchdog.stop()
        clear_own_runtime()
        release()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Say on-device Linux dictation engine')
    parser.add_argument('--no-menubar', action='store_true')
    parser.add_argument('--server', action='store_true')
    parser.add_argument('--owner-pid', type=int)
    parser.add_argument('--model')
    parser.add_argument('--language')
    parser.add_argument('--output', type=Path)
    commands = parser.add_mutually_exclusive_group()
    commands.add_argument('--transcribe-file', type=Path)
    commands.add_argument('--predownload', action='store_true')
    commands.add_argument('--version-json', action='store_true')
    args = parser.parse_args(argv)
    if args.owner_pid is not None and args.owner_pid <= 1:
        parser.error('--owner-pid must name a desktop process, not PID 0 or 1')
    try:
        if args.version_json:
            print(json.dumps(build_info()))
            return 0
        if args.predownload or args.transcribe_file:
            import config
            import postprocess

            transcriber = configure_model(args.model, args.language)
            transcriber.predownload()
            if args.predownload:
                result = {'model': config.MODEL, 'downloaded': True}
            else:
                audio = read_audio_wav(args.transcribe_file)
                started = time.monotonic()
                transcriber.load()
                text = postprocess.apply(transcriber.transcribe(audio))
                result = {
                    'text': text, 'model': config.MODEL,
                    'audio_seconds': len(audio) / 16_000,
                    'inference_seconds': time.monotonic() - started,
                }
                if not text:
                    raise RuntimeError('The decoder returned no speech.')
            payload = json.dumps(result, ensure_ascii=False) + '\n'
            if args.output:
                args.output.write_text(payload, encoding='utf-8')
            sys.stdout.write(payload)
            return 0
        return run_desktop(args.model, args.owner_pid)
    except Exception as exc:
        print(f'Say: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
