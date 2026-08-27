"""Presentation pacing for one run of the machine loop.

The simulation clock used to live here, and most of this file was it:
dividing presentation slots into game ticks, measuring the delivered rate to
keep that division honest, and detecting whether the swap was pacing the loop
at all. None of that is presentation's business any more. The engine holds
the authored rate on `blitspersecond.clock.Clock`, and this owns the one
question that is genuinely display's: **when does the next frame go up?**

Every path presents on a deadline grid with client vsync off. That used to be
a per-platform decision -- Gamescope and Win32 by nature, direct XWayland
because Mutter charges a whole additional refresh when a prepared frame
narrowly misses vblank, which measured as roughly 45ms frames and a game
running at 75% of its authored rate. The defect was never the penalty; it was
that a doubled presentation interval became a doubled simulation interval,
because they were the same clock. They are not any more, so the special case
became the general rule. See `docs/bps/DECOUPLED_PRESENTATION.md`.

What this means for the panel rate: it is used ONLY to pace presentation.
Being 0.1% wrong about it is invisible, where being 0.1% wrong about the
simulation rate was what the deleted machinery existed to prevent.
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


# How early a coarse sleep hands over to spin-polling. The spin burns at most
# this much CPU per frame and buys the accuracy `sleep` will not give.
_SPIN_MARGIN = 0.002

# Presentation rate assumed when the platform will not say what the panel
# runs at. Only presentation cadence depends on it, so a wrong guess costs
# an uneven present pattern rather than a game running at the wrong speed.
_ASSUMED_REFRESH = 60.0


class PresentationSession:
    """Display-owned presentation pacing for one invocation of the loop."""

    def __init__(
        self,
        display: "Display",
        *,
        target_rate: int,
        spin_pacing: bool,
        logger: Logger,
        frame_pacing_factory: FramePacingSinkFactory,
    ) -> None:
        self._display = display
        # Kept only as capture metadata: a frame-pacing CSV is comparable
        # against another only if it says what rate it was recording.
        # Presentation itself no longer knows or needs the authored rate.
        self._spin_pacing = spin_pacing
        self._logger = logger

        refresh_rate = display.refresh_rate
        # A reported mode is only trustworthy if it is finite and at least
        # 1Hz. Treat every invalid value as unavailable.
        if refresh_rate is not None and (
            not isfinite(refresh_rate) or refresh_rate < 1
        ):
            logger.error(
                f"Display reported an unusable refresh rate ({refresh_rate!r}); "
                f"presenting at {_ASSUMED_REFRESH:.0f}Hz instead."
            )
            refresh_rate = None
        self._refresh_rate = refresh_rate or _ASSUMED_REFRESH

        presentation_path = detect_presentation_path()
        logger.info(f"Presentation path: {presentation_path.description}.")
        # Win32 reports the current monitor for a window rather than the one
        # it was created on, so a window dragged to another output can still
        # have its PRESENTATION cadence corrected. It no longer corrects
        # anything about the simulation, which is why nothing else needs it.
        self._tracks_window_output = presentation_path.name == "win32"
        self._frame_pacing = frame_pacing_factory(
            target_fps=target_rate,
            refresh_hz=refresh_rate,
            cadence="decoupled",
            presentation_path=presentation_path.description,
        )

        self._period = 1.0 / self._refresh_rate
        self._next_present = perf_counter() + self._period
        self._monitor = PresentationMonitor()
        self._health = FlipHealth.OK
        self._promoted = False

        logger.info(
            f"Presenting on a {self._refresh_rate:.3f}Hz deadline grid "
            f"({'measured' if refresh_rate else 'assumed'})."
        )

    @property
    def deadline(self) -> float:
        """`perf_counter` value at which the next presentation is due.

        Published so the loop can wait on whichever of its two deadlines --
        this one or the clock's -- falls first. Neither divides the other.
        """
        return self._next_present

    def due(self) -> bool:
        """Whether a presentation is owed now."""
        return perf_counter() >= self._next_present

    def slot_at_or_after(self, when: float) -> float:
        """The first presentation slot boundary at or after `when`.

        This is the budget the engine paces against. Display owns the grid
        and publishes points on it; the engine decides what to do with them
        and keeps counting its own frames either way -- so a withheld
        present can never withhold a tick, which is the whole difference
        between this and the cadence that used to grant them.

        Snapping ticks to slot boundaries is what makes the present pattern
        deterministic. Tick k lands on slot `ceil(k * step / period)`, so at
        60fps on 144Hz the slots are 0, 3, 5, 8, 10, 12 -- a repeating
        3,2,3,2,2. That is the old `PresentationCadence` arithmetic arriving
        for free, out of the geometry rather than out of a `Fraction`, and it
        is the same ceiling schedule that class settled on: frame one lands
        at slot 3 because slots 1 and 2 would give it 6.94ms or 13.89ms
        against a 16.67ms budget.

        Measured from the live deadline rather than a stored origin, so a
        grid re-phased by a resume or an output change carries the ticks
        with it without anyone having to publish the change.
        """
        return self._next_present + ceil(
            (when - self._next_present) / self._period
        ) * self._period

    @property
    def promoted(self) -> bool:
        """Whether the last present carried a new frame rather than the held
        one. False is the ordinary case above the crossover, not a fault."""
        return self._promoted

    def wait(self) -> None:
        """Hold until the next presentation is due: coarse sleep, then spin."""
        remaining = self._next_present - perf_counter()
        if self._spin_pacing:
            if remaining > _SPIN_MARGIN:
                sleep(remaining - _SPIN_MARGIN)
            while perf_counter() < self._next_present:
                pass
        elif remaining > 0:
            sleep(remaining)

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

    def _adopt(self, refresh_rate: float) -> None:
        """Re-pace presentation for a window that moved to another output."""
        if refresh_rate == self._refresh_rate or refresh_rate < 1:
            return
        previous = self._refresh_rate
        self._refresh_rate = refresh_rate
        self._period = 1.0 / refresh_rate
        # The old deadline belongs to the old period.
        self._next_present = perf_counter() + self._period
        self._logger.info(
            f"Presentation rate changed ({previous:.3f} -> "
            f"{refresh_rate:.3f}Hz); presenting on the new grid. Simulation "
            f"is unaffected."
        )

    def backgrounded(self) -> None:
        """Stop presenting. The loop keeps ticking on the engine clock, which
        is the whole point of the clock not living here: a hidden window
        changes what is DISPLAYED, not what the simulation rate is."""

    def foregrounded(self) -> None:
        """Restart presentation without stale health evidence."""
        if self._tracks_window_output:
            refresh_rate = self._display.poll_refresh_rate(force=True)
            if refresh_rate is not None:
                self._adopt(refresh_rate)
        self._monitor.reset()
        self._health = FlipHealth.OK
        self._next_present = perf_counter() + self._period

    def resumed(self) -> None:
        """Re-phase the presentation grid after a suspension."""
        self._next_present = perf_counter() + self._period

    def present(self) -> bool:
        """Present the frame display is holding; return whether it stalled."""
        if self._frame_pacing is not None:
            self._frame_pacing.after_prepare()

        deadline_error = perf_counter() - self._next_present
        # Composition no longer happens here: the engine builds frames on its
        # own clock and this promotes whatever is finished. `promoted` is
        # False when the held frame was re-presented.
        promoted = self._promoted = self._display.present()
        presented_ns = (
            perf_counter_ns() if self._frame_pacing is not None else 0
        )

        # If a long frame or debugger pause missed more than one deadline,
        # resync rather than sprinting to catch up.
        self._next_present += self._period
        if self._next_present < perf_counter():
            self._next_present = perf_counter() + self._period

        swap_elapsed = self._display.swap_duration
        self._health = self._monitor.observe(swap_elapsed)
        if self._frame_pacing is not None:
            self._frame_pacing.record_present(
                presented_ns=presented_ns,
                ticks=0,
                recomposed=promoted,
                swap_seconds=swap_elapsed,
                deadline_seconds=deadline_error,
                pacing_mode="deadline",
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
