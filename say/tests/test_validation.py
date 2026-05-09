import types
import unittest

from validation import validate_config


def make_config(**overrides):
    values = {
        'MODEL': 'mlx-community/whisper-large-v3-turbo',
        'VALID_MODELS': {'mlx-community/whisper-large-v3-turbo'},
        'HOTKEY': 'ctrl+space',
        'INJECT_METHOD': 'auto',
        'VALID_INJECT_METHODS': {'auto', 'accessibility', 'clipboard'},
        'SAMPLE_RATE': 16000,
        'MAX_RECORDING_SECONDS': 60,
        'MIN_AUDIO_SECONDS': 0.4,
        'LANGUAGE': 'en',
        'SHOW_NOTIFICATIONS': True,
    }
    values.update(overrides)
    return types.SimpleNamespace(**values)


class ConfigValidationTests(unittest.TestCase):
    def test_valid_config_has_no_errors(self):
        self.assertEqual(validate_config(make_config()), [])

    def test_invalid_hotkey_is_reported(self):
        errors = validate_config(make_config(HOTKEY='ctrl+nope'))
        self.assertTrue(any('Unsupported hotkey token' in error for error in errors))

    def test_invalid_injection_method_is_reported(self):
        errors = validate_config(make_config(INJECT_METHOD='network'))
        self.assertTrue(any('INJECT_METHOD' in error for error in errors))

    def test_min_audio_must_be_less_than_max_recording(self):
        errors = validate_config(
            make_config(MIN_AUDIO_SECONDS=60, MAX_RECORDING_SECONDS=60)
        )
        self.assertTrue(any('MIN_AUDIO_SECONDS' in error for error in errors))


if __name__ == '__main__':
    unittest.main()
