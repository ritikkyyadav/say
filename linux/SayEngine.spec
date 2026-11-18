from pathlib import Path

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
    copy_metadata,
)

ROOT = Path(SPECPATH).resolve().parent
PREPARED = ROOT / 'say' / 'build' / 'linux'
MODEL = PREPARED / 'models' / 'medium-streaming-en'
MANIFEST = PREPARED / 'linux-build.json'
if not MODEL.is_dir() or not MANIFEST.is_file():
    raise SystemExit('Run python linux/build_engine.py to prepare the bundled model and manifest.')

datas = [(str(MODEL), 'models/medium-streaming-en'), (str(MANIFEST), '.'),
         (str(ROOT / 'LICENSE'), 'licenses/Say')]
assets = ROOT / 'say' / 'assets'
if assets.is_dir():
    datas.append((str(assets), 'assets'))
for package in ('moonshine_voice', 'pywhispercpp'):
    datas += collect_data_files(package, excludes=['**/*.so', '**/*.so.*', '**/*.dylib', '**/*.dll'])
for distribution in ('moonshine-voice', 'pywhispercpp', 'PySide6-Essentials', 'shiboken6'):
    datas += copy_metadata(distribution)

binaries = collect_dynamic_libs('moonshine_voice', search_patterns=['*.so', '*.so.*'])
binaries += collect_dynamic_libs('pywhispercpp', search_patterns=['*.so', '*.so.*'])
hiddenimports = collect_submodules('Xlib') + [
    'pynput.keyboard._xorg', 'pynput.mouse._xorg', 'pynput._util.xorg',
    'PySide6.QtCore', 'PySide6.QtGui', 'PySide6.QtWidgets', 'PySide6.QtDBus',
    'uvicorn.logging', 'uvicorn.loops.asyncio', 'uvicorn.protocols.http.auto',
    'uvicorn.protocols.http.h11_impl', 'uvicorn.protocols.websockets.auto',
    'uvicorn.lifespan.on', '_pywhispercpp',
]
a = Analysis(
    [str(ROOT / 'say' / 'linux_app.py')], pathex=[str(ROOT / 'say')],
    binaries=binaries, datas=datas, hiddenimports=hiddenimports,
    excludes=['AppKit', 'Foundation', 'Quartz', 'Cocoa', 'objc', 'rumps',
              'mlx', 'mlx_whisper', 'torch', 'torchaudio', 'transformers', 'huggingface_hub',
              'PyQt5', 'PyQt6', 'PySide6.QtWebEngineWidgets', 'tkinter', 'matplotlib',
              'scipy', 'pandas'],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name='SayEngine',
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False, console=True,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='SayEngine')
