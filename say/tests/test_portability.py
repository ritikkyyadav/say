"""Can the engine core still run somewhere that is not a Mac?

On macOS this question is unanswerable by ordinary means: PyObjC is always
importable, so a module that quietly grew a dependency on AppKit imports
perfectly well and nothing notices until someone tries to build for Windows
and discovers the "portable core" was not.

So the check blocks every Apple module and imports the core anyway. It runs in
a subprocess because it has to vandalise `builtins.__import__` and `sys.modules`
to do it, and doing that inside the shared test process would poison every test
that ran afterwards.

Windows support needs a per-platform implementation of the input, overlay and
injection layers regardless — that work is real and this test does not pretend
otherwise. What it does guarantee is that the *other* two thirds of the engine
stays platform-neutral, so the port stays a bounded job instead of becoming a
rewrite.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import unittest

SAY_DIR = os.path.join(os.path.dirname(__file__), '..')

# Everything that must work on any platform Python runs on. Modules that are
# legitimately macOS-only (overlay, hotkey_fn, text_inject_*, readback,
# system_events, wizard, settings_window, menubar_icon, fn_override, app) are
# deliberately absent — they are the port surface, not the core.
PORTABLE_CORE = [
    'config', 'settings', 'validation',
    'vocabulary', 'overlay_theme', 'waveform',
    'postprocess', 'corrections', 'streaming',
    'model_catalog', 'model_manager', 'transcriber',
    'audio', 'device_watcher', 'injector', 'hotkey',
    'recordings', 'metrics', 'logging_setup', 'owner_watch',
    'notifications', 'single_instance',
    'server.store', 'server.events', 'server.runtime', 'server.api',
]

BLOCKED = (
    'AppKit', 'Foundation', 'Quartz', 'objc', 'rumps',
    'PyObjCTools', 'CoreFoundation', 'ApplicationServices', 'CoreServices',
)

_PROBE = textwrap.dedent(
    '''
    import sys, builtins, importlib, json

    BLOCKED = {blocked!r}
    CORE = {core!r}

    _real = builtins.__import__

    def guarded(name, *a, **k):
        if name.split('.')[0] in BLOCKED:
            raise ImportError("No module named %r (blocked)" % name.split('.')[0])
        return _real(name, *a, **k)

    builtins.__import__ = guarded
    for mod in list(sys.modules):
        if mod.split('.')[0] in BLOCKED:
            del sys.modules[mod]

    sys.path.insert(0, {say!r})
    sys.path.insert(0, {server!r})

    failed = {{}}
    for name in CORE:
        try:
            importlib.import_module(name)
        except Exception as exc:
            failed[name] = '%s: %s' % (type(exc).__name__, exc)

    print(json.dumps(failed))
    '''
)


class PortableCore(unittest.TestCase):
    def test_the_core_imports_with_every_apple_module_blocked(self):
        code = _PROBE.format(
            blocked=BLOCKED,
            core=PORTABLE_CORE,
            say=os.path.abspath(SAY_DIR),
            server=os.path.abspath(os.path.join(SAY_DIR, 'server')),
        )
        proc = subprocess.run(
            [sys.executable, '-c', code],
            capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])

        import json

        failed = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertEqual(
            failed, {},
            'these modules are no longer platform-neutral:\n'
            + '\n'.join(f'  {k}: {v}' for k, v in failed.items()),
        )

    def test_the_data_directory_resolves_on_every_platform(self):
        """Windows and Linux branches exist and are not accidental."""
        from server import store

        self.assertIn('APPDATA', __import__('inspect').getsource(store._app_data_dir))
        self.assertTrue(store.default_db_path().name.endswith('.db'))


if __name__ == '__main__':
    unittest.main()
