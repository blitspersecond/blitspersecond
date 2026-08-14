"""Per-run presentation cadence, pacing, health, and instrumentation."""

from math import isfinite
from pathlib import Path
import sys
from time import perf_counter, perf_counter_ns, sleep
from typing import Protocol, TYPE_CHECKING

from blitspersecond.system import Logger

from .cadence import PresentationCadence
from .health import FlipHealth, PresentationMonitor
from .path import detect_presentation_path
from .rate import DeliveredRate

if TYPE_CHECKING:
    from blitspersecond.display.display import Display


class FramePacingSink(Protocol):
    """Completed-frame event sink used by presentation instrumentation."""

    path: Path

    @property
    def count(self) -> int: ...

    @property
    def dropped(self) -> int: ...

    def begin_iteration(self) -> None: ...

    def after_dispatch(self) -> None: ...

    def after_simulation(self) -> None: ...

    def after_prepare(self) -> None: ...

    def record_present(
        self,
        *,
        presented_ns: int,
        ticks: int,
        recomposed: bool,
        swap_seconds: float,
        deadline_seconds: float | None,
        pacing_mode: str,
        flip_health: str,
    ) -> None: ...

    def save(self) -> None: ...


class FramePacingSinkFactory(Protocol):
    """Construct an optional sink once presentation metadata is known."""

    def __call__(
        self,
        *,
        target_fps: float,
        refresh_hz: float | None,
        cadence: str,
        presentation_path: str,
    ) -> FramePacingSink | None: ...


# How early a coarse sleep hands over to spin-polling when swap does not pace
# presentation. The spin burns at most this much CPU time per frame.
_SPIN_MARGIN = 0.002


def _log_deadline_grid_pacing(
    logger: Logger,
    *,
    initial_reason: str | None = None,
) -> None:
    """Report the non-blocking-swap path at platform-appropriate severity."""
    if initial_reason == "gamescope":
        logger.info(
            "Gamescope presentation path selected deadline-grid pacing "
            "from the first frame."
        )
        return
    if initial_reason == "experiment":
        logger.info(
            "Frame-pacing experiment requested deadline-grid presentation "
            "from the first frame."
        )
        return
    if sys.platform == "win32":
        logger.info(
            "Win32/DWM returned non-blocking flips; using expected "
            "deadline-grid presentation pacing."
        )
    else:
        logger.warning(
            "flip() is not blocking on vsync; presentation switched to "
            "deadline-grid pacing."
        )


