"""Observe what the user changed after Say typed for them.

Say's whole quality story is a chain of bets — vocabulary repair, formatting,
per-app profiles, any future semantic tier — and until now none of them had a
scorecard. This module supplies one by reading the text field back a few
seconds after an insertion and diffing it against what we put there.

**The privacy contract, which is the reason this module is shaped the way it
is.** Reading the focused field means touching a document the user did not
hand us. So:

  * It only ever runs on the element Say *just injected into*, inside a short
    window it opened itself. There is no polling, no background scanning, and
    nothing observes a field Say never wrote to.
  * The surrounding document is used only to relocate our own span. Those
    anchors live in memory for the length of the window and are then dropped —
    `_Pending` is the only thing that holds them and it is discarded on fire.
  * Only the span Say produced and what that span became are ever persisted.
    A correction row cannot contain text Say did not originate.
  * `config.LEARN_FROM_CORRECTIONS` disables the whole thing, and no grant is
    requested for it: it rides the Accessibility permission injection already
    needs. Without that grant every call here fails closed and silently.

Everything is best-effort. A dictation has already been delivered by the time
any of this runs, and measurement must never be able to damage the thing it is
measuring.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

import config
from logging_setup import get_logger

_log = get_logger('readback')

# How long to wait before reading the field back. Long enough for the user to
# notice a mistake and fix it, short enough that the field usually still
# exists and still holds our span.
DEFAULT_DELAY_S = 8.0

# Characters of surrounding text kept (in memory only) to relocate our span.
# Long enough to be unique in a normal document, short enough to survive the
# user editing nearby.
_ANCHOR_CHARS = 48

# Reading back a huge field costs more than the signal is worth, and a span of
# ours inside one is unlikely to relocate cleanly anyway.
_MAX_FIELD_CHARS = 200_000

# Bound on in-flight observations. Rapid-fire dictation should not accumulate
# timers; the oldest is dropped rather than queued.
_MAX_PENDING = 8


@dataclass
class _Pending:
    """One in-flight observation. Holds document context — never persisted."""

    transcript_id: int
    inserted: str
    target_app: str | None
    element: Any
    prefix: str  # in-memory anchor, discarded when the timer fires
    suffix: str  # in-memory anchor, discarded when the timer fires
    timer: threading.Timer | None = field(default=None, compare=False)


def _ax():
    """Import the Accessibility API lazily; None when unavailable."""
    try:
        import ApplicationServices

        return ApplicationServices
    except Exception:
        return None


def _focused_element() -> Any | None:
    ax = _ax()
    if ax is None:
        return None
    try:
        if not ax.AXIsProcessTrusted():
            return None
        system = ax.AXUIElementCreateSystemWide()
        err, el = ax.AXUIElementCopyAttributeValue(
            system, ax.kAXFocusedUIElementAttribute, None
        )
        # Non-zero is an AXError; -25204 (APIDisabled) is the ungranted case.
        return el if err == 0 else None
    except Exception:
        return None


def _read_value(element: Any) -> str | None:
    """Current text of an element, or None if it is gone / not text."""
    ax = _ax()
    if ax is None or element is None:
        return None
    try:
        err, value = ax.AXUIElementCopyAttributeValue(element, ax.kAXValueAttribute, None)
        if err != 0 or not isinstance(value, str):
            return None
        if len(value) > _MAX_FIELD_CHARS:
            return None
        return value
    except Exception:
        return None


def _enabled() -> bool:
    return bool(getattr(config, 'LEARN_FROM_CORRECTIONS', True))


class CorrectionWatcher:
    """Schedules a read-back per insertion and records what changed.

    Store-parameterized rather than reaching for the singleton, so tests can
    inject one — the same pattern `recordings.py` uses.
    """

    def __init__(self, store: Any, *, delay_s: float | None = None):
        self._store = store
        self._delay = float(
            delay_s
            if delay_s is not None
            else getattr(config, 'CORRECTION_READBACK_DELAY_S', DEFAULT_DELAY_S)
        )
        self._lock = threading.Lock()
        self._pending: list[_Pending] = []

    # -- Public -------------------------------------------------------------

    def observe(
        self, transcript_id: int, inserted: str, target_app: str | None = None
    ) -> bool:
        """Start watching the field Say just wrote into. Never raises.

        Returns True when an observation was armed — useful in tests, ignored
        by the controller, which must not care whether measurement succeeded.
        """
        try:
            return self._observe(transcript_id, inserted, target_app)
        except Exception:
            _log.debug('Correction read-back could not be armed', exc_info=True)
            return False

    def shutdown(self) -> None:
        """Cancel every pending observation."""
        with self._lock:
            pending, self._pending = self._pending, []
        for p in pending:
            if p.timer is not None:
                try:
                    p.timer.cancel()
                except Exception:
                    pass

    # -- Internals ----------------------------------------------------------

    def _observe(
        self, transcript_id: int, inserted: str, target_app: str | None
    ) -> bool:
        if not _enabled() or not inserted.strip() or not transcript_id:
            return False

        element = _focused_element()
        if element is None:
            return False
        value = _read_value(element)
        if value is None:
            return False

        # The cursor sits at the end of what we typed, so when the same text
        # appears more than once ours is the last occurrence.
        idx = value.rfind(inserted)
        if idx < 0:
            # The app reformatted, or injection went somewhere we cannot see.
            # Either way we cannot attribute later edits to our span.
            return False

        end = idx + len(inserted)
        pending = _Pending(
            transcript_id=int(transcript_id),
            inserted=inserted,
            target_app=target_app,
            element=element,
            prefix=value[max(0, idx - _ANCHOR_CHARS):idx],
            suffix=value[end:end + _ANCHOR_CHARS],
        )

        # A non-positive delay reads back inline. Production never does this —
        # the whole point is to wait for the user to react — but it makes the
        # observation deterministic under test, where a zero-length Timer
        # otherwise races the assertions that follow it.
        if self._delay <= 0:
            self._fire(pending)
            return True

        timer = threading.Timer(self._delay, self._fire, args=(pending,))
        timer.daemon = True
        pending.timer = timer

        with self._lock:
            while len(self._pending) >= _MAX_PENDING:
                dropped = self._pending.pop(0)
                if dropped.timer is not None:
                    dropped.timer.cancel()
            self._pending.append(pending)

        timer.start()
        return True

    def _fire(self, pending: _Pending) -> None:
        """Read the field back and record any correction. Never raises."""
        with self._lock:
            if pending in self._pending:
                self._pending.remove(pending)
        try:
            self._compare(pending)
        except Exception:
            _log.debug('Correction read-back failed', exc_info=True)
        finally:
            # Drop the document context the moment it has served its purpose.
            pending.prefix = ''
            pending.suffix = ''
            pending.element = None

    def _compare(self, pending: _Pending) -> None:
        if not _enabled():
            return
        value = _read_value(pending.element)
        if value is None:
            return

        region = _relocate(value, pending.prefix, pending.suffix)
        if region is None:
            # The span could not be located, so this insertion was never
            # actually measured. Leaving it unmarked keeps it out of the
            # denominator — "we did not look" must never score as "clean".
            return

        # From here the insertion *has* been compared, clean or not. That is
        # what makes it countable.
        try:
            self._store.mark_observed(pending.transcript_id)
        except Exception:
            _log.debug('Could not mark insertion observed', exc_info=True)

        if region == pending.inserted:
            return

        import corrections

        d = corrections.diff(pending.inserted, region)
        if not corrections.looks_like_correction(d):
            return

        self._store.record_correction(
            transcript_id=pending.transcript_id,
            source='observed',
            inserted=d.inserted,
            corrected=d.corrected,
            edit_distance=d.distance,
            words=d.words,
            target_app=pending.target_app,
        )
        # Info, not debug: this fires at most once per dictation and is the
        # one line that tells you the measurement loop is actually closing.
        _log.info(
            'Correction observed on transcript %s: %d/%d words changed (%r -> %r)',
            pending.transcript_id, d.distance, d.words,
            d.inserted[:60], d.corrected[:60],
        )


def _relocate(value: str, prefix: str, suffix: str) -> str | None:
    """Find what Say's span has become, using its surrounding anchors.

    Returns None whenever the span cannot be located unambiguously. Giving up
    is the correct outcome: a wrong region produces a fabricated correction,
    which is worse than no data at all because everything downstream would
    then learn from it.
    """
    if prefix:
        start = value.find(prefix)
        if start < 0:
            return None
        start += len(prefix)
    else:
        start = 0

    if suffix:
        end = value.find(suffix, start)
        if end < 0:
            return None
    else:
        end = len(value)

    if end < start:
        return None
    return value[start:end]
