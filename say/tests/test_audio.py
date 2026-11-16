"""Tests for AudioRecorder pre-roll seeding and event channel.

We avoid spinning up a real `sounddevice` stream by invoking `_callback`
directly — the same path the real audio thread takes.
"""

import threading
import time
import unittest
import unittest.mock
from unittest.mock import patch

import audio as audio_mod
import config
import numpy as np
from audio import AudioRecorder


class AudioRecorderTests(unittest.TestCase):
    def _make(self):
        from audio import AudioRecorder

        return AudioRecorder()

    def test_callback_feeds_pre_roll_when_idle(self):
        rec = self._make()
        block = np.full((1024, 1), 0.1, dtype='float32')
        rec._callback(block, 1024, None, None)
        self.assertGreater(len(rec._pre_roll_buffer), 0)
        self.assertFalse(rec._capturing)

    def test_start_seeds_frames_with_pre_roll(self):
        rec = self._make()
        # Fill the pre-roll buffer
        for _ in range(5):
            rec._callback(
                np.full((1024, 1), 0.05, dtype='float32'), 1024, None, None
            )
        pre_roll_size = len(rec._pre_roll_buffer)
        self.assertGreater(pre_roll_size, 0)

        # Manually call start() but skip _ensure_stream_open by patching
        # it out — we are not exercising sounddevice here.
        rec._ensure_stream_open = lambda: None
        rec.start()

        seeded = sum(f.shape[0] for f in rec._frames)
        self.assertEqual(seeded, pre_roll_size)
        self.assertTrue(rec._capturing)

    def test_max_duration_event_dispatched_once(self):
        rec = self._make()
        rec._ensure_stream_open = lambda: None

        events = []

        def event_cb(name, msg):
            events.append((name, msg))

        rec.set_event_callback(event_cb)
        # Pin a short cap so the test allocates a tiny buffer regardless of the
        # (generous) shipped default.
        original_cap = config.MAX_RECORDING_SECONDS
        config.MAX_RECORDING_SECONDS = 2
        try:
            rec.start()

            # Prepare a buffer that will exceed MAX_RECORDING_SECONDS in one shot.
            big_samples = config.SAMPLE_RATE * config.MAX_RECORDING_SECONDS + 100
            big_block = np.zeros((big_samples, 1), dtype='float32')
            rec._callback(big_block, big_samples, None, None)

            # Fire a second callback to confirm max_duration is *not* dispatched twice.
            rec._callback(
                np.zeros((1024, 1), dtype='float32'), 1024, None, None
            )
        finally:
            config.MAX_RECORDING_SECONDS = original_cap

        deadline = time.time() + 1.0
        while time.time() < deadline and not any(
            e[0] == 'max_duration' for e in events
        ):
            time.sleep(0.01)

        max_events = [e for e in events if e[0] == 'max_duration']
        self.assertEqual(len(max_events), 1, msg=f'events={events}')

    def test_stream_error_event_on_callback_exception(self):
        rec = self._make()
        rec._ensure_stream_open = lambda: None

        events = []
        rec.set_event_callback(lambda name, msg: events.append((name, msg)))
        rec.start()

        # Force the exception path: pass an object that np.mean cannot handle.
        # The callback's try/except should dispatch a stream_error.
        class Boom:
            def __pow__(self, _):
                raise RuntimeError('synthetic failure')

            shape = (1, 1)

        # We hit the exception inside the amplitude branch; the callback
        # body is wrapped in try/except → dispatch happens.
        rec._callback(Boom(), 1, None, None)

        deadline = time.time() + 1.0
        while time.time() < deadline and not any(
            e[0] == 'stream_error' for e in events
        ):
            time.sleep(0.01)

        self.assertTrue(any(e[0] == 'stream_error' for e in events), msg=str(events))

    def test_stop_returns_concatenated_audio(self):
        rec = self._make()
        rec._ensure_stream_open = lambda: None
        rec.start()
        # Drop pre-roll seed so we can assert on a known length.
        rec._frames = []
        rec._capturing = True

        block = np.full((1024, 1), 0.5, dtype='float32')
        rec._callback(block, 1024, None, None)
        rec._callback(block, 1024, None, None)

        audio = rec.stop()
        self.assertEqual(audio.shape, (2048,))
        self.assertFalse(rec._capturing)


if __name__ == '__main__':
    unittest.main()