class PresentationSession:
    """Display-owned state for one invocation of the machine loop."""

    def __init__(
        self,
        display: "Display",
        *,
        target_rate: int,
        spin_pacing: bool,
        logger: Logger,
        frame_pacing_factory: FramePacingSinkFactory,
        diagnostic_deadline: bool,
    ) -> None:
        self._display = display
        self._target_rate = target_rate
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
                f"falling back to wall-time pacing."
            )
            refresh_rate = None
        self._refresh_rate = refresh_rate
        self._cadence = (
            PresentationCadence(target_rate, refresh_rate)
            if refresh_rate
            else None
        )
        cadence_label = (
            f"{self._cadence.ratio.numerator}/{self._cadence.ratio.denominator}"
            if self._cadence is not None
            else "wall-time"
        )

        presentation_path = detect_presentation_path()
        logger.info(f"Presentation path: {presentation_path.description}.")
        self._frame_pacing = frame_pacing_factory(
            target_fps=target_rate,
            refresh_hz=refresh_rate,
            cadence=cadence_label,
            presentation_path=presentation_path.description,
        )
        self._start_deadline_reason = (
            "gamescope"
            if presentation_path.starts_on_deadline_grid
            else "experiment"
            if diagnostic_deadline
            else None
        )
        self._start_deadline = self._start_deadline_reason is not None
        self._presentation_period = 1.0 / (refresh_rate or target_rate)
        self._fallback_period = 1.0 / target_rate
        self._fallback_max_debt = self._fallback_period * 3
        self._fallback_accumulator = self._fallback_period
        self._fallback_last = perf_counter()

        if self._cadence is not None:
            logger.info(
                f"Starting presentation-locked loop ({target_rate} FPS sim, "
                f"{self._cadence.refresh_rate:.3f}Hz display, cadence "
                f"{self._cadence.ratio.numerator}/"
                f"{self._cadence.ratio.denominator}, effective "
                f"{self._cadence.effective_rate:.3f} FPS)."
            )
        else:
            logger.info(
                f"Display refresh unavailable; starting wall-time fallback "
                f"({target_rate} FPS sim)."
            )

        self._deadline_grid_reported = False
        self._unthrottled_pacing = self._start_deadline
        self._next_present = perf_counter() + self._presentation_period
        self._monitor = PresentationMonitor(
            initial_health=(
                FlipHealth.UNTHROTTLED
                if self._start_deadline
                else FlipHealth.PROBING
            )
        )
        # Permit the first simulation tick before flip gives the monitor any
        # evidence. A subsequent PROBING verdict holds simulation.
        self._flip_health = (
            FlipHealth.UNTHROTTLED
            if self._start_deadline
            else FlipHealth.OK
        )
        self._prime_deadline = False
        self._background_next_tick = perf_counter()

        # The display pacing a window is a property of where the window is,
        # not of the run: moving between a 144Hz and a 60Hz output silently
        # multiplies the simulation rate by the ratio of the two. Nothing
        # reports that change, so it is measured. Only meaningful while the
        # swap blocks -- see rate.py.
        self._delivered_rate = DeliveredRate(display.output_refresh_rates)
        # Read the outputs now, during engine startup, rather than paying for
        # it inside the first frame that needs an answer.
        self._delivered_rate.refresh_outputs()
        self._last_present = 0.0

    @property
    def fallback_period(self) -> float:
        """Target-rate period used while presentation is withheld."""
        return self._fallback_period

    def begin_iteration(self) -> bool:
        """Begin instrumentation and return whether simulation must be held."""
        if self._frame_pacing is not None:
            self._frame_pacing.begin_iteration()
        priming = self._prime_deadline
        self._prime_deadline = False
        return priming

    def after_dispatch(self) -> None:
        if self._frame_pacing is not None:
            self._frame_pacing.after_dispatch()

    def backgrounded(self) -> None:
        """Start a fresh target-rate deadline while frames are withheld."""
        self._background_next_tick = perf_counter()
        # No presentations are being delivered, so nothing measures the rate.
        self._delivered_rate.reset()
        self._last_present = 0.0

    def foregrounded(self) -> None:
        """Restart presentation without stale cadence or health evidence."""
        self._monitor.reset()
        self._flip_health = (
            FlipHealth.UNTHROTTLED
            if self._start_deadline
            else FlipHealth.OK
        )
        self._unthrottled_pacing = self._start_deadline
        self._prime_deadline = False
        if self._cadence is not None:
            self._cadence.reset()
        self._next_present = perf_counter() + self._presentation_period
        # Restoration is exactly when a display may have been added, removed,
        # or reconfigured, so both the evidence and the candidate outputs go.
        self._delivered_rate.reset()
        self._delivered_rate.refresh_outputs()
        self._last_present = 0.0

    def resumed(self) -> None:
        """Discard suspended wall-time debt and re-prime the tick cadence."""
        if self._cadence is not None:
            self._cadence.reset()
        self._fallback_accumulator = self._fallback_period
        self._fallback_last = perf_counter()
        # The interval spanning a suspension describes the pause, not the
        # display.
        self._delivered_rate.reset()
        self._last_present = 0.0

    def ticks_due(
        self,
        *,
        running: bool,
        priming: bool,
        backgrounded: bool,
    ) -> int:
        """Return fixed simulation ticks due before the next presentation."""
        if not running:
            return 0
        if priming or self._flip_health is FlipHealth.PROBING:
            return 0
        if backgrounded:
            # Presentation is no longer granting cadence. The background wait
            # below makes this one tick occur at the target wall-clock rate.
            return 1
        if self._cadence is not None:
            return self._cadence.advance()

        now = perf_counter()
        self._fallback_accumulator = min(
            self._fallback_accumulator + (now - self._fallback_last),
            self._fallback_max_debt,
        )
        self._fallback_last = now
        tick_count = 0
        while self._fallback_accumulator >= self._fallback_period:
            self._fallback_accumulator -= self._fallback_period
            tick_count += 1
        return tick_count

    def after_simulation(self) -> None:
        if self._frame_pacing is not None:
            self._frame_pacing.after_simulation()

    def wait_backgrounded(self) -> None:
        """Pace a hidden loop without submitting to a stalled swap chain."""
        self._background_next_tick += self._fallback_period
        now = perf_counter()
        if self._background_next_tick < now:
            self._background_next_tick = now + self._fallback_period
        wait = self._background_next_tick - now
        if wait > 0:
            sleep(wait)

    def present(self, tick_count: int) -> bool:
        """Present one frame and return whether the swap stream is stalled."""
        recomposed = tick_count > 0
        self._display.prepare(recompose=recomposed)
        if self._frame_pacing is not None:
            self._frame_pacing.after_prepare()

        if self._unthrottled_pacing:
            # The frame is fully prepared. Hold it until its fixed deadline,
            # using a coarse sleep followed by an optional short spin.
            wait = self._next_present - perf_counter()
            if self._spin_pacing:
                if wait > _SPIN_MARGIN:
                    sleep(wait - _SPIN_MARGIN)
                while perf_counter() < self._next_present:
                    pass
            elif wait > 0:
                sleep(wait)

        deadline_error = (
            perf_counter() - self._next_present
            if self._unthrottled_pacing
            else None
        )
        pacing_mode = (
            "deadline"
            if self._unthrottled_pacing
            else (
                "probing"
                if self._flip_health is FlipHealth.PROBING
                else "vsync"
            )
        )
        self._display.present()
        completed = perf_counter()
        presented_ns = (
            perf_counter_ns() if self._frame_pacing is not None else 0
        )

        # If a long frame or debugger pause missed more than one deadline,
        # resync rather than sprinting to catch up.
        self._next_present += self._presentation_period
        if self._next_present < perf_counter():
            self._next_present = perf_counter() + self._presentation_period

        swap_elapsed = self._display.swap_duration
        previous_health = self._flip_health
        self._flip_health = self._monitor.observe(swap_elapsed)
        if self._frame_pacing is not None:
            self._frame_pacing.record_present(
                presented_ns=presented_ns,
                ticks=tick_count,
                recomposed=recomposed,
                swap_seconds=swap_elapsed,
                deadline_seconds=deadline_error,
                pacing_mode=pacing_mode,
                flip_health=self._flip_health.name.lower(),
            )

        entering_unthrottled = (
            self._flip_health is FlipHealth.UNTHROTTLED
            and previous_health is not FlipHealth.UNTHROTTLED
        )
        if entering_unthrottled:
            # Establish one real held-frame deadline before simulation resumes.
            self._next_present = perf_counter() + self._presentation_period
            self._prime_deadline = True

        self._unthrottled_pacing = (
            self._flip_health is FlipHealth.UNTHROTTLED
        )
        if (
            self._unthrottled_pacing
            and self._cadence is not None
            and not self._deadline_grid_reported
        ):
            self._deadline_grid_reported = True
            _log_deadline_grid_pacing(
                self._logger,
                initial_reason=self._start_deadline_reason,
            )

        # A blocking swap is the only interval that measures the display; on
        # the deadline grid it measures this loop's own timer. Fed last, so a
        # re-cadence owns the deadline bookkeeping above rather than racing it.
        if self._flip_health is FlipHealth.STALLED:
            self._delivered_rate.reset()
        elif pacing_mode == "vsync" and self._last_present:
            measured = self._delivered_rate.observe(
                completed - self._last_present
            )
            if measured is not None:
                self._retune(measured)
        self._last_present = completed

        return self._flip_health is FlipHealth.STALLED

    def _retune(self, refresh_rate: float) -> None:
        """Adopt a measured display rate, rebuilding the simulation cadence.

        Called when presentation has been proven to be arriving at a rate the
        session was not built for -- the window moved to another output, or a
        display was reconfigured underneath it. Without this the cadence keeps
        dividing ticks by the launch display's rate while presentations arrive
        at the current one, and the simulation runs fast or slow by exactly
        the ratio between them.
        """
        if refresh_rate == self._refresh_rate:
            return
        previous = self._refresh_rate
        self._refresh_rate = refresh_rate
        self._presentation_period = 1.0 / refresh_rate
        self._cadence = PresentationCadence(self._target_rate, refresh_rate)
        self._delivered_rate.reset()
        self._logger.info(
            f"Presentation rate changed "
            f"({previous if previous else 'unknown'} -> {refresh_rate:.3f}Hz); "
            f"cadence re-derived to "
            f"{self._cadence.ratio.numerator}/{self._cadence.ratio.denominator} "
            f"(effective {self._cadence.effective_rate:.3f} FPS)."
        )
        # The old deadline belongs to the old period.
        self._next_present = perf_counter() + self._presentation_period

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
