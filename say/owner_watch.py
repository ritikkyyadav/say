"""Tie the engine's lifetime to the app that launched it.

The engine is started through LaunchServices (`open -n`), which detaches it:
its parent becomes launchd, so PPID says nothing about who owns it and the
process cheerfully outlives the window that spawned it. That is how quitting
Say — or worse, Force Quitting it — could leave a dictation engine running with
a live Fn event tap and a microphone it is willing to open, while the user has
no window, no menu-bar item and no Dock icon telling them it is there. Nothing
was being recorded without a keypress, but "you cannot see it and cannot stop
it" is a trust problem whether or not it is a privacy one, and the honest fix
is for the engine to leave when the app does.

The shell therefore passes its own pid as `--owner-pid` and this watchdog
brings the engine down the moment that process disappears. **This side has to
be the guarantee, not the shell's own teardown**: Force Quit is SIGKILL, which
the shell can neither catch nor act on, so any shutdown that runs inside the
shell is only ever the fast path.

Two mechanisms, because neither is allowed to be a single point of failure:
`kqueue`/`NOTE_EXIT` is exact and fires the instant the owner exits — and being
registered against the *process* rather than the number, it cannot be fooled by
pid reuse — while a `kill(pid, 0)` poll runs alongside as a backstop for a
refused registration or a missed notification.

The one rule that outranks promptness: **an unreadable process state means
alive.** Guessing "gone" would tear down the engine mid-dictation, which is a
worse failure than lingering a second longer.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable

from logging_setup import get_logger

_log = get_logger('owner_watch')

# How often the backstop poll runs, and the cap on how long a kqueue wait
# blocks before it re-checks the stop flag. One second is imperceptible to a
# user who just quit and costs nothing while idle.
DEFAULT_POLL_S = 1.0

# How long the graceful shutdown gets after the owner exits before the process
# leaves regardless. Generous enough for a normal teardown, short enough that a
# quit still feels like a quit.
EXIT_GRACE_S = 5.0


def process_alive(pid: int) -> bool:
    """Is `pid` a live process?

    Signal 0 performs the permission and existence checks without delivering
    anything. Only `ProcessLookupError` means gone — `PermissionError` means it
    exists and belongs to someone else, and any other OSError is an answer we
    could not read. Both of those report alive on purpose: this result gates
    shutting the engine down, so the safe default is to keep running.
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as exc:
        _log.debug('Could not read the state of pid %s (%s) — assuming alive', pid, exc)
        return True
    return True


