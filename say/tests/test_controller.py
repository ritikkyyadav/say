import os
import tempfile
import unittest
from unittest.mock import patch

import config
from controller import DictationController, DictationState
from injector import InjectionResult
from server import store as store_module


class FakeRecorder:
    def __init__(self, samples):
        self.samples = samples
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True
        return self.samples

    @staticmethod
    def duration(audio):
        return len(audio) / config.SAMPLE_RATE


class FakeTranscriber:
    def __init__(self, text='hello world'):
        self.loaded = False
        self.text = text
        self.transcribed = False

    def load(self):
        self.loaded = True

    def transcribe(self, _audio):
        self.transcribed = True
        return self.text


class ControllerTests(unittest.TestCase):
    def setUp(self):
        # Point the transcript store at a per-test temp directory so the
        # post-injection write in controller.py doesn't pollute the
        # user's real Application Support DB.
        self._tmp = tempfile.TemporaryDirectory()
        store_module.reset_store_for_tests(os.path.join(self._tmp.name, 't.db'))

    def tearDown(self):
        store_module.reset_store_for_tests(os.path.join(self._tmp.name, 't.db'))
        self._tmp.cleanup()

    def test_successful_lifecycle(self):
        events = []
        recorder = FakeRecorder([0] * config.SAMPLE_RATE)
        transcriber = FakeTranscriber()

        def fake_inject(text):
            self.assertEqual(text, 'hello world')
            return InjectionResult(True, 'clipboard')

        controller = DictationController(
            recorder=recorder,
            transcriber=transcriber,
            injector=fake_inject,
            status_callback=events.append,
            logger=None,
        )

        with patch('controller.notify_transcription_complete'), patch(
            'controller.notify_error'
        ):
            self.assertTrue(controller.load_model())
            self.assertTrue(controller.start_recording())
            self.assertTrue(controller.stop_recording())

        self.assertTrue(recorder.started)
        self.assertTrue(recorder.stopped)
        self.assertTrue(transcriber.loaded)
        self.assertTrue(transcriber.transcribed)
        self.assertEqual(controller.state, DictationState.READY)
        self.assertIn(DictationState.RECORDING, [event.state for event in events])
        self.assertIn(DictationState.PROCESSING, [event.state for event in events])

    def test_short_audio_skips_transcription(self):
        recorder = FakeRecorder([0] * 10)
        transcriber = FakeTranscriber()
        controller = DictationController(
            recorder=recorder,
            transcriber=transcriber,
            injector=lambda _: InjectionResult(True, 'clipboard'),
            logger=None,
        )

        with patch('controller.notify_transcription_complete'), patch(
            'controller.notify_error'
        ):
            controller.load_model()
            controller.start_recording()
            self.assertTrue(controller.stop_recording())

        self.assertFalse(transcriber.transcribed)
        self.assertEqual(controller.state, DictationState.READY)

    def test_max_duration_event_finalizes_recording(self):
        """Audio recorder firing 'max_duration' triggers stop_recording."""
        recorder = FakeRecorder([0] * config.SAMPLE_RATE)
        transcriber = FakeTranscriber()
        controller = DictationController(
            recorder=recorder,
            transcriber=transcriber,
            injector=lambda _: InjectionResult(True, 'clipboard'),
            logger=None,
        )

        with patch('controller.notify_transcription_complete'), patch(
            'controller.notify_error'
        ):
            controller.load_model()
            controller.start_recording()
            controller._on_audio_event('max_duration', 'cap reached')

        self.assertTrue(transcriber.transcribed)
        self.assertEqual(controller.state, DictationState.READY)

    def test_stream_error_event_transitions_to_error(self):
        recorder = FakeRecorder([0] * config.SAMPLE_RATE)
        transcriber = FakeTranscriber()
        events = []
        controller = DictationController(
            recorder=recorder,
            transcriber=transcriber,
            injector=lambda _: InjectionResult(True, 'clipboard'),
            status_callback=events.append,
            logger=None,
        )

        with patch('controller.notify_error'):
            controller.load_model()
            controller.start_recording()
            controller._on_audio_event('stream_error', 'mic disconnected')

        self.assertEqual(controller.state, DictationState.ERROR)
        self.assertIn(
            DictationState.ERROR, [event.state for event in events]
        )
        self.assertFalse(transcriber.transcribed)


if __name__ == '__main__':
    unittest.main()
