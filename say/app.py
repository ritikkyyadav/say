#!/usr/bin/env python3
"""Say menu bar application with Wispr Flow-style overlay."""

from __future__ import annotations

import os
import subprocess
import threading

import atexit

import config
import fn_override
import menubar_icon
import model_manager
import rumps
import settings
import settings_window
import single_instance
from audio import AudioRecorder
from controller import DictationController, DictationEvent, DictationState
from device_watcher import DeviceWatcher
from first_run import run_wizard as run_first_run_wizard
from listener_factory import make_listener
from logging_setup import LOG_DIR, configure_logging, get_logger, install_crash_handler
from menubar_icon import IconState
from notifications import notify
from overlay import DictationOverlay
from permissions import check_environment, microphone_summary, open_privacy_settings
from PyObjCTools import AppHelper
from system_events import SystemEventWatcher
from transcriber import Transcriber
from validation import validate_config

configure_logging()
install_crash_handler()
settings.apply_to_config()
_log = get_logger('app')


# Title text shown next to the menu-bar icon. Empty for idle so the
# icon alone reads cleanly; a single emoji for active states keeps the
# state recognizable even on tiny / cluttered menu bars.
TITLE_IDLE = ''
TITLE_RECORDING = '🔴'
TITLE_PROCESSING = '⏳'
TITLE_ERROR = '⚠️'


