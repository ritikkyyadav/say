"""Drive the real packaged window, microphone pipeline and GTK text delivery in CI."""
from __future__ import annotations

import argparse
import json
import os
import select
import signal
import subprocess
import time
import urllib.request
from pathlib import Path

from gui_environment import DesktopEnvironment
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.options import BaseOptions
from smoke_common import ROOT, api, runtime_info, speech_fixture, wait_for, word_error_rate


class Options(BaseOptions):
    @property
    def default_capabilities(self):
        return {'browserName': 'wry'}

    def to_capabilities(self):
        return self._caps


def driver_ready():
    with urllib.request.urlopen('http://127.0.0.1:4444/status', timeout=2) as response:
        return json.load(response).get('value', {}).get('ready', False)


def source_outputs(env: dict[str, str]) -> int:
    value = subprocess.check_output(['pactl', 'list', 'short', 'source-outputs'], env=env, text=True)
    return len(value.strip().splitlines())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--mode', choices=['x11', 'wayland'], required=True)
    parser.add_argument('--force-quit', action='store_true')
    args = parser.parse_args()
    app = args.app.absolute()
    installed = app in {Path('/usr/bin/say-desktop'), Path('/usr/bin/say')}
    if (not app.is_file() or app.name not in {'AppRun', 'say-desktop', 'say'}
            or (not installed and not app.resolve().is_relative_to(ROOT))):
        raise SystemExit('Expected an owned installed or extracted Say package.')
    desktop = DesktopEnvironment(args.mode, args.out)
    if app.name == 'AppRun':
        desktop.env['APPDIR'] = str(app.parent)
    driver = None
    owner_fd = None
    engine_fd = None
    info = None
    output = desktop.folder
    wave = speech_fixture(output)
    try:
        desktop.start()
        wait_for(driver_ready, description='native Tauri WebDriver')
        options = Options()
        options.set_capability('tauri:options', {'application': str(app)})
        driver = webdriver.Remote(command_executor='http://127.0.0.1:4444', options=options)
        info = wait_for(lambda: runtime_info(desktop.env), description='private engine runtime')
        wait_for(lambda: api(info, '/health')['state'] == 'ready', timeout=90,
                 description='bundled offline model ready')
        engine_exe = Path(f'/proc/{info["pid"]}/exe').resolve()
        manifest = json.loads((engine_exe.parent / '_internal' / 'linux-build.json').read_text())
        assert manifest['source_commit'] == os.environ['GITHUB_SHA']
        status_path = Path(f'/proc/{info["pid"]}/status')
        parent = int(next(line.split()[1] for line in status_path.read_text().splitlines()
                          if line.startswith('PPid:')))
        parent_exe = Path(f'/proc/{parent}/exe').resolve()
        assert parent_exe.name in {'say-desktop', 'say'} and parent > 1
        owner_fd = os.pidfd_open(parent)
        engine_fd = os.pidfd_open(info['pid'])
        engine_exit = select.poll()
        engine_exit.register(engine_fd, select.POLLIN)
        platform = api(info, '/platform')
        assert platform['session'] == args.mode
        assert platform['automatic_insertion'] == (args.mode == 'x11')
        wait_for(lambda: driver.find_elements(By.CSS_SELECTOR, '[data-testid="linux-setup-finish"]'),
                 description='native Linux setup screen')
        driver.find_element(By.CSS_SELECTOR, '[data-testid="linux-setup-finish"]').click()
        wait_for(lambda: driver.find_elements(By.CSS_SELECTOR, '[data-testid="linux-recorder"]'),
                 description='native recording controls')
        assert driver.find_element(By.CSS_SELECTOR, '.topbar').text.strip() == 'Home'
        driver.save_screenshot(str(output / 'native-ready.png'))
        wait_for(lambda: source_outputs(desktop.env) == 0, description='microphone closed while idle')
        driver.execute_script("Object.defineProperty(navigator,'onLine',{value:false,configurable:true}); window.dispatchEvent(new Event('offline'));")
        target = output / 'gtk-text.txt'
        if args.mode == 'x11':
            assert api(info, '/dictation')['shortcut_started']
            desktop.spawn(['/usr/bin/python3', str(ROOT / 'linux' / 'gtk_probe.py'),
                           '--out', str(target), '--seed', 'Clipboard before dictation'], 'gtk-target')
            wait_for(target.exists, description='GTK input target')
            def target_window():
                found = subprocess.run(['xdotool', 'search', '--onlyvisible', '--name',
                                        '^Say Linux input probe$'], env=desktop.env,
                                       capture_output=True, text=True)
                ids = found.stdout.splitlines()
                return ids[-1] if ids else None
            window = wait_for(target_window, description='GTK target window')
            subprocess.run(['xdotool', 'windowactivate', '--sync', window], env=desktop.env, check=True)
            subprocess.run(['xdotool', 'keydown', 'ctrl', 'keydown', 'space'], env=desktop.env, check=True)
        else:
            driver.find_element(By.CSS_SELECTOR, '[data-testid="linux-record-toggle"]').click()
        wait_for(lambda: api(info, '/dictation')['state'] == 'recording', description='real capture')
        wait_for(lambda: source_outputs(desktop.env) > 0, description='actual PulseAudio capture stream')
        playback = desktop.spawn(['paplay', '--device=say_test', str(wave)], 'speech-playback')
        time.sleep(1)
        if args.mode == 'x11':
            subprocess.run(['scrot', '--overwrite', str(output / 'native-listening.png')],
                           env=desktop.env, check=True)
        assert playback.wait(timeout=45) == 0
        if args.mode == 'x11':
            subprocess.run(['xdotool', 'keyup', 'space', 'keyup', 'ctrl'], env=desktop.env, check=True)
        else:
            driver.find_element(By.CSS_SELECTOR, '[data-testid="linux-record-toggle"]').click()
        def complete():
            current = api(info, '/dictation')
            return current if current['state'] == 'ready' and current.get('latest') else None
        completed = wait_for(complete, timeout=90, description='persisted complete dictation')
        latest = completed['latest']
        score = word_error_rate(latest['text'])
        assert latest['text'].strip() and score <= 0.45, f'Synthetic dictation WER: {score:.3f}'
        if args.mode == 'wayland':
            assert latest['delivery'] == 'manual'
            driver.find_element(By.CSS_SELECTOR, '[data-testid="linux-copy-result"]').click()
            wait_for(lambda: driver.find_element(By.CSS_SELECTOR, '[data-testid="linux-copy-result"]').text == 'Copied',
                     description='trusted Copy action')
            driver.save_screenshot(str(output / 'native-manual-copy.png'))
            desktop.spawn(['/usr/bin/python3', str(ROOT / 'linux' / 'gtk_probe.py'),
                           '--out', str(target), '--paste-on-map'], 'gtk-target')
        else:
            assert latest['delivery'] == 'inserted'
        wait_for(lambda: target.exists() and target.read_text().strip(), description='GTK received text')
        assert target.read_text().strip() == latest['text'].strip(), 'Delivered text was truncated or changed.'
        if args.mode == 'x11':
            clipboard = subprocess.check_output(['xclip', '-o', '-selection', 'clipboard'],
                                               env=desktop.env, text=True)
            assert clipboard == 'Clipboard before dictation', 'Clipboard was not restored.'
        wait_for(lambda: source_outputs(desktop.env) == 0, description='microphone closed after delivery')
        second = subprocess.run([str(app)], env=desktop.env, capture_output=True, timeout=15)
        assert second.returncode == 0 and runtime_info(desktop.env)['pid'] == info['pid']
        driver.save_screenshot(str(output / 'native-result.png'))
        count_before_cancel = len(api(info, '/transcripts'))
        if args.mode == 'x11':
            subprocess.run(['xdotool', 'key', 'ctrl+space'], env=desktop.env, check=True)
        else:
            driver.find_element(By.CSS_SELECTOR, '[data-testid="linux-record-toggle"]').click()
        wait_for(lambda: api(info, '/dictation')['state'] == 'recording', description='cancel capture')
        if args.mode == 'x11':
            subprocess.run(['xdotool', 'key', 'Escape'], env=desktop.env, check=True)
        else:
            driver.find_element(By.CSS_SELECTOR, '[data-testid="linux-cancel"]').click()
        wait_for(lambda: api(info, '/dictation')['state'] == 'ready', description='cancel completed')
        assert len(api(info, '/transcripts')) == count_before_cancel
        wait_for(lambda: source_outputs(desktop.env) == 0, description='cancel closed microphone')
        if args.force_quit:
            signal.pidfd_send_signal(owner_fd, signal.SIGKILL)
        else:
            driver.close()
        wait_for(lambda: not (Path(desktop.env['XDG_DATA_HOME']) / 'Say' / 'runtime.json').exists(),
                 timeout=15, description='owned engine cleanup after window exit')
        wait_for(lambda: bool(engine_exit.poll(0)), timeout=15, description='owned engine process exit')
        report = {'mode': args.mode, 'source_commit': manifest['source_commit'],
                  'version': manifest['version'], 'text': latest['text'], 'delivery': latest['delivery'],
                  'synthetic_word_error_rate': score, 'real_virtual_microphone': True,
                  'gtk_received_complete_text': True, 'idle_microphone_closed': True,
                  'single_instance': True, 'owner_exit_cleanup': True, 'cancel_discards_audio': True,
                  'force_quit': args.force_quit, 'offline_local_controls': True}
        (output / 'gui-smoke.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(report))
    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass
        if owner_fd is not None:
            try:
                signal.pidfd_send_signal(owner_fd, signal.SIGTERM)
            except ProcessLookupError:
                pass
            os.close(owner_fd)
        if engine_fd is not None:
            os.close(engine_fd)
        desktop.close()
        if info is not None:
            for log in output.rglob('*.log'):
                value = log.read_text(errors='replace')
                log.write_text(value.replace(info['token'], '[local-runtime-token]'))


if __name__ == '__main__':
    main()
