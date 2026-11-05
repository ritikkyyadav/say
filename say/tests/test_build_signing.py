"""The build must never silently fall back to ad-hoc code signing.

An ad-hoc signature gives the bundle a Designated Requirement of
`cdhash H"…"` and nothing else. macOS TCC captures that string when the user
grants Accessibility / Input Monitoring and re-validates it on every launch,
so **every rebuild orphans the grant**: System Settings still shows "Say"
switched on (that pane keys its row on bundle id + path) while
`AXIsProcessTrusted()` returns False and the Fn CGEventTap is refused.

The regression these tests pin: both Makefiles used a bare
`export SAY_SIGN_ID`, which exports the name as an EMPTY string when the user
has not set one. `os.environ.get('SAY_SIGN_ID', 'Say Local Signing')` treats
empty as *present*, so the default never applied, `security find-certificate
-c ''` failed, and Say.spec ad-hoc signed — on every build, silently.
"""

import os
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = ROOT / 'say' / 'Say.spec'
MAKEFILES = (ROOT / 'say' / 'Makefile', ROOT / 'desktop' / 'Makefile')


class SigningIdentityResolutionTests(unittest.TestCase):
    def test_environ_get_default_does_not_cover_empty(self):
        # The language behaviour the bug rested on. If this ever changes,
        # the rest of these tests are guarding nothing.
        env = {'SAY_SIGN_ID': ''}
        self.assertEqual(env.get('SAY_SIGN_ID', 'Say Local Signing'), '')
        self.assertEqual(env.get('SAY_SIGN_ID') or 'Say Local Signing', 'Say Local Signing')

    def test_spec_treats_empty_identity_as_unset(self):
        src = SPEC.read_text()
        m = re.search(r'^_SIGN_ID_NAME\s*=\s*(.+)$', src, re.M)
        self.assertIsNotNone(m, 'Say.spec no longer defines _SIGN_ID_NAME')
        expr = m.group(1)
        self.assertNotIn(
            "os.environ.get('SAY_SIGN_ID', ",
            expr,
            "Say.spec uses os.environ.get's default, which an empty "
            'SAY_SIGN_ID defeats — every build would ad-hoc sign.',
        )
        # Evaluate it for real against an empty env var.
        resolved = eval(expr, {'os': type('o', (), {'environ': {'SAY_SIGN_ID': ''}})()})  # noqa: S307
        self.assertEqual(resolved, 'Say Local Signing')

    def test_makefiles_export_the_resolved_identity(self):
        for mk in MAKEFILES:
            with self.subTest(makefile=mk.name):
                src = mk.read_text()
                # Plain booleans, not assertRegex: a failed assertRegex dumps
                # the entire Makefile into the report and buries the reason.
                self.assertIsNone(
                    re.search(r'^export SAY_SIGN_ID\s*$', src, re.M),
                    f'{mk.name} exports SAY_SIGN_ID bare — that exports an empty '
                    'string and defeats the signing-identity default, so every '
                    'build ad-hoc signs and orphans TCC grants.',
                )
                self.assertIsNotNone(
                    re.search(r'^export SAY_SIGN_ID\s*:?=\s*\$\(SIGN_ID\)', src, re.M),
                    f'{mk.name} must export the resolved $(SIGN_ID).',
                )

    def test_make_actually_exports_a_non_empty_identity(self):
        """End-to-end: run make with the project's own variable pattern."""
        for mk in MAKEFILES:
            with self.subTest(makefile=mk.name):
                src = mk.read_text()
                sign_line = re.search(r'^SIGN_ID\s*:?=.*$', src, re.M).group(0)
                export_line = re.search(r'^export SAY_SIGN_ID\s*:?=.*$', src, re.M).group(0)
                snippet = (
                    f'{sign_line}\n{export_line}\n'
                    'probe:\n'
                    '\t@printf "%s" "$$SAY_SIGN_ID"\n'
                )
                env = {k: v for k, v in os.environ.items() if k != 'SAY_SIGN_ID'}
                out = subprocess.run(
                    ['make', '-f', '-', 'probe'],
                    input=snippet, capture_output=True, text=True, env=env, cwd=ROOT,
                )
                self.assertEqual(out.returncode, 0, out.stderr)
                self.assertEqual(
                    out.stdout.strip(),
                    'Say Local Signing',
                    'make exported an empty/incorrect signing identity — '
                    'the build would ad-hoc sign and orphan TCC grants.',
                )


if __name__ == '__main__':
    unittest.main()
