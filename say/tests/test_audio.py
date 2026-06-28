"""Tests for AudioRecorder pre-roll seeding and event channel.

We avoid spinning up a real `sounddevice` stream by invoking `_callback`
directly — the same path the real audio thread takes.
"""

import time
import unittest

import config
import numpy as np


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
