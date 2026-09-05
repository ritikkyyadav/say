#!/usr/bin/env python3
"""Say menu bar application."""

from __future__ import annotations

import atexit
import os
import subprocess
import threading

import config
import fn_override
import menubar_icon
import model_manager
import overlay_theme
import rumps
import settings
import settings_window
import single_instance
from AppKit import NSMakeSize
from audio import AudioRecorder
from controller import DictationController, DictationEvent, DictationState
from device_watcher import DeviceWatcher
from first_run import run_wizard as run_first_run_wizard
from gestures import GestureBridge
from listener_factory import ListenerUpgrader, make_listener, try_start_fn_listener
from logging_setup import LOG_DIR, configure_logging, get_logger, install_crash_handler
from menubar_icon import IconState
from notifications import notify
from overlay import make_overlay
from owner_watch import OwnerWatchdog, arm_exit_backstop
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
    """macOS menu bar frontend with the Fn hotkey listener."""

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
        self._upgrader = None
        self.overlay = None
        self.device_watcher: DeviceWatcher | None = None
        self.system_events: SystemEventWatcher | None = None
        self.controller = DictationController(
            recorder=self.recorder,
            transcriber=Transcriber(),
            status_callback=self._handle_status,
            logger=print,
        )
        # One place decides what a key press does to the mic and the glyph
        # (see gestures.py). Both are late-bound: the overlay does not exist
        # until the model has loaded, the listener until make_listener returns.
        self._bridge = GestureBridge(
            self.controller,
            self.recorder,
            overlay=lambda: self.overlay,
            listener=lambda: self.listener,
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

    def _setup_overlay(self) -> None:
        """Build the dictation glyph and give it the live microphone tap."""
        self.overlay = make_overlay()
        if self.overlay is not None:
            self.recorder.set_monitor_callback(self.overlay.feed_audio)
            # So POST /overlay can repaint the running glyph instead of
            # waiting for the next launch.
            overlay_theme.register_overlay(self.overlay)

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

    def _setup_listener(self) -> None:
        """Set up the hotkey listener, with auto-fallback to pynput."""
        prefer_legacy = bool(getattr(config, 'PREFER_LEGACY_HOTKEY', False))

        # Ensure/prompt Accessibility trust for the running engine before
        # building the Fn CGEventTap (also needed for CGEvent text injection).
        # Binds the grant to this exact binary; no-op once trusted.
        try:
            from permissions import request_accessibility_trust

            request_accessibility_trust(prompt=not os.environ.get('SAY_CI'))
        except Exception:
            _log.exception('Accessibility trust prompt failed')

        try:
            info = make_listener(
                on_start=self._bridge.on_start,
                on_stop=self._bridge.on_stop,
                on_cancel=self._bridge.on_cancel,
                on_arm=self._bridge.on_arm,
                on_disarm=self._bridge.on_disarm,
                prefer_legacy=prefer_legacy,
            )
        except RuntimeError as exc:
            self.title = TITLE_ERROR
            self._apply_status_icon(IconState.ERROR)
            self.status_item.title = 'Status: Hotkey listener unavailable'
            self.hotkey_item.title = 'Hotkey: unavailable'
            _log.error('Listener factory failed: %s', exc)
            notify('Say', 'Hotkey listener could not start. Check permissions.')
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
                'Say',
                f'Fn key unavailable — using fallback hotkey: {info.label}.',
            )
            self._watch_for_accessibility(prefer_legacy)

    def _watch_for_accessibility(self, prefer_legacy: bool) -> None:
        """Switch to Fn the moment Accessibility is granted, without a restart.

        The grant applies to this already-running process — only the check we
        did at startup is stale. Users otherwise grant it, restart the window,
        and stay on the fallback because the engine outlived the window.
        """
        if prefer_legacy or self._upgrader is not None:
            return

        def build():
            return try_start_fn_listener(
                on_start=self._bridge.on_start,
                on_stop=self._bridge.on_stop,
                on_cancel=self._bridge.on_cancel,
                on_arm=self._bridge.on_arm,
                on_disarm=self._bridge.on_disarm,
            )

        self._upgrader = ListenerUpgrader(
            build,
            # The swap touches menu items, so hop to the main thread.
            lambda info: AppHelper.callAfter(self._adopt_listener, info),
        )
        self._upgrader.start()

    def _adopt_listener(self, info) -> None:
        """Main-thread swap of the active hotkey listener."""
        previous, self.listener = self.listener, info.listener
        if previous is not None:
            try:
                previous.stop()
            except Exception:
                _log.exception('Could not stop the fallback listener after upgrade')

        self.hotkey_item.title = f'Hotkey: {info.label}'
        from server.api import set_listener_state
        set_listener_state(
            kind=info.kind,
            label=info.label,
            fell_back=False,
            fn_error=None,
            started=True,
        )
        if info.kind == 'fn' and getattr(config, 'OVERRIDE_FN_KEY_ACTION', True):
            try:
                fn_override.acquire()
            except Exception:
                _log.exception('Fn-key override failed after upgrade')
        notify('Say', f'Accessibility granted — {info.label} is now active.')

    def _handle_status(self, event: DictationEvent) -> None:
        """Update the menu-bar icon and title from state changes."""
        self.status_item.title = f'Status: {event.message}'

        if event.state == DictationState.RECORDING:
            self.title = TITLE_RECORDING
            self._apply_status_icon(IconState.RECORDING)
        elif event.state == DictationState.PROCESSING:
            self.title = TITLE_PROCESSING
            self._apply_status_icon(IconState.PROCESSING)
        elif event.state == DictationState.ERROR:
            self.title = TITLE_ERROR
            self._apply_status_icon(IconState.ERROR)
        else:
            self.title = TITLE_IDLE
            self._apply_status_icon(IconState.IDLE)

        # The glyph follows the same event, through the same routing the
        # sidecar uses, so the two entry points cannot disagree about it.
        self._bridge.on_status(event)

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
            self._unsquash_status_icon()
        except Exception:
            _log.exception('Could not apply menu-bar icon for %s', state)

    def _unsquash_status_icon(self) -> None:
        """Restore the mark's aspect after rumps forces the icon square.

        rumps' `_nsimage_from_file` hard-codes `setSize_((20, 20))` on every
        status icon. The Say mark is 5:2, so accepting that squashes it into
        an unreadable smear. The status item itself was created with
        `statusItemWithLength_(-1)` — variable width — so a wide image is
        fine once the size is right.

        This reaches past rumps' public API, hence the guard: a rumps
        release that renames `_icon_nsimage` should cost us a correct icon
        size, not the menu bar.
        """
        try:
            image = self._icon_nsimage
            if image is None:
                return
            width, height = menubar_icon.icon_point_size()
            image.setSize_(NSMakeSize(width, height))
            self._nsapp.nsstatusitem.setImage_(image)
        except Exception:
            _log.debug('Could not restore menu-bar icon size', exc_info=True)

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
        if self.overlay is not None:
            self.overlay.close()
        if self.system_events is not None:
            self.system_events.stop()
        if self.device_watcher is not None:
            self.device_watcher.stop()
        if self._upgrader is not None:
            self._upgrader.stop()
        if self.listener is not None:
            self.listener.stop()
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


