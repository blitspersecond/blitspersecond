"""Estimate the display rate actually pacing presentation.

The rate a window is paced at is not a property of the run; it is a property
of the output the window happens to be on, and that changes when the window
moves or a display is plugged in. The platform does not report the change:
under XWayland an X11 client cannot query which Wayland output paces its
surface, and neither pyglet's ``Window.screen`` nor its mode rate is
re-resolved after construction.

So the rate is measured, from the interval between delivered presentations,
and two guards keep the measurement from lying.

**Steadiness.** A window whose rectangle overlaps two outputs can be paced by
an interleaving of both their vblanks. Measured on GNOME/Mutter, that stream's
median is a rate no display is running at -- 69Hz between a 144Hz and a 60Hz
output -- so a median alone is not evidence. A large majority of the ring has
to agree with it before it counts.

**Candidacy.** A steady rate is only accepted when it matches the current mode
rate of some connected output. This is what separates "the window moved to a
60Hz display" from "we are dropping every other frame on a 144Hz display".
Both deliver a steady 60 presentations per second; only the first should
re-cadence. The second is a slow game, and ``PresentationCadence`` is
deliberately built so that a slow presentation stream makes the game slow
rather than hiding it.

The estimator is only meaningful where the swap blocks on vsync. On a
deadline-grid path -- Gamescope, Win32/DWM -- the interval is the engine's own
deadline and proves nothing about the display, so the caller must not feed it
there.
"""

from __future__ import annotations

from collections import deque
from statistics import median
from typing import Callable, Sequence


class DeliveredRate:
    """Rolling estimate of the presentation rate, or None while unproven."""

    def __init__(
        self,
        outputs: Callable[[], Sequence[float]],
        *,
        window: int = 20,
        steady_tolerance: float = 0.08,
        agreement: float = 0.8,
        match_tolerance: float = 0.05,
        implausible_interval: float = 0.25,
        recheck_after: int = 180,
    ) -> None:
        if window < 2:
            raise ValueError("window must be at least 2 intervals")
        if not 0.0 < agreement <= 1.0:
            raise ValueError("agreement must be a fraction above zero")
        self._outputs = outputs
        self._window = window
        self._steady_tolerance = steady_tolerance
        self._agreement = agreement
        self._match_tolerance = match_tolerance
        self._implausible_interval = implausible_interval
        self._recheck_after = recheck_after

        self._intervals: deque[float] = deque(maxlen=window)
        self._candidates: tuple[float, ...] | None = None
        self._since_miss = 0

    def reset(self) -> None:
        """Discard accumulated evidence.

        Every interval spanning a discontinuity -- a suspension, a
        backgrounded stretch, a re-cadence -- describes the gap rather than
        the display, so the caller resets across all of them.
        """
        self._intervals.clear()

    def refresh_outputs(self) -> None:
        """Re-read the connected outputs now.

        Measured at 0.9ms on the development desktop -- 13% of a 144Hz frame,
        because pyglet's ``get_screens()`` performs a full XRandR resource
        query. Far too expensive to do per frame, so it is done once at
        construction (during engine startup, where slow work belongs), again
        whenever presentation restarts, and otherwise only on the slow timer
        in ``_match``.
        """
        try:
            self._candidates = tuple(
                float(value) for value in self._outputs() if value
            )
        except Exception:
            # An unreadable output list only costs the ability to re-cadence;
            # the caller keeps running on the rate it already has.
            self._candidates = ()
        self._since_miss = 0

    def observe(self, interval: float) -> float | None:
        """Feed one present-to-present interval; return a proven rate or None."""
        if interval <= 0.0 or interval > self._implausible_interval:
            # A stalled or withheld swap stream says nothing about the rate,
            # and one such interval would poison the whole ring.
            self._intervals.clear()
            return None

        self._intervals.append(interval)
        if len(self._intervals) < self._window:
            return None

        centre = median(self._intervals)
        if centre <= 0.0:
            return None
        agreeing = sum(
            1
            for value in self._intervals
            if abs(value - centre) <= self._steady_tolerance * centre
        )
        if agreeing < self._agreement * len(self._intervals):
            # Mixed stream: the window is straddling outputs, or presentation
            # is erratic. Either way there is no single rate to adopt.
            return None

        return self._match(1.0 / centre)

    def _match(self, rate: float) -> float | None:
        """Snap a steady rate onto a real output rate, or reject it."""
        nearest = self._nearest(rate)
        if nearest is not None:
            self._since_miss = 0
            return nearest
        # A steady rate matching no known output is either a slow game on a
        # fast display -- correctly rejected -- or a stale output list after a
        # mode change or a hotplug. Re-read the outputs if it persists;
        # enumerating them is far too expensive to do every frame.
        self._since_miss += 1
        if self._since_miss >= self._recheck_after:
            # Pays the enumeration cost only while a steady rate persistently
            # matches nothing -- which is either a display that appeared since
            # the last read, or a game running at a rate no display has. The
            # first must be caught; the second makes this a rare, bounded tax.
            self.refresh_outputs()
        return None

    def _nearest(self, rate: float) -> float | None:
        """The known output rate this one is, or None if it is none of them."""
        if self._candidates is None:
            self.refresh_outputs()
        if not self._candidates:
            return None
        nearest = min(self._candidates, key=lambda value: abs(value - rate))
        if abs(nearest - rate) <= self._match_tolerance * nearest:
            return nearest
        return None
