"""Inspect and exercise owned Debian/AppImage artifacts on disposable native CI."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from build_engine import assert_elf, native_arch
from smoke_common import ROOT


def exactly_one(paths) -> Path:
    selected = [path for path in paths if path.is_file()]
    if len(selected) != 1:
        raise RuntimeError(f'Expected one package artifact, found {selected}.')
    return selected[0].resolve()


def sha256(path: Path) -> str:
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def main() -> None:
    if sys.platform != 'linux' or os.environ.get('CI') != 'true':
        raise SystemExit('Package installation tests require a disposable native Linux CI runner.')
    arch = native_arch()
    bundles = ROOT / 'desktop' / 'src-tauri' / 'target' / 'release' / 'bundle'
    deb = exactly_one((bundles / 'deb').glob('*.deb'))
    image = exactly_one((bundles / 'appimage').glob('*.AppImage'))
    output = ROOT / 'say' / 'dist' / 'linux-smoke'
    output.mkdir(parents=True, exist_ok=True)
    unpacked = output / 'deb-extracted'
    unpacked.mkdir(exist_ok=True)
    subprocess.run(['dpkg-deb', '-x', str(deb), str(unpacked)], check=True)
    deb_engine = exactly_one(unpacked.rglob('SayEngine'))
    image.chmod(image.stat().st_mode | 0o111)
    extracted = output / 'appimage-extracted'
    extracted.mkdir(exist_ok=True)
    with (output / 'appimage-extract.log').open('w') as stream:
        subprocess.run([str(image), '--appimage-extract'], cwd=extracted,
                       stdout=stream, stderr=subprocess.STDOUT, check=True)
    appdir = extracted / 'squashfs-root'
    image_engine = exactly_one(appdir.rglob('SayEngine'))
    metadata = []
    for label, engine in [('deb', deb_engine), ('appimage', image_engine)]:
        assert_elf(engine, arch)
        manifest = json.loads((engine.parent / '_internal' / 'linux-build.json').read_text())
        assert manifest['source_commit'] == os.environ['GITHUB_SHA']
        model = engine.parent / '_internal' / 'models' / 'medium-streaming-en'
        for item in manifest['model']['files']:
            path = (model / item['path']).resolve()
            assert path.is_relative_to(model.resolve()) and path.is_file()
            assert path.stat().st_size == item['bytes'] and sha256(path) == item['sha256']
        command = [sys.executable, str(ROOT / 'linux' / 'cli_smoke.py'), '--engine', str(engine),
                   '--out', str(output / f'cli-{label}')]
        if label == 'appimage':
            command.append('--test-cpp')
        subprocess.run(command, check=True)
        metadata.append({'format': label, 'engine_sha256': sha256(engine),
                         'bundled_model_hashes_verified': True})
    subprocess.run(['sudo', 'apt-get', 'install', '-y', '--no-install-recommends', str(deb)], check=True)
    package = subprocess.check_output(['dpkg-deb', '-f', str(deb), 'Package'], text=True).strip()
    files = subprocess.check_output(['dpkg', '-L', package], text=True).splitlines()
    installed = exactly_one(Path(path) for path in files
                            if Path(path).parent == Path('/usr/bin')
                            and Path(path).name in {'say-desktop', 'say'})
    assert_elf(installed, arch)
    assert (appdir / 'AppRun').is_file()
    for label, app in [('deb', installed), ('appimage', appdir / 'AppRun')]:
        for mode in ('x11', 'wayland'):
            command = ['dbus-run-session', '--', 'xvfb-run', '-a', '-s', '-screen 0 1280x900x24',
                       sys.executable, str(ROOT / 'linux' / 'gui_smoke.py'),
                       '--app', str(app), '--mode', mode,
                       '--out', str(output / f'gui-{label}-{mode}')]
            if label == 'appimage' and mode == 'wayland':
                command.append('--force-quit')
            subprocess.run(command, check=True)
    report = {'arch': arch, 'source_commit': os.environ['GITHUB_SHA'],
              'packages': [{'name': path.name, 'bytes': path.stat().st_size,
                            'sha256': sha256(path)} for path in (deb, image)],
              'native_engines': metadata, 'matrix': ['deb/x11', 'deb/wayland',
                                                   'appimage/x11', 'appimage/wayland']}
    (output / 'package-smoke.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