def _setup_sidecar_ui(controller, recorder, listener, overlay_slot=None) -> bool:
    """Bring up the system watchers in --no-menubar mode.

    The shipped desktop app runs the engine here, not through `SayApp`, so for
    a long time none of this ran in the product: no cancellation on screen-lock
    or sleep, and no recovery when the input device changed mid-recording. Both
    were implemented and simply never constructed outside menu-bar mode.

    Also builds the dictation overlay, which is the other thing that only works
    with a Cocoa loop turning, and drops it into `overlay_slot` so the hotkey
    callbacks wired earlier can reach it.

    Returns True if a Cocoa event loop should now be run — the watchers listen
    for NSNotifications, which only arrive while that loop is turning. On any
    failure this returns False and the caller falls back to the plain sleep
    loop, so a problem here costs the watchers rather than dictation itself.
    """
    try:
        from AppKit import NSApplication, NSApplicationActivationPolicyAccessory

        ns_app = NSApplication.sharedApplication()
        # Accessory: no Dock icon, no menu bar, windows still render.
        ns_app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)

        def on_system_event(name):
            if name in ('screen_locked', 'will_sleep'):
                _log.info('Cancelling any active recording due to %s', name)
                if listener is not None:
                    listener.force_idle()
                controller.cancel_recording()

        watcher = SystemEventWatcher(callback=on_system_event)
        watcher.start()

        device_watcher = DeviceWatcher(
            on_change=lambda dev: controller._on_audio_event(
                'device_changed', f'Default input device changed to {dev or "(none)"}'
            )
        )
        device_watcher.start()

        # Built here rather than at the top of `_run_server_only` because the
        # window and its display link both need this run loop. Failure returns
        # None and dictation carries on without a glyph.
        overlay = make_overlay()
        overlay_theme.register_overlay(overlay)
        if overlay is not None:
            recorder.set_monitor_callback(overlay.feed_audio)
            if overlay_slot is not None:
                overlay_slot[0] = overlay

        # Keep references alive for the process lifetime.
        _run_server_only._ui = (  # type: ignore[attr-defined]
            ns_app, watcher, device_watcher, overlay,
        )
        _log.info('Sidecar UI ready: sleep/lock + device watchers, overlay %s',
                  'active' if overlay is not None else 'off')
        return True
    except Exception:
        _log.exception('Sidecar watcher setup failed; running headless')
        return False


