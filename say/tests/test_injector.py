import unittest
from unittest.mock import patch

from injector import escape_applescript_text, inject
from text_inject_cgevent import InjectResult as CGEventResult
from text_inject_clipboard import InjectResult as ClipResult


class InjectorTests(unittest.TestCase):
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
