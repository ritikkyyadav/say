# PyInstaller spec for Say.
#
# Build with:  pyinstaller --noconfirm --clean Say.spec
# Or via:      make build
#
# After build, the Makefile patches Info.plist (see Info.plist in this
# directory) and copies the bundled Moonshine model into Resources/models/.

# -*- mode: python ; coding: utf-8 -*-

import os
import subprocess

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
)

# ── Code-signing identity ────────────────────────────────────────────────
# An AD-HOC signature makes the bundle's Designated Requirement a bare
# `cdhash H"..."` — no bundle id, no anchor. macOS TCC captures that string
# when the user grants Accessibility / Input Monitoring and re-validates it
# on every launch, so EVERY REBUILD SILENTLY ORPHANS THE GRANT: System
# Settings still shows "Say" switched on while AXIsProcessTrusted() returns
# False and the Fn CGEventTap is refused. Signing with a stable local
# identity instead yields
#     identifier "com.ritikyadav.say.dictation" and certificate root = H"..."
# which does not change when the code does, so one grant survives forever.
# Create the identity once with `make signing-identity`.
# `or`, not os.environ.get's default: the Makefiles do a bare
# `export SAY_SIGN_ID`, which exports the name as an EMPTY string when the
# user has not set one. Empty is 'present' as far as os.environ.get is
# concerned, so the default never applied and every build silently ad-hoc
# signed — giving the bundle a bare `cdhash` requirement and orphaning the
# user's Accessibility / Input-Monitoring grants on every single rebuild.
_SIGN_ID_NAME = os.environ.get('SAY_SIGN_ID') or 'Say Local Signing'


def _codesign_identity() -> str:
    """The signing identity, falling back to ad-hoc when it isn't installed.

    Keeps a fresh clone / CI buildable without the certificate; they just
    inherit the rebuild-invalidates-TCC behaviour described above.
    """
    if _SIGN_ID_NAME == '-':
        return '-'
    found = subprocess.run(
        ['security', 'find-certificate', '-c', _SIGN_ID_NAME],
        capture_output=True,
    ).returncode == 0
    if not found:
        print(f"[Say.spec] signing identity {_SIGN_ID_NAME!r} not in the keychain "
              f"— falling back to ad-hoc (TCC grants will not survive rebuilds; "
              f"run `make signing-identity`)")
        return '-'
    print(f"[Say.spec] code-signing with {_SIGN_ID_NAME!r}")
    return _SIGN_ID_NAME


CODESIGN_IDENTITY = _codesign_identity()


block_cipher = None

# mlx_whisper ships internal assets (mel_filters.npz, *.tiktoken) that
# it loads from its package directory at runtime. Without these files
# the model load raises silently inside transcribe().
_data_files = []

# Brand assets (the mark, the menu-bar template). Without this the shipped
# app has no logo at all: brand.py finds nothing and every surface falls
# back to its runtime-drawn placeholder glyph.
_assets_dir = os.path.join(SPECPATH, 'assets')
if os.path.isdir(_assets_dir):
    _data_files.append((_assets_dir, 'assets'))

_data_files += collect_data_files('mlx_whisper')
_data_files += collect_data_files('tiktoken_ext')

# mlx ships native Metal kernels (libmlx.dylib, libjaccl.dylib, …)
# resolved via @rpath. PyInstaller picks up libmlx via its module
# import but misses sibling dylibs — collect everything in mlx/lib/.
_binaries = []
_binaries += collect_dynamic_libs('mlx')

# mlx.metallib is the compiled Metal kernel library. libmlx.dylib uses
# dladdr() at runtime to find its own path and then looks for
# `mlx.metallib` in the SAME directory.  PyInstaller flattens
# libmlx.dylib to Contents/Frameworks/ but leaves mlx.metallib inside
# `mlx/lib/`, breaking the lookup.  Force-place it at the binary root
# (target='.') so it sits beside libmlx.dylib.
import mlx as _mlx_pkg  # noqa: E402
# mlx is a namespace package — __file__ is None.  Use __path__ instead.
_mlx_root = list(_mlx_pkg.__path__)[0]
_metallib = os.path.join(_mlx_root, 'lib', 'mlx.metallib')
if os.path.isfile(_metallib):
    _binaries.append((_metallib, '.'))
# Also keep the in-tree copy so any code path that resolves the
# nested location still finds it.
_data_files.append((_metallib, 'mlx/lib'))

