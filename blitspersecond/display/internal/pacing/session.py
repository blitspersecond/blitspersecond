"""The frame pacer: one question, asked once per presentation slot.

**Is this slot the one the pending frame belongs on?** Yes, and it goes up
and the engine is called for the next one. No, and it is held. That is the
whole algorithm. There is no cadence table, no rational, and nothing
computed ahead of time -- the familiar 3,2,3,2,2 at 60fps on a 144Hz panel is
what that question ANSWERS, not what it consults.

A frame belongs on the first slot at or after its deadline, and its deadline
is `origin + frame * step` -- exact, from the engine's own frame count. Two
properties fall out of that and the engine depends on both:

* **Never early.** A frame cannot be shown before its deadline, so the game
  can never run fast, and a frame the engine finished early is HELD rather
  than shown into the previous frame's slot.
* **The engine gets the whole gap.** The engine is called immediately after
  the swap, so its budget is the interval to the next slot it must make --
  13.89 to 20.83ms at 60-on-144, never the 1.39ms of slack that hitting an
  ideal instant would leave it.

Overrun needs no rule. If the engine runs past the slot its frame was due
on, that slot's answer was simply "not ready"; the next slot answers yes and
the origin re-anchors to where the frame actually landed. The cycle restarts
from there, which hands the recovering frame the FRONT gap -- `ceil(refresh /
rate)`, provably the largest gap in the schedule, so the frame most likely to
be in trouble gets the most room. Time lost stays lost: the engine measures
in frames, and a slowdown is affordable where a compressed frame is not.

This replaced a presentation-locked cadence that GRANTED ticks, where a
withheld present withheld simulation and a compositor charging an extra
refresh for a narrowly missed vblank ran the game at 75% speed. Nothing here
can do that: the pacer decides when a frame is SHOWN and never how many times
the engine runs. See `docs/bps/DECOUPLED_PRESENTATION.md`.
"""

from math import ceil, isfinite
from time import perf_counter, perf_counter_ns, sleep
from typing import TYPE_CHECKING

from blitspersecond.system import Logger

from .health import FlipHealth, PresentationMonitor
from .path import detect_presentation_path
from .sink import FramePacingSinkFactory

if TYPE_CHECKING:
    from blitspersecond.display.display import Display


# How far before a slot the compositor stops accepting content for it. A swap
# landing inside this window is shown at the FOLLOWING slot, not this one, so
# the pacer treats the slot as already gone.
#
# PROVISIONAL. 1ms is a conservative Linux placeholder, not a measurement --
# DWM and Mutter both compose ahead of their own vblank and neither publishes
# the figure. PresentMon's MsBetweenDisplayChange is what can calibrate this
# properly, which is why the board card carries the capture recipe.
_LATCH_MARGIN = 0.001

# Presentation rate assumed when the platform will not say what the panel
# runs at. Only the slot grid depends on it, so a wrong guess costs an uneven
# present pattern rather than a game running at the wrong speed.
_ASSUMED_REFRESH = 60.0


