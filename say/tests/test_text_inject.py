"""Tests for the CGEvent and clipboard text injectors."""

import sys
import unittest
from unittest.mock import MagicMock, patch


class CGEventInjectorTests(unittest.TestCase):
    def _patch_quartz(self):
        fake = MagicMock()
        fake.CGEventCreateKeyboardEvent.return_value = MagicMock()
        fake.kCGHIDEventTap = 'kCGHIDEventTap'
        return fake

    def test_empty_text_returns_no_text(self):
        from text_inject_cgevent import inject_text_cgevent

        result = inject_text_cgevent('')
        self.assertFalse(result.success)
        self.assertEqual(result.error, 'no text')

    def test_chunks_long_text_into_pairs(self):
        from text_inject_cgevent import _CHUNK_LEN, inject_text_cgevent

        fake_quartz = self._patch_quartz()

        with patch.dict(sys.modules, {'Quartz': fake_quartz}):
            text = 'a' * (_CHUNK_LEN * 3 + 5)  # 3 full chunks + a partial
            result = inject_text_cgevent(text)

        self.assertTrue(result.success)
        # Two events per chunk (down + up), 4 chunks → 8 events.
        self.assertEqual(fake_quartz.CGEventCreateKeyboardEvent.call_count, 8)
        self.assertEqual(fake_quartz.CGEventPost.call_count, 8)

    def test_returns_error_when_create_returns_none(self):
        from text_inject_cgevent import inject_text_cgevent

        fake_quartz = MagicMock()
        fake_quartz.CGEventCreateKeyboardEvent.return_value = None
        fake_quartz.kCGHIDEventTap = 'tap'

        with patch.dict(sys.modules, {'Quartz': fake_quartz}):
            result = inject_text_cgevent('hello')

        self.assertFalse(result.success)
        self.assertIn('None', result.error)


class ClipboardInjectorTests(unittest.TestCase):
    def test_empty_returns_no_text(self):
        from text_inject_clipboard import inject_text_clipboard

        result = inject_text_clipboard('')
        self.assertFalse(result.success)
        self.assertEqual(result.error, 'no text')

    def test_writes_pasteboard_and_posts_cmd_v(self):
        from text_inject_clipboard import inject_text_clipboard

        fake_pb = MagicMock()
        fake_pb.stringForType_.return_value = 'previous'
        fake_pb.setString_forType_.return_value = True

        fake_appkit = MagicMock()
        fake_appkit.NSPasteboard.generalPasteboard.return_value = fake_pb
        fake_appkit.NSPasteboardTypeString = 'public.utf8-plain-text'

        fake_quartz = MagicMock()
        fake_quartz.CGEventCreateKeyboardEvent.return_value = MagicMock()
        fake_quartz.kCGEventFlagMaskCommand = 0x100000
        fake_quartz.kCGHIDEventTap = 'tap'

        with patch.dict(
            sys.modules,
            {'AppKit': fake_appkit, 'Quartz': fake_quartz},
        ), patch('text_inject_clipboard._POST_PASTE_SETTLE_S', 0.0), patch(
            'text_inject_clipboard._PRE_PASTE_SETTLE_S', 0.0
        ):
            result = inject_text_clipboard('hello')

        self.assertTrue(result.success)
        # Called once to write 'hello', once to restore 'previous'.
        self.assertEqual(fake_pb.setString_forType_.call_count, 2)
        # Cmd+V down + up.
        self.assertEqual(fake_quartz.CGEventPost.call_count, 2)


if __name__ == '__main__':
    unittest.main()