# Moonshine — the bundled default STT. `moonshine_voice` loads
# libmoonshine.dylib first by a package-relative path (Path(__file__).parent),
# then by bare name (DYLD search). Add ONLY the macOS dylibs (libmoonshine +
# its sibling libonnxruntime) into the bundled `moonshine_voice/` dir so the
# package-relative loader resolves and PyInstaller signs them (keeping the
# ad-hoc seal valid). We add them explicitly instead of via
# collect_dynamic_libs() because that also returns the Linux `libmoonshine.so`
# (an ELF binary) which PyInstaller's Mach-O dependency scan can't parse.
import glob as _glob  # noqa: E402
import importlib.util as _ilu  # noqa: E402

_mv_dir = os.path.dirname(_ilu.find_spec('moonshine_voice').origin)
_mv_libs = [os.path.join(_mv_dir, 'libmoonshine.dylib')]
_mv_libs += _glob.glob(os.path.join(_mv_dir, 'libonnxruntime*.dylib'))
for _p in _mv_libs:
    if os.path.isfile(_p):
        _binaries.append((_p, 'moonshine_voice'))

# moonshine_voice Python data (assets/configs). Exclude every native binary so
# the dylibs aren't double-included as unsigned data (which breaks the seal)
# and the Linux .so is never copied in.
_data_files += collect_data_files(
    'moonshine_voice', excludes=['*.dylib', '*.so', '*.dll']
)


a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=_binaries,
    datas=_data_files,
    hiddenimports=collect_submodules('tiktoken_ext')
    + collect_submodules('mlx')
    + collect_submodules('mlx_whisper')
    + collect_submodules('moonshine_voice')
    + [
        'rumps',
        'mlx_whisper',
        'moonshine_voice',
        'moonshine_voice.transcriber',
        'moonshine_voice.moonshine_api',
        'pynput.keyboard._darwin',
        'pynput.mouse._darwin',
        'huggingface_hub',
        'requests',
        'filelock',
        # Desktop UI sidecar — HTTP API + SSE
        'fastapi',
        'uvicorn',
        'uvicorn.protocols.http.h11_impl',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan.on',
        'uvicorn.loops.asyncio',
        'sse_starlette',
        'sse_starlette.sse',
        'platformdirs',
        'server',
        'server.api',
        'server.events',
        'server.runtime',
        'server.store',
        # Engine modules that are imported *lazily* (inside functions), so the
        # graph only reaches them through bytecode analysis. Declared here on
        # purpose: each one is loaded behind a broad `except Exception` that
        # degrades silently by design, so a module PyInstaller failed to bundle
        # would disable the feature in the shipped app with no visible symptom
        # — the packaged build would simply, quietly, stop post-processing or
        # stop measuring corrections.
        'postprocess',
        'streaming',
        'recordings',
        'corrections',
        'readback',
        'overlay',
        'overlay_theme',
        'waveform',
        # Reached only through `import vocabulary` inside the
        # /stats/vocabulary endpoint. Bytecode analysis does find it today,
        # but the whole Home screen is built from that endpoint, so it is
        # declared rather than left to luck.
        'vocabulary',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Say',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    target_arch='arm64',
    codesign_identity=CODESIGN_IDENTITY,
                            # Sign during the build so PyInstaller lays out
                            # + signs its own Python.framework correctly (post-hoc
                            # codesign can't seal the data-in-Frameworks layout).
                            # NOT ad-hoc: see _codesign_identity() above — an
                            # ad-hoc cdhash-only requirement makes macOS discard
                            # the Accessibility grant on every rebuild.
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='Say',
)

app = BUNDLE(
    coll,
    name='Say.app',
    icon=os.path.join(SPECPATH, 'assets', 'Say.icns'),
    bundle_identifier='com.ritikyadav.say.dictation',
    info_plist={
        'CFBundleName': 'Say',
        'CFBundleDisplayName': 'Say',
        'CFBundleVersion': '0.1.0',
        'CFBundleShortVersionString': '0.1.0',
        'LSUIElement': True,
        'LSMinimumSystemVersion': '13.0',
        'NSMicrophoneUsageDescription': (
            'Say records your voice to transcribe it locally on this Mac. '
            'No audio is uploaded.'
        ),
        'NSAppleEventsUsageDescription': (
            'Say sends synthetic keyboard events to insert transcribed '
            'text into the active app.'
        ),
        'NSAccessibilityUsageDescription': (
            'Say uses Accessibility APIs to inject transcribed text and '
            'watch the Fn hotkey.'
        ),
        'NSCameraUsageDescription': 'Say does not use the camera.',
    },
)
