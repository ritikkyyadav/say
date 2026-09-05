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


class Utf16ChunkingTests(unittest.TestCase):
    """Quartz counts UTF-16 units; Python counts code points. The injector
    used to confuse the two and drop the tail of any chunk containing an
    emoji. These pin the unit-aware chunking and the length handed over."""

    def test_astral_characters_count_as_two_units(self):
        from text_inject_cgevent import utf16_len

        self.assertEqual(utf16_len('abc'), 3)
        self.assertEqual(utf16_len('😀'), 2)
        self.assertEqual(utf16_len('hello world 😀 test!'), 20)

    def test_chunks_are_measured_in_units_not_characters(self):
        from text_inject_cgevent import _CHUNK_LEN, chunks, utf16_len

        pieces = chunks('😀' * 11)
        self.assertEqual([utf16_len(p) for p in pieces], [20, 2])
        for piece in pieces:
            self.assertLessEqual(utf16_len(piece), _CHUNK_LEN)

    def test_a_surrogate_pair_is_never_split(self):
        from text_inject_cgevent import chunks

        pieces = chunks('a' * 19 + '😀' + 'b')
        # 19 units, then the emoji would make 21: it starts the next chunk.
        self.assertEqual(pieces, ['a' * 19, '😀b'])
        for piece in pieces:
            piece.encode('utf-16-le').decode('utf-16-le')   # every chunk is whole

    def test_chunking_is_lossless(self):
        from text_inject_cgevent import chunks

        text = 'café ünïcode こんにちは 🙂🙂🙂🙂🙂🙂🙂🙂🙂🙂🙂 end!'
        self.assertEqual(''.join(chunks(text)), text)

    def test_the_length_handed_to_quartz_is_the_unit_count(self):
        from text_inject_cgevent import inject_text_cgevent, utf16_len

        fake_quartz = MagicMock()
        fake_quartz.CGEventCreateKeyboardEvent.return_value = MagicMock()
        fake_quartz.kCGHIDEventTap = 'tap'
        with patch.dict(sys.modules, {'Quartz': fake_quartz}):
            result = inject_text_cgevent('hello world 😀 test!')

        self.assertTrue(result.success)
        calls = fake_quartz.CGEventKeyboardSetUnicodeString.call_args_list
        self.assertTrue(calls)
        for call in calls:
            _evt, length, chunk = call.args
            self.assertEqual(length, utf16_len(chunk))
            self.assertEqual(length, len(chunk.encode('utf-16-le')) // 2)

    def test_real_quartz_round_trip_keeps_every_character(self):
        """The failure as observed: set the payload the way the injector does,
        read it back through Quartz, and compare. Skips without Quartz."""
        try:
            import Quartz
        except Exception:  # pragma: no cover - Linux CI
            self.skipTest('Quartz unavailable')
        from text_inject_cgevent import chunks, utf16_len

        text = 'hello world 😀 test! 🙂🙂🙂🙂🙂 done'
        got = []
        for chunk in chunks(text):
            evt = Quartz.CGEventCreateKeyboardEvent(None, 0, True)
            Quartz.CGEventKeyboardSetUnicodeString(evt, utf16_len(chunk), chunk)
            _n, s = Quartz.CGEventKeyboardGetUnicodeString(evt, 64, None, None)
            got.append(s)
        self.assertEqual(''.join(got), text)


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