class PreRollTests(unittest.TestCase):
    """Pre-roll must actually contain audio by the time start() reads it.

    Regression: the mic was only opened inside start(), and the ring buffer is
    fed exclusively by the audio callback — so the buffer was always empty at
    the moment it was seeded and pre-roll contributed zero samples. Combined
    with the hotkey's tap/hold window, the first ~300-450 ms of every dictation
    was discarded.
    """

    def setUp(self):
        self.rec = AudioRecorder()
        self._saved = config.PRE_ROLL_SECONDS
        config.PRE_ROLL_SECONDS = 0.5
        # Never touch real audio hardware: arm() would open a live
        # sounddevice InputStream, which needs a mic grant and would hang CI.
        self._patch = patch.object(
            AudioRecorder, '_ensure_stream_open', lambda _self: None
        )
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        config.PRE_ROLL_SECONDS = self._saved

    def _feed(self, seconds: float) -> None:
        """Push audio through the callback the way sounddevice would."""
        n = int(seconds * config.SAMPLE_RATE)
        block = np.full((n, 1), 0.25, dtype='float32')
        self.rec._callback(block, n, None, None)

    def test_buffer_is_empty_without_arming(self):
        self.assertEqual(len(self.rec._pre_roll_buffer), 0)

    def test_armed_audio_is_prepended_to_the_recording(self):
        self.rec.arm()                 # mic opens; ring buffer starts filling
        self._feed(0.30)               # user begins speaking during the window
        self.rec._capturing = True     # start() would do this
        with self.rec._lock:
            seed = np.fromiter(
                self.rec._pre_roll_buffer, dtype='float32',
                count=len(self.rec._pre_roll_buffer),
            ).reshape(-1, 1)
            self.rec._frames = [seed]
            self.rec._captured_samples = seed.shape[0]
        self._feed(0.20)               # rest of the sentence
        audio = self.rec.stop()
        # 0.30 s armed + 0.20 s captured, none of it lost.
        self.assertGreater(len(audio), int(0.45 * config.SAMPLE_RATE))

    def test_disarm_clears_the_buffer(self):
        self.rec.arm()
        self._feed(0.20)
        self.assertGreater(len(self.rec._pre_roll_buffer), 0)
        self.rec.disarm()
        self.assertEqual(len(self.rec._pre_roll_buffer), 0)

    def test_disarm_does_not_disturb_a_live_recording(self):
        self.rec.arm()
        self._feed(0.10)
        self.rec._capturing = True
        self.rec.disarm()              # a late/duplicate disarm must be inert
        self.assertTrue(self.rec._capturing)

    def test_arm_is_a_noop_when_pre_roll_is_disabled(self):
        config.PRE_ROLL_SECONDS = 0
        self.rec.arm()
        self.assertIsNone(self.rec._stream)


class LiveSecondsTests(unittest.TestCase):
    """`live_seconds` distinguishes real capture from a pre-roll-only clip.

    The recorder used to mark a recording live and *then* block re-opening the
    input device, which can take seconds when CoreAudio is still releasing it.
    Everything spoken in that window was lost and `stop()` returned just the
    pre-roll seed, so the controller had no way to tell a real half-second
    clip from a microphone that never started.
    """

    def _make(self):
        rec = AudioRecorder()
        rec._ensure_stream_open = lambda: None
        return rec

    def test_pre_roll_seed_does_not_count_as_live(self):
        rec = self._make()
        for _ in range(5):
            rec._callback(np.full((1024, 1), 0.05, dtype='float32'), 1024, None, None)
        rec.start()
        self.assertGreater(sum(f.shape[0] for f in rec._frames), 0)
        self.assertEqual(rec.live_seconds, 0.0)

    def test_callback_audio_counts_as_live(self):
        rec = self._make()
        rec.start()
        for _ in range(3):
            rec._callback(np.full((1024, 1), 0.2, dtype='float32'), 1024, None, None)
        self.assertAlmostEqual(
            rec.live_seconds, 3 * 1024 / config.SAMPLE_RATE, places=5
        )

    def test_live_seconds_survives_stop_for_the_controller_to_read(self):
        rec = self._make()
        rec.start()
        rec._callback(np.full((1024, 1), 0.2, dtype='float32'), 1024, None, None)
        rec.stop()
        self.assertGreater(rec.live_seconds, 0.0)

    def test_stream_is_opened_before_capture_begins(self):
        """Opening first is what stops the reopen delay from eating speech."""
        rec = AudioRecorder()
        order = []
        rec._ensure_stream_open = lambda: order.append(
            ('open', rec._capturing)
        )
        rec.start()
        self.assertEqual(order, [('open', False)])
        self.assertTrue(rec._capturing)