class OwnerWatchdog:
    """Call `on_owner_exit` once, when `owner_pid` stops existing.

    `start()` returns False when there is nothing to watch (no owner pid, or a
    pid that cannot be an owner), which is the normal case for the menu-bar and
    dev entry points — those are launched by the user and own themselves.
    """

    def __init__(
        self,
        owner_pid: int | None,
        on_owner_exit: Callable[[], None],
        *,
        poll_interval: float = DEFAULT_POLL_S,
    ) -> None:
        self._owner_pid = int(owner_pid) if owner_pid else 0
        self._on_owner_exit = on_owner_exit
        self._poll_interval = max(0.05, float(poll_interval))
        self._stop = threading.Event()
        self._fired = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def owner_pid(self) -> int:
        return self._owner_pid

    @property
    def watching(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> bool:
        """Begin watching. Returns True if a watch is now in effect.

        A pid of 0 or 1 is rejected rather than watched: 1 is launchd, which
        never exits, so watching it would silently mean watching nothing.
        """
        if self._owner_pid <= 1:
            _log.debug('No owner pid to watch (%s) — engine owns itself', self._owner_pid)
            return False

        # Already gone before we even started — the app was quit during the
        # engine's boot (model load alone can take seconds). Fire inline so the
        # caller sees it immediately rather than a poll interval later.
        if not process_alive(self._owner_pid):
            _log.warning(
                'Owner process %s was already gone at startup — engine will not stay up',
                self._owner_pid,
            )
            self._fire()
            return True

        self._thread = threading.Thread(
            target=self._run, daemon=True, name='say-owner-watch'
        )
        self._thread.start()
        _log.info('Watching owner pid %s; the engine exits when it does', self._owner_pid)
        return True

    def stop(self) -> None:
        """Stop watching. Safe to call from the shutdown path, and repeatedly."""
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=self._poll_interval + 0.5)

    def _fire(self) -> None:
        """Run the callback exactly once, and never raise into the caller."""
        if self._fired.is_set():
            return
        self._fired.set()
        try:
            self._on_owner_exit()
        except Exception:
            _log.exception('Owner-exit callback failed')

    def _register_kqueue(self):
        """Ask the kernel to tell us when the owner exits. None if unavailable.

        Registration is against the running process, so a pid recycled onto a
        new process afterwards cannot masquerade as our owner.
        """
        import select

        if not hasattr(select, 'kqueue'):
            return None
        try:
            kq = select.kqueue()
            kq.control(
                [
                    select.kevent(
                        self._owner_pid,
                        filter=select.KQ_FILTER_PROC,
                        flags=select.KQ_EV_ADD | select.KQ_EV_ENABLE,
                        fflags=select.KQ_NOTE_EXIT,
                    )
                ],
                0,
                0,
            )
            return kq
        except Exception as exc:
            # ESRCH here just means the owner died between the liveness check
            # and now; the poll below notices on its first pass either way.
            _log.debug('kqueue watch on pid %s unavailable (%s) — polling instead',
                       self._owner_pid, exc)
            return None

    def _run(self) -> None:
        kq = self._register_kqueue()
        try:
            while not self._stop.is_set():
                if kq is not None:
                    try:
                        if kq.control(None, 1, self._poll_interval):
                            break  # NOTE_EXIT — the owner is gone
                    except Exception:
                        # Fall back to polling rather than abandoning the watch.
                        _log.debug('kqueue wait failed — falling back to polling',
                                   exc_info=True)
                        kq = None
                        continue
                else:
                    self._stop.wait(self._poll_interval)
                    if self._stop.is_set():
                        return
                # Backstop, and the sole mechanism when kqueue is unavailable.
                if not process_alive(self._owner_pid):
                    break
            else:
                return
        finally:
            if kq is not None:
                try:
                    kq.close()
                except Exception:
                    pass

        if not self._stop.is_set():
            self._fire()


def arm_exit_backstop(
    *,
    grace_s: float = EXIT_GRACE_S,
    cleanup: Callable[[], None] | None = None,
    exit_fn: Callable[[int], None] | None = None,
) -> threading.Timer:
    """Guarantee the process leaves, even if the graceful shutdown stalls.

    The clean path can block — a wedged CoreAudio device is the known case in
    this engine, and `recorder.shutdown()` sits right in the middle of it — and
    "the app is closed, so dictation is off" must not be contingent on every
    subsystem behaving. Without this, one hung device call reproduces the exact
    symptom the owner watch exists to prevent.

    `cleanup` runs first and is only for the few things that outlive the
    process if they are skipped (the user's Fn-key mapping, the stale
    handshake). It can never prevent the exit: a hard exit that leaves a little
    mess behind still beats an engine the user cannot see or stop.

    The timer is a daemon, so winning the race costs nothing — a process that
    exits normally takes it down with it.
    """
    resolved_exit = exit_fn if exit_fn is not None else os._exit

    def _fire() -> None:
        _log.error(
            'Shutdown did not finish %.1fs after the app exited — leaving hard', grace_s
        )
        if cleanup is not None:
            try:
                cleanup()
            except Exception:
                _log.exception('Cleanup before the hard exit failed')
        resolved_exit(0)

    timer = threading.Timer(grace_s, _fire)
    timer.daemon = True
    timer.start()
    return timer
