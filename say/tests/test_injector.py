import unittest
from types import SimpleNamespace
from unittest.mock import patch

import injector
from injector import escape_applescript_text, inject
from text_inject_cgevent import InjectResult as CGEventResult
from text_inject_clipboard import InjectResult as ClipResult


class InjectorTests(unittest.TestCase):
    def setUp(self):
        # These pin the routing, not the machine: with the developer's screen
        # locked macOS turns secure input on, inject() refuses before routing,
        # and three of them failed for that reason alone (2026-10-04). The
        # secure-input path has its own tests below.
        patcher = patch('injector.is_secure_input_enabled', return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_escapes_applescript_string_content(self):
        self.assertEqual(
            escape_applescript_text('say "hello" \\ now'),
            'say \\"hello\\" \\\\ now',
        )

    def test_empty_text_does_not_inject(self):
        result = inject('')
        self.assertFalse(result.success)
        self.assertEqual(result.method, 'none')

    def test_auto_prefers_cgevent_when_it_succeeds(self):
        with patch('injector.config') as cfg, patch(
            'injector.inject_text_cgevent', return_value=CGEventResult(True, None)
        ) as cge, patch(
            'injector.inject_text_clipboard', return_value=ClipResult(True, None)
        ) as clip:
            cfg.INJECT_METHOD = 'auto'
            result = inject('hello')

        self.assertTrue(result.success)
        self.assertEqual(result.method, 'accessibility')
        cge.assert_called_once()
        clip.assert_not_called()

    def test_auto_falls_back_to_clipboard_when_cgevent_fails(self):
        with patch('injector.config') as cfg, patch(
            'injector.inject_text_cgevent',
            return_value=CGEventResult(False, 'permission denied'),
        ), patch(
            'injector.inject_text_clipboard',
            return_value=ClipResult(True, None),
        ) as clip:
            cfg.INJECT_METHOD = 'auto'
            result = inject('hello')

        self.assertTrue(result.success)
        self.assertEqual(result.method, 'clipboard')
        clip.assert_called_once()

    def test_explicit_clipboard_method_skips_cgevent(self):
        with patch('injector.config') as cfg, patch(
            'injector.inject_text_cgevent', return_value=CGEventResult(False, 'x')
        ) as cge, patch(
            'injector.inject_text_clipboard',
            return_value=ClipResult(True, None),
        ):
            cfg.INJECT_METHOD = 'clipboard'
            result = inject('hello')

        self.assertTrue(result.success)
        self.assertEqual(result.method, 'clipboard')
        cge.assert_not_called()


if __name__ == '__main__':
    unittest.main()


class SecureInputTests(unittest.TestCase):
    """Password fields silently swallow both injection paths.

    macOS blocks synthetic keystrokes *and* paste while secure input is on, and
    reports nothing back — so the old code claimed success while the text went
    nowhere. Detecting it up front turns an invisible data loss into a clear
    message, and the controller then keeps the transcript.
    """

    def test_probe_returns_a_bool_and_never_raises(self):
        self.assertIsInstance(injector.is_secure_input_enabled(), bool)

    def test_injection_is_refused_while_secure_input_is_on(self):
        with patch.object(injector, 'is_secure_input_enabled', return_value=True):
            result = injector.inject('hello')
        self.assertFalse(result.success)
        self.assertEqual(result.method, 'blocked')
        self.assertIn('secure input', (result.error or '').lower())

    def test_neither_backend_is_called_when_blocked(self):
        with patch.object(injector, 'is_secure_input_enabled', return_value=True), \
                patch.object(injector, 'inject_text_cgevent') as cge, \
                patch.object(injector, 'inject_text_clipboard') as clip:
            injector.inject('hello')
        cge.assert_not_called()
        clip.assert_not_called()

    def test_normal_path_is_unaffected_when_secure_input_is_off(self):
        with patch.object(injector, 'is_secure_input_enabled', return_value=False), \
                patch.object(
                    injector, 'inject_text_cgevent',
                    return_value=SimpleNamespace(success=True, error=None)):
            result = injector.inject('hello')
        self.assertTrue(result.success)
