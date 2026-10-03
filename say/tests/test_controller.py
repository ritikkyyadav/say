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


class FocusGuardTests(unittest.TestCase):
    """B2. The keystrokes go wherever focus is when transcription ends, which
    with a slow model is seconds after the key was released. A dictation
    meant for one window must never be typed into another."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = store_module.reset_store_for_tests(os.path.join(self._tmp.name, 't.db'))

    def tearDown(self):
        store_module.reset_store_for_tests(os.path.join(self._tmp.name, 't.db'))
        self._tmp.cleanup()

    def _run(self, focus_sequence):
        injected = []
        controller = DictationController(
            recorder=FakeRecorder([0] * config.SAMPLE_RATE),
            transcriber=FakeTranscriber(),
            injector=lambda text: (injected.append(text), InjectionResult(True, 'accessibility'))[1],
            status_callback=(events := []).append,
            logger=None,
        )
        with patch('controller.get_frontmost_app', side_effect=focus_sequence), \
                patch('controller.notify_transcription_complete'), \
                patch('controller.notify_error') as notified, \
                patch.object(DictationController, '_copy_to_clipboard', return_value=True) as copied:
            controller.load_model()
            controller.start_recording()
            controller.stop_recording()
        rows = self.store.list()
        return injected, rows, events, copied, notified

    def test_unchanged_focus_types_the_text(self):
        injected, rows, _events, copied, _n = self._run(['com.app.a', 'com.app.a'])
        self.assertEqual(injected, ['Hello world.'])
        self.assertEqual(rows[0].target_app, 'com.app.a')
        self.assertTrue(rows[0].success)
        copied.assert_not_called()

    def test_moved_focus_holds_the_text_instead_of_typing_it(self):
        injected, rows, events, copied, notified = self._run(['com.app.a', 'com.app.b'])
        self.assertEqual(injected, [])
        copied.assert_called_once_with('Hello world.')
        row = rows[0]
        self.assertEqual(row.inject_method, 'held')
        self.assertFalse(row.success)
        # The row records where the words were meant to go.
        self.assertEqual(row.target_app, 'com.app.a')
        final = events[-1]
        self.assertEqual(final.state, DictationState.READY)
        self.assertIn('com.app.b', final.message)
        self.assertIn('clipboard', final.message)
        notified.assert_called_once()

    def test_unknown_focus_on_either_side_still_types(self):
        for sequence in (['com.app.a', None], [None, 'com.app.b'], [None, None]):
            injected, _rows, _e, _c, _n = self._run(sequence)
            self.assertEqual(injected, ['Hello world.'], sequence)

    def test_the_policy_table(self):
        from controller import focus_policy

        self.assertEqual(focus_policy('a', 'a'), 'inject')
        self.assertEqual(focus_policy('a', 'b'), 'hold')
        self.assertEqual(focus_policy(None, 'b'), 'inject')
        self.assertEqual(focus_policy('a', None), 'inject')
        self.assertEqual(focus_policy(None, None), 'inject')


class ProgressEventTests(unittest.TestCase):
    """B8: while the user waits, PROCESSING events carry (done, total)."""

    def test_progress_reaches_the_event_and_the_bus_only_while_processing(self):
        events = []
        controller = DictationController(
            recorder=FakeRecorder([0] * config.SAMPLE_RATE),
            transcriber=FakeTranscriber(),
            injector=lambda _: InjectionResult(True, 'clipboard'),
            status_callback=events.append,
            logger=None,
        )
        controller.state = DictationState.RECORDING
        controller._on_stream_progress(1, 3)
        self.assertEqual(events, [])           # still talking: nothing to wait for

        controller.state = DictationState.PROCESSING
        with patch('controller.get_bus') as bus:
            controller._on_stream_progress(2, 3)
        self.assertEqual(events[-1].state, DictationState.PROCESSING)
        self.assertEqual(events[-1].progress, (2, 3))
        payload = bus.return_value.publish_threadsafe.call_args.args[0]
        self.assertEqual(payload['progress'], {'done': 2, 'total': 3})

    def test_events_without_progress_carry_none(self):
        payloads = []
        controller = DictationController(
            recorder=FakeRecorder([0] * config.SAMPLE_RATE),
            transcriber=FakeTranscriber(),
            logger=None,
        )
        with patch('controller.get_bus') as bus:
            bus.return_value.publish_threadsafe.side_effect = payloads.append
            controller._emit(DictationState.READY, 'ready')
        self.assertIsNone(payloads[0]['progress'])


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
        self.assertEqual(self.store.observation_summary()['successful'], 0)

    def test_raw_decoder_text_and_effective_config_are_saved_locally(self):
        self._run(lambda _: InjectionResult(True, 'accessibility'))
        row = self.store._conn.execute('SELECT * FROM transcripts').fetchone()
        self.assertEqual(row['raw_asr'], 'hello world')
        self.assertEqual(row['text'], 'Hello world.')
        self.assertEqual(len(row['pipeline_hash']), 64)
        self.assertIsNotNone(row['pipeline_version'])
        self.assertEqual(row['language'], config.LANGUAGE)
        self.assertNotIn('raw_asr', self.store.list()[0].to_dict())

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

    def test_disabled_readback_is_reported_not_left_pending(self):
        with patch.object(config, 'LEARN_FROM_CORRECTIONS', False):
            self._run(lambda _: InjectionResult(True, 'accessibility'))
        self.assertEqual(self.store.observation_summary()['outcomes']['disabled'], 1)
        self.assertEqual(self.store.correction_summary()['insertions'], 0)

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

    def test_the_session_gets_both_prompts(self):
        """B3: the tail-safe prompt must reach the session, or the guard is inert."""
        import postprocess
        import streaming

        captured = {}

        class Session:
            def __init__(self, transcriber, base_prompt='', short_prompt=None, **_kwargs):
                captured['base'] = base_prompt
                captured['short'] = short_prompt

            def push(self, segment):
                pass

            def cancel(self):
                pass

        recorder = FakeRecorder([0] * config.SAMPLE_RATE)
        recorder.set_segment_callback = lambda _cb: None
        c = self._controller(recorder, FakeTranscriber())
        saved = config.STYLE_SEED_ENABLED
        config.STYLE_SEED_ENABLED = True
        try:
            with patch.object(streaming, 'SegmentedSession', Session):
                c._begin_streaming()
        finally:
            config.STYLE_SEED_ENABLED = saved
            c._end_streaming(cancel=True)

        self.assertIn(postprocess.STYLE_SEED, captured['base'])
        self.assertNotIn(postprocess.STYLE_SEED, captured['short'] or '')

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

    def test_a_partial_text_from_an_incomplete_session_is_redecoded(self):
        recorder = FakeRecorder([0] * config.SAMPLE_RATE)
        transcriber = FakeTranscriber()

        class PartialSession:
            segment_count = 1
            complete = False
            attempted = 1

            def finish(self, tail):
                return 'earlier segment'

            def cancel(self):
                pass

        c = self._controller(recorder, transcriber)
        c._session = PartialSession()
        recorder.set_segment_callback = lambda _cb: None
        recorder.pop_tail = lambda: [0.05] * (config.SAMPLE_RATE // 2)
        self.assertEqual(c._transcribe([0.05] * config.SAMPLE_RATE), 'hello world')
        self.assertTrue(transcriber.transcribed)

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
        # Exercise the live-audio guard independently of the configurable
        # minimum clip length (the real regression used a 0.63 s clip).
        self._min_audio = patch.object(config, 'MIN_AUDIO_SECONDS', 0.4)
        self._min_audio.start()

    def tearDown(self):
        self._min_audio.stop()
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


class PressOutcomeTests(unittest.TestCase):
    """Every key press ends in exactly one recorded outcome.

    The reliability score is "presses that turned into text", and a score is
    only as honest as its denominator: a path that records nothing silently
    improves it, a path that records twice silently ruins it."""

    def setUp(self):
        import metrics

        self._tmp = tempfile.TemporaryDirectory()
        store_module.reset_store_for_tests(os.path.join(self._tmp.name, 't.db'))
        self._ledger = metrics.get_ledger()._path
        self._mark = os.path.getsize(self._ledger) if os.path.exists(self._ledger) else 0

    def tearDown(self):
        store_module.reset_store_for_tests(os.path.join(self._tmp.name, 't.db'))
        self._tmp.cleanup()

    def _presses(self):
        import json

        if not os.path.exists(self._ledger):
            return []
        with open(self._ledger, encoding='utf-8') as f:
            f.seek(self._mark)
            rows = [json.loads(line) for line in f if line.strip()]
        return [r for r in rows if r.get('event') == 'press']

    def _controller(self, recorder=None, transcriber=None, injector=None):
        c = DictationController(
            recorder=recorder or FakeRecorder([0.05] * config.SAMPLE_RATE * 2),
            transcriber=transcriber or FakeTranscriber(),
            injector=injector or (lambda text: InjectionResult(True, 'accessibility')),
            status_callback=None,
            logger=None,
        )
        c.load_model()
        return c

    def _press(self, c, how='stop'):
        with patch('controller.get_frontmost_app', return_value='com.app.a'), \
                patch('controller.notify_transcription_complete'), \
                patch('controller.notify_error'), \
                patch.object(DictationController, '_copy_to_clipboard', return_value=True):
            c.start_recording()
            if how == 'stop':
                c.stop_recording()
            elif how == 'cancel':
                c.cancel_recording()
            elif how == 'system':
                c.cancel_recording(reason='system')

    def _only(self):
        presses = self._presses()
        self.assertEqual(len(presses), 1, presses)
        return presses[0]

    def test_a_delivered_dictation(self):
        self._press(self._controller())
        p = self._only()
        self.assertEqual(p['outcome'], 'delivered')
        self.assertEqual(p['mode'], 'ptt')
        self.assertEqual(p['words'], 2)
        self.assertIn('wait_seconds', p)
        self.assertNotIn('text', p)

    def test_a_clip_too_short(self):
        self._press(self._controller(recorder=FakeRecorder([0.05] * (config.SAMPLE_RATE // 4))))
        self.assertEqual(self._only()['outcome'], 'too_short')

    def test_silence(self):
        self._press(self._controller(transcriber=FakeTranscriber(text='')))
        self.assertEqual(self._only()['outcome'], 'silence')

    def test_a_failed_decode(self):
        class Broken(FakeTranscriber):
            def transcribe(self, _audio):
                raise RuntimeError('decoder died')

        self._press(self._controller(transcriber=Broken()))
        self.assertEqual(self._only()['outcome'], 'error:transcription')

    def test_a_failed_injection(self):
        self._press(self._controller(injector=lambda t: InjectionResult(False, 'cgevent', 'denied')))
        p = self._only()
        self.assertEqual(p['outcome'], 'error:injection')
        self.assertTrue(p['recovered_to_clipboard'])

    def test_focus_moving_holds_it(self):
        c = self._controller()
        with patch('controller.get_frontmost_app', side_effect=['com.app.a', 'com.app.b']), \
                patch('controller.notify_error'), \
                patch.object(DictationController, '_copy_to_clipboard', return_value=True):
            c.start_recording()
            c.stop_recording()
        self.assertEqual(self._only()['outcome'], 'held')

    def test_a_user_cancel_and_a_system_cancel_are_told_apart(self):
        self._press(self._controller(), how='cancel')
        self._press(self._controller(), how='system')
        self.assertEqual([p['outcome'] for p in self._presses()],
                         ['cancelled', 'cancelled_by_system'])

    def test_a_press_while_busy_is_recorded_as_refused(self):
        c = self._controller()
        with patch('controller.get_frontmost_app', return_value='com.app.a'):
            c.start_recording()
            self.assertFalse(c.start_recording())  # already recording
        p = self._only()
        self.assertEqual(p['outcome'], 'refused_busy')
        self.assertEqual(p['state'], 'recording')

    def test_a_microphone_that_will_not_start(self):
        class Dead(FakeRecorder):
            def start(self):
                raise OSError('device busy')

        self._press(self._controller(recorder=Dead([0.0])), how='none')
        self.assertEqual(self._only()['outcome'], 'error:mic_start')

    def test_a_clip_the_microphone_never_delivered(self):
        class PreRollOnly(FakeRecorder):
            live_seconds = 0.0

        self._press(self._controller(recorder=PreRollOnly([0.05] * config.SAMPLE_RATE * 2)))
        self.assertEqual(self._only()['outcome'], 'no_live_audio')

    def test_a_stream_error_mid_recording(self):
        c = self._controller()
        with patch('controller.get_frontmost_app', return_value='com.app.a'), \
                patch('controller.notify_error'):
            c.start_recording()
            c._on_audio_event('stream_error', 'gone')
            c.stop_recording()  # nothing left to stop: must not record again
        self.assertEqual(self._only()['outcome'], 'error:audio_stream')

    def test_every_recorded_outcome_is_a_known_one(self):
        from controller import PRESS_OUTCOMES

        for how in ('stop', 'cancel', 'system'):
            self._press(self._controller(), how=how)
        for p in self._presses():
            self.assertIn(p['outcome'], PRESS_OUTCOMES)
