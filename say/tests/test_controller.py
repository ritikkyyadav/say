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


class CanStartRecordingTests(unittest.TestCase):
    """`can_start_recording()` is the rule the hotkey wiring consults before it
    shows the glyph and opens the mic. It has to agree with `start_recording`
    exactly, or the mark goes up for a recording the engine then refuses."""

    def _controller(self, state):
        controller = DictationController(
            recorder=FakeRecorder([0] * config.SAMPLE_RATE),
            transcriber=FakeTranscriber(),
            injector=lambda _: InjectionResult(True, 'clipboard'),
            logger=None,
        )
        controller.state = state
        return controller

    def test_startable_states(self):
        for state in (DictationState.IDLE, DictationState.READY, DictationState.ERROR):
            self.assertTrue(self._controller(state).can_start_recording(), state)

    def test_busy_states(self):
        for state in (
            DictationState.LOADING, DictationState.RECORDING, DictationState.PROCESSING,
        ):
            self.assertFalse(self._controller(state).can_start_recording(), state)

    def test_a_live_recording_blocks_regardless_of_state(self):
        controller = self._controller(DictationState.READY)
        controller._recording = True
        self.assertFalse(controller.can_start_recording())

    def test_the_predicate_agrees_with_start_recording(self):
        for state in DictationState:
            controller = self._controller(state)
            expected = controller.can_start_recording()
            with patch('controller.notify_error'):
                self.assertEqual(controller.start_recording(), expected, state)


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

        injected = []

        def fake_inject(text):
            # The controller now runs the transcript through postprocess before
            # injecting, so what lands in the app is the cleaned-up form of what
            # the model produced — capitalised and terminated.
            injected.append(text)
            self.assertEqual(text, 'Hello world.')
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
        self.assertEqual(injected, ['Hello world.'])
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


class TranscriptDurabilityTests(unittest.TestCase):
    """A dictation must survive a failed injection.

    Previously `store.insert()` lived inside the success branch, so if the text
    could not be delivered it was discarded — no history row, nothing on the
    clipboard, no way back. That also made the DB structurally incapable of
    recording a failure, which is why it showed a 0% failure rate.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = store_module.reset_store_for_tests(
            os.path.join(self._tmp.name, 't.db')
        )

    def tearDown(self):
        store_module.reset_store_for_tests(os.path.join(self._tmp.name, 't.db'))
        self._tmp.cleanup()

    def _run(self, injector):
        controller = DictationController(
            recorder=FakeRecorder([0] * config.SAMPLE_RATE),
            transcriber=FakeTranscriber(),
            injector=injector,
            logger=None,
        )
        with patch('controller.notify_transcription_complete'), patch(
            'controller.notify_error'
        ), patch.object(DictationController, '_copy_to_clipboard', return_value=True):
            controller.load_model()
            controller.start_recording()
            controller.stop_recording()
        return controller

    def test_failed_injection_still_persists_the_transcript(self):
        self._run(lambda _: InjectionResult(False, 'accessibility', 'boom'))
        rows = self.store.list(limit=10)
        self.assertEqual(len(rows), 1, 'the transcript must survive a failed insert')
        self.assertFalse(rows[0].success)
        self.assertEqual(rows[0].text, 'Hello world.')

    def test_failed_injection_reaches_the_clipboard(self):
        controller = DictationController(
            recorder=FakeRecorder([0] * config.SAMPLE_RATE),
            transcriber=FakeTranscriber(),
            injector=lambda _: InjectionResult(False, 'accessibility', 'boom'),
            logger=None,
        )
        with patch('controller.notify_transcription_complete'), patch(
            'controller.notify_error'
        ), patch.object(
            DictationController, '_copy_to_clipboard', return_value=True
        ) as clip:
            controller.load_model()
            controller.start_recording()
            controller.stop_recording()
        clip.assert_called_once()
        self.assertEqual(clip.call_args[0][0], 'Hello world.')

    def test_successful_injection_marks_the_row_succeeded(self):
        self._run(lambda _: InjectionResult(True, 'clipboard'))
        rows = self.store.list(limit=10)
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0].success)
        self.assertEqual(rows[0].inject_method, 'clipboard')

    def test_no_row_is_left_marked_pending(self):
        self._run(lambda _: InjectionResult(True, 'accessibility'))
        self.assertNotEqual(self.store.list(limit=10)[0].inject_method, 'pending')


class StreamingTranscriptionTests(unittest.TestCase):
    """The controller must use segments when available and never lose a dictation.

    Streaming is a latency optimisation, so every failure mode has to degrade to
    whole-clip transcription rather than to silence.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        store_module.reset_store_for_tests(os.path.join(self._tmp.name, 't.db'))
        self._saved = getattr(config, 'STREAMING_TRANSCRIPTION', True)

    def tearDown(self):
        config.STREAMING_TRANSCRIPTION = self._saved
        store_module.reset_store_for_tests(os.path.join(self._tmp.name, 't.db'))
        self._tmp.cleanup()

    def _controller(self, recorder, transcriber):
        return DictationController(
            recorder=recorder,
            transcriber=transcriber,
            injector=lambda _: InjectionResult(True, 'clipboard'),
            logger=None,
        )

    def test_falls_back_when_the_recorder_cannot_segment(self):
        """FakeRecorder has no set_segment_callback — must still transcribe."""
        recorder = FakeRecorder([0] * config.SAMPLE_RATE)
        transcriber = FakeTranscriber()
        c = self._controller(recorder, transcriber)
        with patch('controller.notify_transcription_complete'), patch(
            'controller.notify_error'
        ):
            c.load_model()
            c.start_recording()
            self.assertTrue(c.stop_recording())
        self.assertTrue(transcriber.transcribed)

    def test_streaming_disabled_uses_whole_clip(self):
        config.STREAMING_TRANSCRIPTION = False
        recorder = FakeRecorder([0] * config.SAMPLE_RATE)
        c = self._controller(recorder, FakeTranscriber())
        self.assertFalse(c._streaming_enabled())

    def test_session_is_cleared_on_cancel(self):
        recorder = FakeRecorder([0] * config.SAMPLE_RATE)
        c = self._controller(recorder, FakeTranscriber())
        with patch('controller.notify_error'):
            c.load_model()
            c.start_recording()
            c.cancel_recording()
        self.assertIsNone(c._session)

    def test_empty_streaming_result_falls_back_to_whole_clip(self):
        """If segments yield nothing, decode the full buffer rather than lose it."""
        recorder = FakeRecorder([0] * config.SAMPLE_RATE)
        transcriber = FakeTranscriber()

        class EmptySession:
            segment_count = 0
            def finish(self, tail):
                return ''
            def cancel(self):
                pass

        c = self._controller(recorder, transcriber)
        c._session = EmptySession()
        recorder.set_segment_callback = lambda _cb: None
        recorder.pop_tail = lambda: []
        text = c._transcribe([0] * config.SAMPLE_RATE)
        self.assertEqual(text, 'hello world')

    def test_a_silent_clip_is_not_decoded_twice(self):
        """B10. Every decode ran and every one heard nothing: the clip is
        silent, and decoding it again only doubles the wait (22 times in
        the log) for the same empty answer."""
        recorder = FakeRecorder([0] * config.SAMPLE_RATE)
        transcriber = FakeTranscriber()

        class SilentSession:
            segment_count = 0
            complete = True
            attempted = 2
            def finish(self, tail):
                return ''
            def cancel(self):
                pass

        c = self._controller(recorder, transcriber)
        c._session = SilentSession()
        recorder.set_segment_callback = lambda _cb: None
        recorder.pop_tail = lambda: []
        self.assertEqual(c._transcribe([0] * config.SAMPLE_RATE), '')
        self.assertFalse(transcriber.transcribed)

    def test_an_incomplete_empty_session_still_re_decodes(self):
        """A failed segment may have swallowed real words: re-decode."""
        recorder = FakeRecorder([0] * config.SAMPLE_RATE)
        transcriber = FakeTranscriber()

        class BrokenSession:
            segment_count = 0
            complete = False
            attempted = 2
            def finish(self, tail):
                return ''
            def cancel(self):
                pass

        c = self._controller(recorder, transcriber)
        c._session = BrokenSession()
        recorder.set_segment_callback = lambda _cb: None
        recorder.pop_tail = lambda: []
        self.assertEqual(c._transcribe([0] * config.SAMPLE_RATE), 'hello world')
        self.assertTrue(transcriber.transcribed)

    def test_short_clip_tears_down_the_streaming_session(self):
        """A discarded tap must not leak a worker thread + segment callback."""
        recorder = FakeRecorder([0] * 10)          # far below MIN_AUDIO_SECONDS
        c = self._controller(recorder, FakeTranscriber())
        c._session = object()                      # pretend one is live
        recorder.set_segment_callback = lambda _cb: None
        with patch('controller.notify_error'):
            c.start_recording()
            c.stop_recording()
        self.assertIsNone(c._session)