class SayApp(rumps.App):
    """macOS menu bar frontend with Wispr Flow-style overlay and Fn hotkey."""

    def __init__(self):
        super().__init__(TITLE_PROCESSING, quit_button=None)
        self._apply_status_icon(IconState.PROCESSING)

        # Restore any AppleFnUsageType backup left over from a prior
        # crash, then register an atexit cleanup. We do NOT acquire
        # the override here — that happens later, only if the Fn
        # listener actually starts (see `_setup_listener`). With the
        # default ctrl+space hotkey, the user's system defaults stay
        # untouched.
        try:
            fn_override.release()
        except Exception:
            _log.exception('Could not restore AppleFnUsageType from prior run')
        atexit.register(self._safe_release_fn_override)

        self.recorder = AudioRecorder()
        self.listener = None
        self.overlay: DictationOverlay | None = None
        self.device_watcher: DeviceWatcher | None = None
        self.system_events: SystemEventWatcher | None = None
        self.controller = DictationController(
            recorder=self.recorder,
            transcriber=Transcriber(),
            status_callback=self._handle_status,
            logger=print,
        )
        # Let the model manager swap the live transcriber when the user picks
        # a different model from Settings or the desktop UI — no relaunch.
        model_manager.get_manager().register_engine(self.controller)

        self.status_item = rumps.MenuItem('Status: Starting...')
        self.status_item.set_callback(None)
        self.hotkey_item = rumps.MenuItem('Hotkey: Fn (hold) / Fn+Space (toggle)')
        self.hotkey_item.set_callback(None)
        self.model_item = rumps.MenuItem(f'Model: {config.MODEL.split("/")[-1]}')
        self.model_item.set_callback(None)
        self.inject_item = rumps.MenuItem(f'Injection: {config.INJECT_METHOD}')
        self.inject_item.set_callback(None)

        self.menu = [
            self.status_item,
            None,
            self.hotkey_item,
            self.model_item,
            self.inject_item,
            None,
            rumps.MenuItem('Preferences…', callback=self._open_preferences, key=','),
            rumps.MenuItem('Open Config File', callback=self._open_config),
            rumps.MenuItem('Environment Check', callback=self._show_environment),
            rumps.MenuItem('List Microphone Devices', callback=self._show_microphones),
            rumps.MenuItem('Test Injection', callback=self._test_injection),
            rumps.MenuItem('Run Diagnostics', callback=self._run_diagnostics),
            rumps.MenuItem('Show Logs', callback=self._show_logs),
            None,
            rumps.MenuItem(
                'Open Microphone Settings',
                callback=lambda _: open_privacy_settings('microphone'),
            ),
            rumps.MenuItem(
                'Open Accessibility Settings',
                callback=lambda _: open_privacy_settings('accessibility'),
            ),
            rumps.MenuItem(
                'Open Input Monitoring Settings',
                callback=lambda _: open_privacy_settings('input-monitoring'),
            ),
            None,
            rumps.MenuItem('Quit Say', callback=self._quit),
        ]

        errors = validate_config()
        if errors:
            self.title = TITLE_ERROR
            self._apply_status_icon(IconState.ERROR)
            self.status_item.title = 'Status: Config error'
            rumps.alert('Say config error', '\n'.join(errors))
            return

        if settings.is_first_run():
            try:
                run_first_run_wizard(
                    alert_fn=rumps.alert,
                    on_finish=settings.mark_first_run_complete,
                )
            except Exception:
                _log.exception('First-run wizard failed')

        threading.Thread(target=self._load_and_listen, daemon=True).start()

    def _load_and_listen(self) -> None:
        try:
            self.controller.transcriber.predownload(
                status_cb=lambda msg: AppHelper.callAfter(
                    self._set_download_status, msg
                ),
            )
        except Exception:
            _log.exception('Model predownload raised')
        if self.controller.load_model():
            AppHelper.callAfter(self._setup_overlay)
            self._setup_listener()
            self._setup_device_watcher()
            AppHelper.callAfter(self._setup_system_events)

    def _setup_system_events(self) -> None:
        """Cancel recordings on screen-lock / sleep so they don't leak."""
        def on_event(name):
            if name in ('screen_locked', 'will_sleep'):
                _log.info('Cancelling any active recording due to %s', name)
                if self.listener is not None:
                    self.listener.force_idle()
                self.controller.cancel_recording()
            else:
                _log.info('System event: %s', name)

        self.system_events = SystemEventWatcher(callback=on_event)
        self.system_events.start()

    def _set_download_status(self, message: str) -> None:
        self.status_item.title = f'Status: {message}'

    def _setup_device_watcher(self) -> None:
        """Watch for default-input-device changes (e.g. AirPods unplugged)."""
        def on_change(new_device):
            self.controller._on_audio_event(
                'device_changed',
                f'Default input device changed to {new_device or "(none)"}',
            )

        self.device_watcher = DeviceWatcher(on_change=on_change)
        self.device_watcher.start()

    def _setup_overlay(self) -> None:
        """Create the floating overlay with cancel/finish buttons."""
        self.overlay = DictationOverlay(
            on_cancel=self._on_overlay_cancel,
            on_finish=self._on_overlay_finish,
        )

        # Connect audio recorder amplitude to overlay waveform
        self.recorder.set_amplitude_callback(self._on_amplitude)

        # Now that the model is loaded, show the persistent idle dot.
        self.overlay.show_idle_dot()

    def _setup_listener(self) -> None:
        """Set up the hotkey listener, with auto-fallback to pynput."""
        prefer_legacy = bool(getattr(config, 'PREFER_LEGACY_HOTKEY', False))
        try:
            info = make_listener(
                on_start=self._on_recording_start,
                on_stop=self._on_recording_stop,
                on_cancel=self._on_recording_cancel,
                prefer_legacy=prefer_legacy,
            )
        except RuntimeError as exc:
            self.title = TITLE_ERROR
            self._apply_status_icon(IconState.ERROR)
            self.status_item.title = 'Status: Hotkey listener unavailable'
            self.hotkey_item.title = 'Hotkey: unavailable'
            _log.error('Listener factory failed: %s', exc)
            notify('Oceanus', 'Hotkey listener could not start. Check permissions.')
            from server.api import set_listener_state
            set_listener_state(
                kind=None,
                label=None,
                fell_back=False,
                fn_error=f'Hotkey listener crashed: {exc}',
                started=False,
            )
            return

        self.listener = info.listener
        self.hotkey_item.title = f'Hotkey: {info.label}'

        # Publish listener state so the desktop UI's /listener endpoint
        # reflects reality in menu-bar (--server) mode too — not just the
        # --no-menubar sidecar path. Without this the UI shows a false
        # "Hotkey unavailable" banner even though the Fn key works.
        from server.api import set_listener_state
        set_listener_state(
            kind=info.kind,
            label=info.label,
            fell_back=info.fell_back,
            fn_error=None,
            started=True,
        )

        # Only neutralize AppleFnUsageType if the Fn listener actually
        # won. With the legacy hotkey there's no Fn key to fight over.
        if info.kind == 'fn' and getattr(config, 'OVERRIDE_FN_KEY_ACTION', True):
            try:
                fn_override.acquire()
            except Exception:
                _log.exception('Fn-key override failed; continuing anyway')

        if info.fell_back:
            notify(
                'Oceanus',
                f'Fn key unavailable — using fallback hotkey: {info.label}.',
            )

    def _on_recording_start(self) -> None:
        """Called when hotkey triggers recording start."""
        mode = self.listener.mode if self.listener else 'ptt'
        self.controller.start_recording(mode=mode)

    def _on_recording_stop(self) -> None:
        """Called when hotkey triggers recording stop."""
        self.controller.stop_recording()

    def _on_recording_cancel(self) -> None:
        """Called when recording is cancelled (Esc or overlay cancel)."""
        self.controller.cancel_recording()

    def _on_overlay_cancel(self) -> None:
        """Cancel button pressed on overlay."""
        if self.listener is not None:
            self.listener.force_idle()
        self.controller.cancel_recording()

    def _on_overlay_finish(self) -> None:
        """Finish button pressed on overlay."""
        if self.listener is not None:
            self.listener.force_idle()
        self.controller.stop_recording()

    def _on_amplitude(self, amplitude: float) -> None:
        """Forward amplitude from audio callback to overlay."""
        if self.overlay is not None:
            self.overlay.push_amplitude(amplitude)

    def _handle_status(self, event: DictationEvent) -> None:
        """Update menu bar icon and overlay based on state changes."""
        self.status_item.title = f'Status: {event.message}'

        if event.state == DictationState.RECORDING:
            self.title = TITLE_RECORDING
            self._apply_status_icon(IconState.RECORDING)
            if self.overlay:
                mode = event.mode or 'ptt'
                AppHelper.callAfter(self.overlay.show_recording, mode)

        elif event.state == DictationState.PROCESSING:
            self.title = TITLE_PROCESSING
            self._apply_status_icon(IconState.PROCESSING)
            if self.overlay:
                AppHelper.callAfter(self.overlay.show_processing)

        elif event.state == DictationState.ERROR:
            self.title = TITLE_ERROR
            self._apply_status_icon(IconState.ERROR)
            if self.overlay:
                AppHelper.callAfter(self.overlay.hide)

        else:
            self.title = TITLE_IDLE
            self._apply_status_icon(IconState.IDLE)
            if self.overlay:
                AppHelper.callAfter(self.overlay.show_idle_dot)

    # ── Menu-bar icon application ────────────────────────────────

    def _apply_status_icon(self, state: IconState) -> None:
        """Set the menu-bar icon (with template flag) for `state`.

        Falls back silently to the title-only display if the cached PNG
        can't be written (e.g. read-only home dir, missing permissions).
        """
        try:
            path = menubar_icon.icon_path_for(state)
            if path is None:
                return
            self.icon = path
            self.template = menubar_icon.is_template(state)
        except Exception:
            _log.exception('Could not apply menu-bar icon for %s', state)

    def _open_preferences(self, _sender) -> None:
        try:
            settings_window.open_settings()
        except Exception:
            _log.exception('Failed to open Preferences')
            rumps.alert(
                'Preferences unavailable',
                'Could not open the Preferences window. '
                'Edit config.py manually as a workaround.',
            )

    def _open_config(self, _sender) -> None:
        config_path = os.path.join(os.path.dirname(__file__), 'config.py')
        subprocess.run(['open', '-e', config_path], check=False)

    def _show_environment(self, _sender) -> None:
        lines = []
        for check in check_environment():
            marker = 'OK' if check.ok else 'MISSING'
            lines.append(f'{marker}: {check.name} - {check.message}')
        rumps.alert('Say environment', '\n'.join(lines))

    def _show_microphones(self, _sender) -> None:
        rumps.alert('Audio devices', microphone_summary())

    def _test_injection(self, _sender) -> None:
        from injector import inject

        result = inject('Say is working ✓')
        if not result.success:
            rumps.alert(
                'Test injection failed',
                result.error or 'Unknown error. Grant Accessibility permission and retry.',
            )

    def _run_diagnostics(self, _sender) -> None:
        """Run env checks + 1s mic sample + warmup, report timings in an alert."""
        import time as _time

        import numpy as _np

        lines: list[str] = []

        for check in check_environment():
            marker = 'OK' if check.ok else 'MISSING'
            lines.append(f'{marker}: {check.name} - {check.message}')
        lines.append('')

        try:
            self.recorder.start()
            _time.sleep(1.0)
            audio = self.recorder.stop()
            if audio.size > 0:
                rms = float(_np.sqrt(_np.mean(audio ** 2)))
                lines.append(f'Mic 1s capture: {audio.size} samples, RMS={rms:.4f}')
            else:
                lines.append('Mic 1s capture: no samples returned')
        except Exception as exc:
            lines.append(f'Mic capture failed: {exc}')
            _log.exception('Diagnostics mic capture failed')

        try:
            t0 = _time.time()
            self.controller.transcriber.load()
            lines.append(f'Model load/warmup: {_time.time() - t0:.2f}s')
        except Exception as exc:
            lines.append(f'Model load failed: {exc}')
            _log.exception('Diagnostics model load failed')

        rumps.alert('Say diagnostics', '\n'.join(lines))

    def _show_logs(self, _sender) -> None:
        subprocess.run(['open', LOG_DIR], check=False)

    def _safe_release_fn_override(self) -> None:
        """atexit-safe wrapper that swallows logging on torn-down logger."""
        try:
            fn_override.release()
        except Exception:
            pass

    def _quit(self, _sender) -> None:
        if self.system_events is not None:
            self.system_events.stop()
        if self.device_watcher is not None:
            self.device_watcher.stop()
        if self.listener is not None:
            self.listener.stop()
        if self.overlay is not None:
            self.overlay.hide()
        try:
            self.recorder.shutdown()
        except Exception:
            pass
        try:
            fn_override.release()
        except Exception:
            _log.exception('Failed to restore AppleFnUsageType on quit')
        single_instance.release()
        rumps.quit_application()