def _run_server_only(owner_pid: int | None = None) -> None:
    """Boot the engine + HTTP API with no menu-bar agent.

    Used by the desktop shell's sidecar mode. The model is loaded and the
    hotkey listener is registered, but no rumps app runs. Exits when the
    process is signalled — or when `owner_pid`, the desktop app that launched
    us, goes away. See `owner_watch` for why the engine has to enforce that
    itself rather than trust the shell to ask.
    """
    import signal
    import time

    from server.api import serve_in_thread

    # The hotkey is wired long before a Cocoa run loop exists to hang a window
    # off, so the overlay is late-bound through this slot: everything below can
    # refer to it, and `_setup_sidecar_ui` fills it in once the loop is up.
    overlay_slot: list = [None]

    # Armed here, before anything slow. The model load alone takes seconds and
    # the hotkey tap is registered right after it, so a quit during boot must be
    # noticed *now* — otherwise the engine finishes arming itself for an app
    # that no longer exists.
    stop = threading.Event()
    watchdog: OwnerWatchdog | None = None
    if owner_pid:
        def _release_what_outlives_us() -> None:
            # The only two things that survive this process if we skip them:
            # the user's globe key would stay stranded on "Do Nothing", and a
            # stale handshake would point the next launch at a dead engine.
            try:
                fn_override.release()
            except Exception:
                _log.exception('Could not restore AppleFnUsageType before the hard exit')
            try:
                from server.runtime import clear_runtime

                clear_runtime()
            except Exception:
                _log.exception('Could not clear runtime.json before the hard exit')

        def _owner_gone() -> None:
            _log.warning(
                'Say (pid %s) exited — shutting the engine down with it.', owner_pid
            )
            stop.set()
            # `stop` asks nicely; this makes sure. See arm_exit_backstop.
            arm_exit_backstop(cleanup=_release_what_outlives_us)

        watchdog = OwnerWatchdog(owner_pid, _owner_gone)
        if not watchdog.start():
            watchdog = None

    def _owner_left_during_boot(stage: str) -> bool:
        if not stop.is_set():
            return False
        _log.info('Say quit during startup (%s) — aborting engine boot.', stage)
        return True

    recorder = AudioRecorder()
    controller = DictationController(
        recorder=recorder,
        transcriber=Transcriber(),
        logger=print,
    )
    # Everything a key press does to the mic and the glyph lives in one place
    # (gestures.py), so a refused recording can never leave the mark up and the
    # microphone open — the failure that read as "the overlay disappears".
    listener_info = None
    bridge = GestureBridge(
        controller,
        recorder,
        overlay=lambda: overlay_slot[0],
        listener=lambda: listener_info.listener if listener_info is not None else None,
    )
    controller.status_callback = bridge.on_status
    # Bind the live engine so /models/active swaps the model without a relaunch.
    model_manager.get_manager().register_engine(controller)

    try:
        controller.transcriber.predownload()
    except Exception:
        _log.exception('Model predownload raised')
    if not controller.load_model():
        _log.error('Model load failed; server will still serve cached transcripts')

    # Before the microphone probe below: opening the mic at all — even for the
    # 50ms the TCC prompt needs — after the user has quit is the exact
    # behaviour this whole change exists to prevent.
    if _owner_left_during_boot('after the model load'):
        return

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

    # Register this engine in Privacy → Accessibility and prompt if it is not
    # yet trusted (e.g. right after an ad-hoc rebuild changed our cdhash). This
    # binds the grant to the running engine identity directly — avoiding the
    # nested-bundle misattribution where the parent shell gets credited.
    # Harmless no-op once trusted.
    try:
        from permissions import request_accessibility_trust

        if not request_accessibility_trust(prompt=not os.environ.get('SAY_CI')):
            _log.warning(
                'Accessibility not yet trusted for the engine — system prompt '
                'shown. Grant Say in System Settings > Privacy & Security '
                '> Accessibility, then quit and reopen Say.'
            )
    except Exception:
        _log.exception('Accessibility trust prompt failed')

    # No runtime.json cleanup here on purpose: `serve_in_thread` has not run
    # yet, so this process has not written it. Clearing it would delete the
    # handshake of whatever engine *did*.
    if _owner_left_during_boot('before the hotkey listener'):
        return

    from server.api import set_listener_state

    fn_error: str | None = None
    try:
        _log.info('Starting hotkey listener (sidecar mode)…')
        listener_info = make_listener(
            on_start=bridge.on_start,
            on_stop=bridge.on_stop,
            on_cancel=bridge.on_cancel,
            on_arm=bridge.on_arm,
            on_disarm=bridge.on_disarm,
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
                    'Say. Falling back to ctrl+space, which still '
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

    # If we fell back because Accessibility was not granted yet, watch for the
    # grant and switch to Fn in place. Users grant the permission and then
    # restart the *window* — which this engine outlives — so without this the
    # fallback hotkey sticks until the engine itself is killed, with nothing
    # on screen explaining why.
    upgrader: ListenerUpgrader | None = None
    if (
        listener_info is not None
        and listener_info.fell_back
        and listener_info.kind == 'legacy'
        and not bool(getattr(config, 'PREFER_LEGACY_HOTKEY', False))
    ):
        def _build_fn_listener():
            return try_start_fn_listener(
                on_start=bridge.on_start,
                on_stop=bridge.on_stop,
                on_cancel=bridge.on_cancel,
                on_arm=bridge.on_arm,
                on_disarm=bridge.on_disarm,
            )

        def _adopt_fn_listener(info) -> None:
            nonlocal listener_info, fn_override_acquired
            previous = listener_info.listener if listener_info is not None else None
            listener_info = info
            if previous is not None:
                try:
                    previous.stop()
                except Exception:
                    _log.exception('Could not stop the fallback listener after upgrade')
            set_listener_state(
                kind=info.kind,
                label=info.label,
                fell_back=False,
                fn_error=None,
                started=True,
            )
            if not fn_override_acquired and getattr(config, 'OVERRIDE_FN_KEY_ACTION', True):
                try:
                    fn_override.acquire()
                    fn_override_acquired = True
                except Exception:
                    _log.exception('Fn-key override failed after upgrade')

        upgrader = ListenerUpgrader(_build_fn_listener, _adopt_fn_listener)
        upgrader.start()
        _log.info('Watching for Accessibility so the Fn hotkey can start without a restart.')

    serve_in_thread(log_level='warning', owner_pid=owner_pid)

    def _on_signal(signum, _frame):
        _log.info('Received signal %s — shutting down server', signum)
        stop.set()

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    listener = listener_info.listener if listener_info is not None else None

    _shutdown_done = threading.Event()

    def _shutdown() -> None:
        """Release everything this process took. Safe to call more than once.

        Must be able to run from *inside* the Cocoa run loop: PyObjC's
        `runEventLoop()` terminates the process rather than returning, so any
        cleanup placed after it never executes. Leaving `fn_override` unreleased
        would strand the user's globe key on "Do Nothing" after they quit.
        """
        if _shutdown_done.is_set():
            return
        _shutdown_done.set()
        bridge.overlay_do('close')
        if watchdog is not None:
            watchdog.stop()
        if upgrader is not None:
            upgrader.stop()
        try:
            # Read through listener_info, not the `listener` captured above:
            # an upgrade may have replaced it since.
            active = listener_info.listener if listener_info is not None else None
            if active is not None:
                active.stop()
        except Exception:
            pass
        try:
            recorder.shutdown()
        except Exception:
            pass
        if fn_override_acquired:
            try:
                fn_override.release()
            except Exception:
                _log.exception('Failed to restore AppleFnUsageType on shutdown')
        # Clear runtime.json so the desktop shell doesn't try to talk to a dead
        # engine. serve_blocking's `finally` is inside a daemon thread, which is
        # killed before it runs — so do it explicitly here.
        try:
            from server.runtime import clear_runtime
            clear_runtime()
        except Exception:
            _log.exception('Failed to clear runtime.json on shutdown')

    # Belt and braces: also run on normal interpreter exit.
    atexit.register(_shutdown)

    if _setup_sidecar_ui(controller, recorder, listener, overlay_slot):
        # A Cocoa run loop is required for the sleep/lock and device watchers
        # to receive their NSNotifications. Python signal handlers do not run while that
        # loop blocks in C, so a repeating timer polls the stop flag from inside
        # Cocoa — and tears everything down *before* stopping the loop, because
        # stopping it ends the process.
        def _check_stop(_timer=None):
            if stop.is_set():
                _shutdown()
                AppHelper.stopEventLoop()

        try:
            from Foundation import NSTimer

            NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
                0.25, True, _check_stop
            )
            AppHelper.runEventLoop(installInterrupt=False)
        except Exception:
            _log.exception('Cocoa event loop failed; falling back to headless wait')
            while not stop.is_set():
                time.sleep(0.5)
    else:
        while not stop.is_set():
            time.sleep(0.5)

    _shutdown()


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
    parser.add_argument(
        '--owner-pid',
        type=int,
        default=None,
        help=(
            'Exit when this process does. The desktop shell passes its own pid '
            'so a quit — or a Force Quit, which it cannot catch — takes the '
            'engine down with it instead of orphaning it onto launchd.'
        ),
    )
    args = parser.parse_args()

    if args.no_menubar:
        # Server-only mode: no rumps, no menu bar.
        _run_server_only(owner_pid=args.owner_pid)
        return

    if not single_instance.acquire():
        # Another Say instance is already running; bail quietly.
        sys.exit(0)

    if args.server:
        _maybe_run_server_alongside_menubar()

    SayApp().run()


if __name__ == '__main__':
    main()
