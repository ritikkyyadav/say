"""Disposable display/audio services for owned native Linux CI sessions."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from smoke_common import ROOT, isolated_env, wait_for


class DesktopEnvironment:
    def __init__(self, mode: str, folder: Path):
        self.folder = folder.resolve()
        if (sys.platform != 'linux' or os.environ.get('CI') != 'true'
                or not self.folder.is_relative_to(ROOT) or mode not in {'x11', 'wayland'}):
            raise RuntimeError('These disposable desktop services run only inside native CI artifacts.')
        self.mode = mode
        self.folder.mkdir(parents=True, exist_ok=True)
        self.env = isolated_env(self.folder / 'isolated')
        self.env['LIBGL_ALWAYS_SOFTWARE'] = '1'
        self.env['WEBKIT_DISABLE_DMABUF_RENDERER'] = '1'
        self.processes: list[subprocess.Popen] = []
        self.logs = []

    def spawn(self, arguments: list[str], name: str, env=None) -> subprocess.Popen:
        stream = (self.folder / f'{name}.log').open('w')
        self.logs.append(stream)
        child = subprocess.Popen(arguments, env=self.env if env is None else env,
                                 stdout=stream, stderr=subprocess.STDOUT)
        self.processes.append(child)
        return child

    def start(self) -> None:
        self.spawn(['openbox', '--sm-disable'], 'openbox')
        self.spawn(['xcompmgr', '-a'], 'compositor')
        pulse = Path(self.env['XDG_RUNTIME_DIR']) / 'pulse' / 'native'
        self.env['PULSE_SERVER'] = f'unix:{pulse}'
        (Path(self.env['HOME']) / '.asoundrc').write_text(
            'pcm.!default { type pulse }\nctl.!default { type pulse }\n',
        )
        subprocess.run(['pulseaudio', '--start', '--exit-idle-time=-1',
                        f'--log-target=file:{self.folder / "pulse.log"}'],
                       env=self.env, check=True)
        wait_for(lambda: pulse.exists(), description='private PulseAudio socket')
        for arguments in [
            ['load-module', 'module-null-sink', 'sink_name=say_test', 'rate=16000',
             'channels=1', 'channel_map=mono'],
            ['set-default-source', 'say_test.monitor'],
            ['set-default-sink', 'say_test'],
            ['set-source-mute', 'say_test.monitor', '0'],
        ]:
            subprocess.run(['pactl', *arguments], env=self.env, check=True, capture_output=True)
        self.env['PULSE_SOURCE'] = 'say_test.monitor'
        if self.mode == 'wayland':
            self.spawn(['weston', '--backend=x11-backend.so', '--socket=say-test-wayland',
                        '--width=1280', '--height=900', '--idle-time=0', '--no-config'], 'weston')
            wait_for(lambda: (Path(self.env['XDG_RUNTIME_DIR']) / 'say-test-wayland').exists(),
                     description='real Weston Wayland socket')
            self.env.update(WAYLAND_DISPLAY='say-test-wayland', XDG_SESSION_TYPE='wayland',
                            QT_QPA_PLATFORM='wayland', GDK_BACKEND='wayland')
        else:
            self.env.pop('WAYLAND_DISPLAY', None)
            self.env.update(XDG_SESSION_TYPE='x11', QT_QPA_PLATFORM='xcb', GDK_BACKEND='x11')
        native = shutil.which('WebKitWebDriver')
        if not native:
            raise RuntimeError('The native WebKitWebDriver is not installed.')
        self.spawn(['tauri-driver', '--native-driver', native], 'tauri-driver')
        time.sleep(0.4)

    def close(self) -> None:
        for child in reversed(self.processes):
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
        subprocess.run(['pactl', 'exit'], env=self.env, capture_output=True)
        for stream in self.logs:
            stream.close()
