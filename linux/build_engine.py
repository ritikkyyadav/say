"""Build the native Linux engine with an offline English model and provenance."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import shutil
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / 'say' / 'build' / 'linux'
MODEL_ID = 'moonshine/medium-streaming-en'


def git(*arguments: str) -> str:
    return subprocess.check_output(['git', '-C', str(ROOT), *arguments], text=True).strip()


def digest(path: Path) -> str:
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def native_arch() -> str:
    if sys.platform != 'linux' or platform.machine() not in {'x86_64', 'aarch64'}:
        raise SystemExit('Build on a native x86_64 or aarch64 Linux runner, not on macOS.')
    return platform.machine()


def assert_elf(path: Path, arch: str) -> None:
    with path.open('rb') as source:
        header = source.read(20)
    expected = {'x86_64': 62, 'aarch64': 183}[arch]
    if header[:6] != b'\x7fELF\x02\x01' or struct.unpack('<H', header[18:20])[0] != expected:
        raise RuntimeError(f'{path.name} is not a native 64-bit {arch} ELF executable.')


def prepare() -> dict:
    import moonshine_voice as mv

    arch = native_arch()
    BUILD.mkdir(parents=True, exist_ok=True)
    if not BUILD.resolve().is_relative_to(ROOT):
        raise RuntimeError('The Linux output directory escapes the source checkout.')
    resolved, model_arch = mv.get_model_for_language(
        'en', mv.ModelArch.MEDIUM_STREAMING, cache_root=BUILD / 'model-cache',
    )
    if model_arch != mv.ModelArch.MEDIUM_STREAMING:
        raise RuntimeError('The SDK resolved a different model architecture.')
    model = BUILD / 'models' / 'medium-streaming-en'
    if model.exists():
        shutil.rmtree(model)
    shutil.copytree(resolved, model)
    files = [{'path': str(path.relative_to(model)), 'bytes': path.stat().st_size,
              'sha256': digest(path)} for path in sorted(model.rglob('*')) if path.is_file()]
    if sum(item['bytes'] for item in files) < 100_000_000:
        raise RuntimeError('The bundled Medium model is incomplete.')
    metadata = json.loads((ROOT / 'desktop' / 'package.json').read_text())
    dirty = bool(git('status', '--porcelain', '--untracked-files=no'))
    if os.environ.get('CI') == 'true' and dirty:
        raise RuntimeError('CI must build committed source without tracked modifications.')
    manifest = {
        'product': 'Say', 'version': metadata['version'], 'platform': 'linux', 'arch': arch,
        'source_commit': git('rev-parse', 'HEAD'), 'source_dirty': dirty, 'frozen': True,
        'source_date_epoch': int(git('show', '-s', '--format=%ct', 'HEAD')),
        'python': platform.python_version(), 'glibc': list(platform.libc_ver()),
        'model': {'id': MODEL_ID, 'files': files},
        'packages': sorted([
            {'name': dist.metadata['Name'], 'version': dist.version}
            for dist in importlib.metadata.distributions() if dist.metadata['Name']
        ], key=lambda item: item['name'].lower()),
        'ci': {key: os.environ.get(key) for key in
               ('GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT', 'ImageOS', 'ImageVersion')},
    }
    (BUILD / 'linux-build.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare-only', action='store_true')
    args = parser.parse_args()
    manifest = prepare()
    if not args.prepare_only:
        env = dict(os.environ, SOURCE_DATE_EPOCH=str(manifest['source_date_epoch']))
        subprocess.run([
            sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
            '--distpath', str(ROOT / 'say' / 'dist' / 'linux'),
            '--workpath', str(BUILD / 'pyinstaller'), str(ROOT / 'linux' / 'SayEngine.spec'),
        ], cwd=ROOT, env=env, check=True)
        engine = ROOT / 'say' / 'dist' / 'linux' / 'SayEngine' / 'SayEngine'
        assert_elf(engine, manifest['arch'])
        print(f'Built native {manifest["arch"]} engine: {engine}')
    print(json.dumps({'version': manifest['version'], 'arch': manifest['arch'],
                      'source_commit': manifest['source_commit'], 'model': MODEL_ID}))


if __name__ == '__main__':
    main()