def _trigger_microphone_prompt() -> None:
    """Open the default input device briefly to register Say with TCC.

    macOS only adds an app to Privacy → Microphone *after* the app has
    actually called the audio input API once.  In sidecar mode the
    recorder doesn't run until the user triggers the hotkey — but the
    hotkey can't fire without Input Monitoring, which the user can't
    grant without first seeing Say in the Privacy list, etc.  Breaking
    that chicken-and-egg is what this probe does.
    """
    import sounddevice as sd

    try:
        # Stream the default input for a single short callback then close.
        # 8 kHz mono with a tiny block-size minimises CPU and memory.
        with sd.InputStream(
            samplerate=16000,
            channels=1,
            dtype='float32',
            blocksize=512,
        ) as stream:
            stream.read(512)
        _log.info('Microphone permission probe completed')
    except Exception as exc:
        # OSError on permission denial is expected before the user grants
        # mic access; we still want the side effect (TCC registration).
        _log.info('Microphone probe surfaced TCC: %s', exc)


def _run_server_only() -> None:
    """Boot the engine + HTTP API with no menu-bar agent.

    Used by the desktop shell's sidecar mode. The model is loaded and the
    hotkey listener is registered, but no rumps app runs. Exits when the
    process is signalled.
    """
    import signal
    import time

    from server.api import serve_in_thread

    recorder = AudioRecorder()
    controller = DictationController(
        recorder=recorder,
        transcriber=Transcriber(),
        status_callback=None,
        logger=print,
    )
    # Bind the live engine so /models/active swaps the model without a relaunch.
    model_manager.get_manager().register_engine(controller)

    try:
        controller.transcriber.predownload()
    except Exception:
        _log.exception('Model predownload raised')
    if not controller.load_model():
        _log.error('Model load failed; server will still serve cached transcripts')

    # Trigger the macOS Microphone TCC prompt explicitly.  Just opening
    # an input stream for ~50ms is enough for the system to (a) show the
    # permission dialog the first time and (b) register Say in the
    # Privacy → Microphone list so the user can later toggle it.
    # Without this probe, the user can't grant mic access because the
    # app never appears in the list.
    try:
        _trigger_microphone_prompt()
    except Exception:
        _log.exception('Microphone permission probe failed')

    from server.api import set_listener_state

    listener_info = None
    fn_error: str | None = None
    try:
        _log.info('Starting hotkey listener (sidecar mode)…')
        listener_info = make_listener(
            on_start=lambda: controller.start_recording(
                mode=(listener_info.listener.mode if listener_info else 'ptt')
            ),
            on_stop=controller.stop_recording,
            on_cancel=controller.cancel_recording,
            prefer_legacy=bool(getattr(config, 'PREFER_LEGACY_HOTKEY', False)),
        )
        if listener_info is not None:
            _log.info(
                'Hotkey listener ready: kind=%s label=%s fell_back=%s',
                listener_info.kind,
                listener_info.label,
                listener_info.fell_back,
            )
            if listener_info.fell_back and listener_info.kind == 'legacy':
                fn_error = (
                    'Fn key requires Accessibility permission for '
                    'Oceanus. Falling back to ctrl+space, which still '
                    'needs Input Monitoring permission to receive events.'
                )
        else:
            _log.warning('make_listener returned None — dictation will not respond to hotkey')
            fn_error = 'No hotkey listener could be started.'
    except Exception as exc:
        _log.exception('Hotkey listener could not start in server-only mode')
        fn_error = f'Hotkey listener crashed: {exc}'

    set_listener_state(
        kind=listener_info.kind if listener_info else None,
        label=listener_info.label if listener_info else None,
        fell_back=listener_info.fell_back if listener_info else False,
        fn_error=fn_error,
        started=listener_info is not None,
    )

    # When the Fn listener is the active one, neutralise macOS's default
    # Fn behaviour (Show Emoji / Start Dictation / Change Input Source)
    # so our CGEventTap actually receives Fn events instead of having
    # them swallowed by the system. The acquire is a no-op outside the
    # 'fn' kind, and is restored on graceful exit below.
    fn_override_acquired = False
    if (
        listener_info is not None
        and listener_info.kind == 'fn'
        and getattr(config, 'OVERRIDE_FN_KEY_ACTION', True)
    ):
        try:
            fn_override.acquire()
            fn_override_acquired = True
            _log.info('AppleFnUsageType neutralised so Fn reaches the listener.')
        except Exception:
            _log.exception('Fn-key override failed; Fn presses may be swallowed by macOS')

    serve_in_thread(log_level='warning')

    stop = threading.Event()

    def _on_signal(signum, _frame):
        _log.info('Received signal %s — shutting down server', signum)
        stop.set()

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    while not stop.is_set():
        time.sleep(0.5)

    try:
        if listener_info is not None:
            listener_info.listener.stop()
    except Exception:
        pass
    try:
        recorder.shutdown()
    except Exception:
        pass
    # Restore the user's original AppleFnUsageType.
    if fn_override_acquired:
        try:
            fn_override.release()
        except Exception:
            _log.exception('Failed to restore AppleFnUsageType on shutdown')
    # Clear runtime.json so the desktop shell doesn't try to talk to a dead
    # engine. serve_blocking's `finally` clause is inside a daemon thread,
    # which gets killed before its finally runs when main exits — so do
    # the cleanup explicitly here.
    try:
        from server.runtime import clear_runtime
        clear_runtime()
    except Exception:
        _log.exception('Failed to clear runtime.json on shutdown')


def _maybe_run_server_alongside_menubar() -> None:
    """Start the HTTP API on a background thread next to the rumps app."""
    try:
        from server.api import serve_in_thread

        serve_in_thread(log_level='warning')
    except Exception:
        _log.exception('Could not start HTTP API alongside menu-bar app')


def main() -> None:
    import argparse
    import sys

    parser = argparse.ArgumentParser(prog='say', description='Say dictation engine')
    parser.add_argument(
        '--server',
        action='store_true',
        help='Boot the local HTTP API for the desktop UI in addition to the menu bar.',
    )
    parser.add_argument(
        '--no-menubar',
        action='store_true',
        help='Run server-only, with no menu-bar agent (used by the desktop sidecar).',
    )
    args = parser.parse_args()

    if args.no_menubar:
        # Server-only mode: no rumps, no overlay.
        _run_server_only()
        return

    if not single_instance.acquire():
        # Another Say instance is already running; bail quietly.
        sys.exit(0)

    if args.server:
        _maybe_run_server_alongside_menubar()

    SayApp().run()


if __name__ == '__main__':
    main()