class PresentationSession:
    """Display-owned frame pacing for one invocation of the machine loop."""

    def __init__(
        self,
        display: "Display",
        *,
        target_rate: int,
        step: float,
        logger: Logger,
        frame_pacing_factory: FramePacingSinkFactory,
    ) -> None:
        self._display = display
        self._logger = logger
        self._step = step

        refresh_rate = display.refresh_rate
        # A reported mode is only trustworthy if it is finite and at least
        # 1Hz. Treat every invalid value as unavailable.
        if refresh_rate is not None and (
            not isfinite(refresh_rate) or refresh_rate < 1
        ):
            logger.error(
                f"Display reported an unusable refresh rate ({refresh_rate!r}); "
                f"pacing against {_ASSUMED_REFRESH:.0f}Hz instead."
            )
            refresh_rate = None
        self._refresh_rate = refresh_rate or _ASSUMED_REFRESH
        self._period = 1.0 / self._refresh_rate

        presentation_path = detect_presentation_path()
        logger.info(f"Presentation path: {presentation_path.description}.")
        # Win32 reports the current monitor for a window rather than the one
        # it was created on, so a window dragged to another output can have
        # its slot grid corrected. It corrects nothing about the simulation.
        self._tracks_window_output = presentation_path.name == "win32"
        self._frame_pacing = frame_pacing_factory(
            target_fps=target_rate,
            refresh_hz=refresh_rate,
            cadence="decoupled",
            presentation_path=presentation_path.description,
        )

        self._origin = 0.0
        self._monitor = PresentationMonitor()
        self._health = FlipHealth.OK
        self._promoted = False
        self._slipped = 0
        self._last_shown = 0.0
        self._interval = 0.0

        logger.info(
            f"Pacing frames against a {self._refresh_rate:.3f}Hz slot grid "
            f"({'measured' if refresh_rate else 'assumed'})."
        )

    # -- the grid ------------------------------------------------------------

    def start(self) -> None:
        """Anchor frame zero. Called by the loop as servicing begins.

        Never during warm-up: opening a window can block on slow hardware and
        an origin set then would already be far in the past, which reads as a
        first-frame stall rather than as a slow start.
        """
        self._origin = perf_counter()

    def deadline(self, frame: int) -> float:
        """When frame `frame` is due: exact, from the engine's own count.

        Never accumulated. `+= step` sixty times a second throws the grid
        position away and lets it walk against the panel's; this cannot
        drift, so ordinary jitter never moves the schedule.
        """
        return self._origin + frame * self._step

    def next_slot(self, after: float) -> float:
        """The first slot boundary at or after `after`."""
        return ceil(after / self._period - 1e-9) * self._period

    def slot_for(self, frame: int) -> float:
        """The slot this frame belongs on: the first one at or after its due
        time. THE question, answered arithmetically rather than polled."""
        return self.next_slot(self.deadline(frame))

    def wait_for(self, frame: int) -> None:
        """Sleep until it is time to put `frame` on screen.

        The target is the frame's slot MINUS the latch margin, because the
        compositor stops accepting content for a slot some way before the
        vblank itself. Submitting at the boundary would hand the frame to the
        FOLLOWING slot -- a whole schedule silently one slot late, with every
        submission-side metric reading identically clean.

        **No spin.** `sleep` is a high-resolution wait on both platforms of
        interest, and burning a core to sharpen it bought accuracy that was
        already there. Measured on Linux/CPython 3.14: waking lands within
        0.080ms of target worst case, and the submit still arrives 0.906ms
        (p50) ahead of its slot out of the 1ms budget -- only ~90us of margin
        is lost to wake error plus the work in between. The spin cost 13.4%
        of a core against 1.9% without it, for no measurable accuracy.
        Python has used a high-resolution waitable timer on Windows since
        3.11, so the same should hold there; it has not been measured.

        This also drops the wake rate from the PANEL rate to the FRAME rate
        -- one wake per frame rather than one per slot to poll. At 60fps on a
        144Hz panel, 60 wakes a second rather than 144.

        Returns immediately when the slot has already passed, which is the
        overrun case: the frame goes up at once and `anchor` restarts the
        cycle from where it actually landed.
        """
        remaining = self.slot_for(frame) - _LATCH_MARGIN - perf_counter()
        if remaining > 0:
            sleep(remaining)

    def anchor(self, frame: int, shown: float) -> None:
        """Re-anchor the grid when a frame landed later than it was due.

        Restarts the cycle from where the frame actually went up, rather than
        leaving every subsequent deadline in the past -- which would make the
        pacer answer "yes" at every slot and sprint at panel rate, the
        catch-up this design exists to refuse.
        """
        ideal = self.deadline(frame)
        if shown > ideal + self._period:
            self._slipped += 1
            self._origin = shown - frame * self._step

    @property
    def interval(self) -> float:
        """Wall seconds between the last two frames reaching the screen.

        The honest answer to "how fast is this actually running", and the
        only place it can be measured -- the clock counts frames and has no
        wall clock to compare them against. Zero until two frames have gone
        up.
        """
        return self._interval

    @property
    def slipped(self) -> int:
        """Frames shown later than the slot they were due on."""
        return self._slipped

    @property
    def promoted(self) -> bool:
        """Whether the last swap carried a new frame rather than the held one."""
        return self._promoted

    @property
    def refresh_rate(self) -> float:
        return self._refresh_rate

    # -- lifecycle edges -----------------------------------------------------

    def backgrounded(self) -> None:
        """Stop presenting. The engine keeps ticking on its own count: hiding
        a window changes what is DISPLAYED, not what the simulation rate is."""

    def foregrounded(self) -> None:
        """Restart presentation without stale health evidence."""
        if self._tracks_window_output:
            refresh_rate = self._display.poll_refresh_rate(force=True)
            if refresh_rate is not None:
                self._adopt(refresh_rate)
        self._monitor.reset()
        self._health = FlipHealth.OK

    def resumed(self, frame: int) -> None:
        """Re-anchor after a suspension: the gap was not simulation."""
        self._origin = perf_counter() - frame * self._step

    def _adopt(self, refresh_rate: float) -> None:
        """Re-pace for a window that moved to another output."""
        if refresh_rate == self._refresh_rate or refresh_rate < 1:
            return
        previous = self._refresh_rate
        self._refresh_rate = refresh_rate
        self._period = 1.0 / refresh_rate
        self._logger.info(
            f"Presentation rate changed ({previous:.3f} -> "
            f"{refresh_rate:.3f}Hz); new slot grid. Simulation is unaffected."
        )

    # -- instrumentation hooks ----------------------------------------------

    def begin_iteration(self) -> None:
        if self._frame_pacing is not None:
            self._frame_pacing.begin_iteration()

    def after_dispatch(self) -> None:
        if self._tracks_window_output:
            refresh_rate = self._display.poll_refresh_rate()
            if refresh_rate is not None:
                self._adopt(refresh_rate)
        if self._frame_pacing is not None:
            self._frame_pacing.after_dispatch()

    def after_simulation(self) -> None:
        if self._frame_pacing is not None:
            self._frame_pacing.after_simulation()

    # -- the swap ------------------------------------------------------------

    def present(self, frame: int) -> bool:
        """Put the pending frame up. Returns whether the swap stream stalled."""
        if self._frame_pacing is not None:
            self._frame_pacing.after_prepare()

        deadline_error = perf_counter() - self.deadline(frame)
        # Promote-and-swap in one call: promotion is deliberately late, so
        # the frame the engine just finished goes up on THIS slot rather than
        # waiting for the next one.
        self._promoted = self._display.present()
        shown = perf_counter()
        self._interval = shown - self._last_shown if self._last_shown else 0.0
        self._last_shown = shown
        presented_ns = (
            perf_counter_ns() if self._frame_pacing is not None else 0
        )
        self.anchor(frame, shown)

        swap_elapsed = self._display.swap_duration
        self._health = self._monitor.observe(swap_elapsed)
        if self._frame_pacing is not None:
            self._frame_pacing.record_present(
                presented_ns=presented_ns,
                ticks=1 if self._promoted else 0,
                recomposed=self._promoted,
                swap_seconds=swap_elapsed,
                deadline_seconds=deadline_error,
                pacing_mode="slot",
                flip_health=self._health.name.lower(),
            )
        return self._health is FlipHealth.STALLED

    def finish(self) -> None:
        """Persist optional frame-pacing instrumentation after loop exit."""
        if self._frame_pacing is None:
            return
        try:
            self._frame_pacing.save()
        except Exception as error:
            self._logger.error(f"Could not save frame-pacing CSV: {error}")
        else:
            dropped = (
                f", {self._frame_pacing.dropped} rows dropped at capacity"
                if self._frame_pacing.dropped
                else ""
            )
            self._logger.info(
                f"Frame-pacing CSV saved to {self._frame_pacing.path} "
                f"({self._frame_pacing.count} rows{dropped})."
            )
