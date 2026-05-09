import unittest

from hotkey import format_hotkey, parse_hotkey


class HotkeyParsingTests(unittest.TestCase):
    def test_parse_common_combinations(self):
        self.assertEqual(parse_hotkey('ctrl+space'), ('ctrl', 'space'))
        self.assertEqual(parse_hotkey('<ctrl>+<space>'), ('ctrl', 'space'))
        self.assertEqual(parse_hotkey('cmd+shift+d'), ('cmd', 'shift', 'd'))
        self.assertEqual(parse_hotkey('f5'), ('f5',))

    def test_format_hotkey(self):
        self.assertEqual(format_hotkey(('cmd', 'shift', 'd')), 'cmd+shift+d')

    def test_rejects_invalid_tokens(self):
        with self.assertRaises(ValueError):
            parse_hotkey('ctrl+notakey')

    def test_rejects_duplicates(self):
        with self.assertRaises(ValueError):
            parse_hotkey('ctrl+ctrl')


if __name__ == '__main__':
    unittest.main()