class LiveAudioGuardTests(unittest.TestCase):
    """A recording the microphone never contributed to must not be decoded.

    Regression for a real failure: `start()` marked the recording live and then
    blocked for seconds re-opening the input device, so `stop()` returned a
    clip made entirely of the pre-roll seed — 0.63 s for a sentence that took
    ten seconds to say. Handed that plus a style-seed prompt, the decoder
    looped and produced 222 words of "available", which were injected verbatim
    into the user's document.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        store_module.reset_store_for_tests(os.path.join(self._tmp.name, 't.db'))

    def tearDown(self):
        store_module.reset_store_for_tests(os.path.join(self._tmp.name, 't.db'))
        self._tmp.cleanup()

    def _controller(self, live_seconds, transcriber=None):
        recorder = FakeRecorder([0] * int(config.SAMPLE_RATE * 0.63))
        recorder.live_seconds = live_seconds
        ctrl = DictationController(
            recorder=recorder,
            transcriber=transcriber or FakeTranscriber(),
            injector=lambda text: InjectionResult(True, 'clipboard'),
        )
        return ctrl, recorder

    def test_pre_roll_only_clip_is_refused(self):
        transcriber = FakeTranscriber()
        ctrl, _ = self._controller(live_seconds=0.0, transcriber=transcriber)
        ctrl.start_recording()
        self.assertFalse(ctrl.stop_recording())
        self.assertFalse(
            transcriber.transcribed,
            'a clip with no live audio must never reach the decoder',
        )
        self.assertIs(ctrl.state, DictationState.ERROR)

    def test_healthy_clip_still_transcribes(self):
        transcriber = FakeTranscriber()
        ctrl, _ = self._controller(live_seconds=0.62, transcriber=transcriber)
        ctrl.start_recording()
        self.assertTrue(ctrl.stop_recording())
        self.assertTrue(transcriber.transcribed)

    def test_recorder_without_the_attribute_is_unaffected(self):
        """Older/injected recorders that don't report live audio still work."""
        transcriber = FakeTranscriber()
        recorder = FakeRecorder([0] * config.SAMPLE_RATE)
        ctrl = DictationController(
            recorder=recorder,
            transcriber=transcriber,
            injector=lambda text: InjectionResult(True, 'clipboard'),
        )
        ctrl.start_recording()
        self.assertTrue(ctrl.stop_recording())
        self.assertTrue(transcriber.transcribed)
