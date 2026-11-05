"""The engine must not outlive the app that launched it.

These pin the guarantee behind the orphaned-engine bug: quitting Say — including
Force Quit, which the shell cannot catch — left a `--no-menubar` engine running
under launchd with a live Fn tap, invisible and unstoppable from the UI.
"""

import os
import subprocess
import sys
import threading
import time
import unittest
from unittest import mock

import owner_watch
from owner_watch import OwnerWatchdog, arm_exit_backstop, process_alive


def _spawn_sleeper() -> subprocess.Popen:
    """A real child process to watch. Real pids, real exits — no mocking."""
    return subprocess.Popen(
        [sys.executable, '-c', 'import time; time.sleep(30)'],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _dead_pid() -> int:
    """A pid that is definitely not running: spawn, kill, reap."""
    proc = subprocess.Popen([sys.executable, '-c', 'pass'],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    proc.wait()
    return proc.pid


class ProcessAliveTests(unittest.TestCase):
    def test_a_running_process_is_alive(self):
        proc = _spawn_sleeper()
        try:
            self.assertTrue(process_alive(proc.pid))
        finally:
            proc.kill()
            proc.wait()

    def test_an_exited_process_is_not_alive(self):
        self.assertFalse(process_alive(_dead_pid()))

    def test_a_process_we_may_not_signal_is_alive(self):
        # Owned by someone else: it exists, which is the only thing we asked.
        with mock.patch.object(owner_watch.os, 'kill', side_effect=PermissionError):
            self.assertTrue(process_alive(4242))

    def test_an_unreadable_state_counts_as_alive(self):
        # The whole point: this result gates killing the engine. Guessing
        # "gone" from an error we could not read would tear down a live
        # dictation, which is a worse failure than lingering a second longer.
        with mock.patch.object(owner_watch.os, 'kill', side_effect=OSError(999, 'weird')):
            self.assertTrue(process_alive(4242))

    def test_nonsense_pids_are_not_alive(self):
        self.assertFalse(process_alive(0))
        self.assertFalse(process_alive(-1))


class OwnerWatchdogTests(unittest.TestCase):
    def setUp(self):
        self.fired = threading.Event()
        self.calls = []
        self.procs: list[subprocess.Popen] = []

    def tearDown(self):
        for proc in self.procs:
            try:
                proc.kill()
                proc.wait(timeout=2)
            except Exception:
                pass

    def _sleeper(self) -> subprocess.Popen:
        proc = _spawn_sleeper()
        self.procs.append(proc)
        return proc

    def _callback(self):
        self.calls.append(time.monotonic())
        self.fired.set()

    def test_the_engine_exits_when_its_owner_does(self):
        proc = self._sleeper()
        dog = OwnerWatchdog(proc.pid, self._callback, poll_interval=0.1)
        self.assertTrue(dog.start())
        self.assertFalse(self.fired.wait(0.3), 'fired while the owner was still alive')

        proc.kill()
        proc.wait()
        self.assertTrue(self.fired.wait(3.0), 'owner exited but the engine was not told')
        dog.stop()

    def test_a_force_quit_is_noticed_just_like_a_clean_exit(self):
        # Force Quit is SIGKILL: the shell runs no shutdown code at all, so the
        # engine only learns about it by watching. SIGKILL the owner directly.
        proc = self._sleeper()
        dog = OwnerWatchdog(proc.pid, self._callback, poll_interval=0.1)
        self.assertTrue(dog.start())

        os.kill(proc.pid, 9)
        proc.wait()
        self.assertTrue(self.fired.wait(3.0), 'a SIGKILLed owner left the engine running')
        dog.stop()

    def test_polling_alone_still_catches_a_dead_owner(self):
        # kqueue is the fast path, not the guarantee. With it unavailable the
        # backstop poll must still bring the engine down.
        proc = self._sleeper()
        dog = OwnerWatchdog(proc.pid, self._callback, poll_interval=0.1)
        with mock.patch.object(OwnerWatchdog, '_register_kqueue', return_value=None):
            self.assertTrue(dog.start())
            proc.kill()
            proc.wait()
            self.assertTrue(self.fired.wait(3.0), 'the poll backstop never fired')
        dog.stop()

    def test_an_owner_already_gone_at_startup_fires_immediately(self):
        # The app was quit while the engine was still loading its model. It
        # must not finish booting into a fully-armed listener for nobody.
        dog = OwnerWatchdog(_dead_pid(), self._callback, poll_interval=5.0)
        self.assertTrue(dog.start())
        self.assertTrue(self.fired.is_set(), 'did not fire inline for a dead owner')

    def test_an_engine_with_no_owner_is_left_alone(self):
        # Menu-bar and dev launches own themselves; nothing should watch them.
        for pid in (None, 0):
            dog = OwnerWatchdog(pid, self._callback)
            self.assertFalse(dog.start())
        self.assertFalse(self.fired.is_set())

    def test_launchd_is_never_treated_as_an_owner(self):
        # pid 1 never exits, so watching it is indistinguishable from not
        # watching at all — and would silently restore the old behaviour.
        dog = OwnerWatchdog(1, self._callback)
        self.assertFalse(dog.start())
        self.assertFalse(dog.watching)

    def test_stop_ends_the_watch_without_shutting_the_engine_down(self):
        proc = self._sleeper()
        dog = OwnerWatchdog(proc.pid, self._callback, poll_interval=0.1)
        self.assertTrue(dog.start())
        dog.stop()
        self.assertFalse(dog.watching)
        proc.kill()
        proc.wait()
        self.assertFalse(self.fired.wait(0.5), 'fired after the watch was stopped')

    def test_the_shutdown_runs_exactly_once(self):
        proc = self._sleeper()
        dog = OwnerWatchdog(proc.pid, self._callback, poll_interval=0.05)
        self.assertTrue(dog.start())
        proc.kill()
        proc.wait()
        self.assertTrue(self.fired.wait(3.0))
        time.sleep(0.3)
        dog.stop()
        self.assertEqual(len(self.calls), 1)

    def test_a_repeated_trigger_shuts_the_engine_down_only_once(self):
        # A dead owner fires inline from start(); starting again (a retry, or a
        # second wiring of the same watchdog) must not run the shutdown twice.
        # The engine's own _shutdown is idempotent today — this keeps the
        # watchdog from relying on that.
        dog = OwnerWatchdog(_dead_pid(), self._callback, poll_interval=5.0)
        self.assertTrue(dog.start())
        self.assertTrue(dog.start())
        self.assertEqual(len(self.calls), 1)

    def test_a_failing_callback_cannot_escape_the_watchdog(self):
        proc = self._sleeper()
        boom = mock.Mock(side_effect=RuntimeError('shutdown blew up'))
        dog = OwnerWatchdog(proc.pid, boom, poll_interval=0.05)
        self.assertTrue(dog.start())
        proc.kill()
        proc.wait()
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and not boom.called:
            time.sleep(0.02)
        self.assertTrue(boom.called)
        dog.stop()  # must not raise


class ExitBackstopTests(unittest.TestCase):
    """Quitting must turn dictation off even if the clean shutdown stalls.

    A wedged CoreAudio device can block `recorder.shutdown()`, and that hang
    reproduces the exact symptom the owner watch exists to prevent: an engine
    still running after the app is gone.
    """

    def test_a_stalled_shutdown_still_ends_the_process(self):
        exited = threading.Event()
        timer = arm_exit_backstop(grace_s=0.1, exit_fn=lambda _code: exited.set())
        self.assertTrue(exited.wait(2.0), 'a hung shutdown left the engine running')
        timer.cancel()

    def test_cleanup_runs_before_the_exit(self):
        order = []
        timer = arm_exit_backstop(
            grace_s=0.05,
            cleanup=lambda: order.append('cleanup'),
            exit_fn=lambda _code: order.append('exit'),
        )
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and 'exit' not in order:
            time.sleep(0.02)
        timer.cancel()
        self.assertEqual(order, ['cleanup', 'exit'])

    def test_a_failing_cleanup_cannot_keep_the_engine_alive(self):
        exited = threading.Event()
        timer = arm_exit_backstop(
            grace_s=0.05,
            cleanup=mock.Mock(side_effect=RuntimeError('fn_override wedged')),
            exit_fn=lambda _code: exited.set(),
        )
        self.assertTrue(exited.wait(2.0), 'a failing cleanup blocked the exit')
        timer.cancel()

    def test_a_clean_shutdown_cancels_the_backstop(self):
        exited = threading.Event()
        timer = arm_exit_backstop(grace_s=0.3, exit_fn=lambda _code: exited.set())
        timer.cancel()
        self.assertFalse(exited.wait(0.6), 'exited hard despite shutting down cleanly')


class RuntimeHandshakeTests(unittest.TestCase):
    """runtime.json is how the shell tells its engine from someone else's."""

    def setUp(self):
        import tempfile
        from pathlib import Path

        import server.runtime as runtime_mod

        self.runtime = runtime_mod
        self.tmp = tempfile.mkdtemp(prefix='say-runtime-')
        self._orig = runtime_mod.runtime_path
        runtime_mod.runtime_path = lambda: Path(self.tmp) / 'runtime.json'

    def tearDown(self):
        import shutil

        self.runtime.runtime_path = self._orig
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_sidecar_records_the_app_that_owns_it(self):
        self.runtime.write_runtime(1234, owner_pid=4321)
        self.assertEqual(self.runtime.read_runtime()['owner_pid'], 4321)

    def test_a_hand_started_engine_records_no_owner(self):
        # Absent, not zero: "nobody's to kill" has to be distinguishable from
        # "owned by a process that is gone", which is an orphan.
        self.runtime.write_runtime(1234)
        self.assertNotIn('owner_pid', self.runtime.read_runtime())


if __name__ == '__main__':
    unittest.main()