class DeviceSerialisationTests(unittest.TestCase):
    """Only one thread may ever be inside CoreAudio.

    Regression — the worst outage this engine has shipped. `arm()` (hotkey
    key-down) and `start()` (gesture resolved, ~250 ms later) arrive on
    *different* threads, and `_ensure_stream_open` guarded itself with a bare
    `if self._stream is not None: return` outside any lock. While `arm()` sat
    inside `sd.InputStream(...)` the attribute was still None, so `start()`
    opened a second stream on the same device. Both then hit PaErrorCode -9986,
    both reached the recovery branch, and one called `sd._terminate()` while the
    other was inside `Pa_OpenStream` — which leaves CoreAudio's HAL mutex held
    forever. Every later open blocked; a sample of the wedged engine showed 35
    threads parked in `Pa_OpenStream`, one per Fn press. Dictation was dead
    until relaunch, with nothing in the log but `start_recording called`.
    """

    def setUp(self):
        self.rec = AudioRecorder()
        self._saved_open = audio_mod._OPEN_TIMEOUT
        self._saved_busy = audio_mod._DEVICE_BUSY_TIMEOUT
        # Keep the suite fast: the behaviour under test is the bound, not its size.
        audio_mod._OPEN_TIMEOUT = 0.4
        audio_mod._DEVICE_BUSY_TIMEOUT = 0.6

    def tearDown(self):
        audio_mod._OPEN_TIMEOUT = self._saved_open
        audio_mod._DEVICE_BUSY_TIMEOUT = self._saved_busy

    @staticmethod
    def _tracking_stream_factory(inside, peak, gate=None):
        """A fake `sd.InputStream` that records how many callers are inside it."""

        def factory(**_kwargs):
            with inside['lock']:
                inside['n'] += 1
                peak.append(inside['n'])
            try:
                if gate is not None:
                    gate.wait(2.0)
            finally:
                with inside['lock']:
                    inside['n'] -= 1
            stream = unittest.mock.MagicMock()
            return stream

        return factory

    def test_arm_and_start_never_open_the_device_concurrently(self):
        inside = {'n': 0, 'lock': threading.Lock()}
        peak: list[int] = []
        gate = threading.Event()
        opened: list[int] = []

        def factory(**kwargs):
            with inside['lock']:
                inside['n'] += 1
                peak.append(inside['n'])
            opened.append(1)
            gate.wait(2.0)          # hold the device open the way CoreAudio can
            with inside['lock']:
                inside['n'] -= 1
            return unittest.mock.MagicMock()

        with patch.object(audio_mod.sd, 'InputStream', factory):
            # arm() first, exactly as the hotkey does on Fn-down...
            t = threading.Thread(target=self.rec.arm, daemon=True)
            t.start()
            time.sleep(0.05)        # ...then the gesture resolves into a record
            starter = threading.Thread(target=self.rec._ensure_stream_open, daemon=True)
            starter.start()
            time.sleep(0.15)
            gate.set()
            t.join(3.0)
            starter.join(3.0)

        self.assertEqual(
            max(peak, default=0), 1,
            'two threads were inside sd.InputStream at once — this is the '
            'collision that wedges CoreAudio',
        )
        self.assertEqual(
            len(opened), 1,
            'start() opened a second device instead of reusing the one arm() '
            'had already opened',
        )

    def test_a_wedged_device_does_not_block_the_caller_forever(self):
        """An open that never returns must surface as an error, not a hang.

        `controller.start_recording()` calls us while holding its own RLock, so
        an unbounded open strands the whole state machine: every later press
        logs `start_recording called` and then blocks silently on that lock.
        """
        never = threading.Event()

        def wedged(**_kwargs):
            never.wait(30.0)        # CoreAudio's HAL mutex, held by nobody
            return unittest.mock.MagicMock()

        with patch.object(audio_mod.sd, 'InputStream', wedged):
            began = time.monotonic()
            with self.assertRaises(audio_mod.MicrophoneUnavailable):
                self.rec._ensure_stream_open()
            elapsed = time.monotonic() - began
        never.set()
        self.assertLess(elapsed, 3.0, 'the open was not bounded')

    def test_a_wedged_device_does_not_accumulate_threads_inside_coreaudio(self):
        """Each retry after a wedge must fail fast, not queue another open.

        The 35 wedged threads in the failing engine were one per Fn press: every
        attempt walked straight into `Pa_OpenStream` and stayed there.
        """
        never = threading.Event()
        entered: list[int] = []

        def wedged(**_kwargs):
            entered.append(1)
            never.wait(30.0)
            return unittest.mock.MagicMock()

        with patch.object(audio_mod.sd, 'InputStream', wedged):
            for _ in range(4):
                with self.assertRaises(audio_mod.MicrophoneUnavailable):
                    self.rec._ensure_stream_open()
        never.set()
        self.assertEqual(
            len(entered), 1,
            'a wedged device must park exactly one thread in CoreAudio, not one '
            'per attempt',
        )

    def test_the_portaudio_reset_cannot_run_while_another_thread_is_opening(self):
        """`sd._terminate()` under a live `Pa_OpenStream` is the fatal step."""
        events: list[str] = []
        lock = threading.Lock()
        gate = threading.Event()

        def failing_open(**_kwargs):
            with lock:
                events.append('open-enter')
            gate.wait(0.2)
            with lock:
                events.append('open-exit')
            raise RuntimeError('PaErrorCode -9986')

        def terminate():
            with lock:
                events.append('terminate')

        with patch.object(audio_mod.sd, 'InputStream', failing_open), \
             patch.object(audio_mod.sd, '_terminate', terminate), \
             patch.object(audio_mod.sd, '_initialize', lambda: None), \
             patch.object(audio_mod.time, 'sleep', lambda _s: None):
            threads = [
                threading.Thread(target=self._swallow, daemon=True) for _ in range(3)
            ]
            for t in threads:
                t.start()
            gate.set()
            for t in threads:
                t.join(5.0)

        # Every terminate must sit between an exit and the next enter.
        depth = 0
        for name in events:
            if name == 'open-enter':
                depth += 1
            elif name == 'open-exit':
                depth -= 1
            elif name == 'terminate':
                self.assertEqual(
                    depth, 0,
                    'PortAudio was reset while a thread was inside Pa_OpenStream',
                )

    def _swallow(self):
        try:
            self.rec._ensure_stream_open()
        except Exception:
            pass

    def test_a_close_cannot_race_an_open(self):
        """The wedged engine also had a thread stuck in AudioOutputUnitStop.

        The idle-close timer fires 0.6 s after every recording, and the device
        watcher calls `shutdown()` on its own thread — either can land on top of
        an `arm()` that is still inside `Pa_OpenStream`. A close is a CoreAudio
        call like any other, so it belongs behind the same lock.
        """
        inside = {'n': 0, 'lock': threading.Lock()}
        peak: list[int] = []
        closing = threading.Event()

        def _enter():
            with inside['lock']:
                inside['n'] += 1
                peak.append(inside['n'])

        def _exit():
            with inside['lock']:
                inside['n'] -= 1

        # A stream already open from the previous dictation, whose stop() blocks
        # the way AudioOutputUnitStop does when the HAL is contended.
        existing = unittest.mock.MagicMock()

        def slow_stop():
            _enter()
            closing.set()
            time.sleep(0.2)
            _exit()

        existing.stop.side_effect = slow_stop
        self.rec._stream = existing

        def slow_open(**_kwargs):
            _enter()
            time.sleep(0.1)
            _exit()
            return unittest.mock.MagicMock()

        with patch.object(audio_mod.sd, 'InputStream', slow_open):
            closer = threading.Thread(target=self.rec._close_stream, daemon=True)
            closer.start()
            closing.wait(2.0)      # the close is inside CoreAudio right now
            opener = threading.Thread(target=self.rec._ensure_stream_open, daemon=True)
            opener.start()
            closer.join(3.0)
            opener.join(3.0)

        self.assertEqual(
            max(peak, default=0), 1,
            'an open overlapped a close — two threads inside CoreAudio',
        )

    def test_arm_never_raises_into_the_hotkey_thread(self):
        """Arming is an optimisation; only start() is worth an error."""
        with patch.object(
            audio_mod.sd, 'InputStream',
            unittest.mock.Mock(side_effect=RuntimeError('no device')),
        ), patch.object(audio_mod.time, 'sleep', lambda _s: None):
            self.rec.arm()          # must not raise
        self.assertIsNone(self.rec._stream)

    def test_start_still_reports_a_device_that_will_not_open(self):
        """Swallowing in arm() must not make a dead mic silent in start()."""
        with patch.object(
            audio_mod.sd, 'InputStream',
            unittest.mock.Mock(side_effect=RuntimeError('no device')),
        ), patch.object(audio_mod.time, 'sleep', lambda _s: None):
            with self.assertRaises(RuntimeError):
                self.rec.start()
        self.assertFalse(self.rec._capturing)
