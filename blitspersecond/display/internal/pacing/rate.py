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

The match is tight -- 0.3% -- because a real display barely moves.

A refresh rate is not declared, it is derived: ``pixel_clock / (htotal *
vtotal)``, with the pixel clock quantised to 10kHz in EDID and htotal/vtotal
integers. The "60Hz" in a settings dialog is a rounded label on that
arithmetic, not a claim the panel can then miss. So the only legitimate spread
is label rounding: the 1000/1001 family (60 -> 59.940, 144 -> 143.856) at
0.1%, with timing granularity below that. So 0.3% is roughly triple the
largest legitimate deviation there is.

What sets the delivered rate is the SOURCE's pixel clock, and it survives the
link intact. DisplayPort does not carry a video clock; the sink regenerates
one as ``Mvid/Nvid * link_symbol_clock``, with Mvid dynamically counted
against that same incoming link clock and trimmed by a FIFO fill-level servo.
Spread-spectrum clocking was the obvious way this could have gone wrong -- DP
SSC is a down-spread of around 0.5%, comfortably outside this tolerance -- but
the modulation appears in both terms of that ratio and cancels, which is the
mechanism's whole point. Checked 2026-08-26 rather than assumed. So the rate
arriving at the panel is oscillator-grade accurate to the timing the source
generated, and the label is the only thing that rounds.

No spec clause stating a refresh-rate accuracy percentage turned up, so treat
0.3% as an engineering line rather than a conformance test -- but a display
outside it is the source generating different timing or frames not being
delivered, not a panel legitimately running slow.

The failure modes are asymmetric, which is what settles the direction to err
in. Too tight and no candidate matches: ``_match`` returns None, the engine
keeps the rate it already has, and the only cost is a re-cadence that does
not happen. Too loose and it re-cadences onto a display the window is not on
-- at the previous 5% a game running 137-151fps on a 165Hz display matched a
connected 144Hz output and rebuilt the simulation grid around it, which is the
exact bug ``_retune`` exists to prevent, inverted. It also brought the
165/170/175/180 modes within one tolerance of each other.

**Variable refresh is out of scope, not unhandled.** A VRR display has a range
rather than a rate, which would dissolve every assumption here. It does not
arise: the engine never takes exclusive fullscreen -- "fullscreen" is
borderless at the active desktop mode (see ``Surface``) -- so adaptive sync is
the compositor's business and the window is paced by the desktop mode either
way.

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
        match_tolerance: float = 0.003,
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
