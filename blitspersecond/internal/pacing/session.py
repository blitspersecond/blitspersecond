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

from collections import deque
from math import ceil, isfinite
from time import perf_counter, perf_counter_ns, sleep
from typing import TYPE_CHECKING

from blitspersecond.system import Logger

from .health import FlipHealth, PresentationMonitor
from .path import detect_presentation_path
from .sink import FramePacingSinkFactory

if TYPE_CHECKING:
    from blitspersecond.internal.display import Display


# Below this there is no point sleeping: the syscall and the wake cost more
# than the wait is worth, and the engine may as well start now.
_SLEEP_FLOOR = 0.001

# Headroom over the worst recent frame. A frame that overruns its reserve
# slips a slot, which the existing recovery handles, so this trades latency
# against how often that happens.
_RESERVE_FACTOR = 2.0

# How many frames the worst-case estimate remembers. A windowed maximum
# rather than a decay: a decay that forgets slowly enough to respect a spike
# also takes hundreds of frames to give the latency back, which was measured
# -- one 20ms stall pinned the reserve above a whole frame interval for the
# rest of the run and the engine simply started immediately every time.
_RESERVE_WINDOW = 90

# The reserve may never exceed this fraction of a frame. Past it the engine
# would start immediately every frame, which is merely the old behaviour with
# extra arithmetic -- so cap it and keep half the interval as real sleep.
_RESERVE_CEILING = 0.5

# Phase search. The notional grid's offset from the real one is unknown and
# unmeasurable, and almost all offsets are fine. The bad case is landing
# within timing jitter of a real vblank, where frames cross the boundary
# intermittently and land a slot early or late. Roughly 2.3% of the phase
# space at the jitter we measure, so it will happen eventually.
#
# It is detectable without any external reference: a present whose interval
# is a whole slot away from the gap we intended is a frame that went to the
# wrong vblank. Enough of those in a window and the grid moves.
_PHASE_WINDOW = 120
_PHASE_TRIGGER = 6
_PHASE_STEP = 0.25  # of a period

# Presentation rate assumed when the platform will not say what the panel
# runs at. Only the slot grid depends on it, so a wrong guess costs an uneven
# present pattern rather than a game running at the wrong speed.
_ASSUMED_REFRESH = 60.0

# DWM exposes one NULL-HWND composition clock on current Windows.  Only use
# it when it describes the same refresh regime as the monitor Windows assigns
# to our window; a primary-display clock must never pace a window on a very
# different secondary display.
_DWM_RATE_TOLERANCE = 0.01


def _rates_agree(first: float, second: float) -> bool:
    return abs(first - second) <= max(first, second) * _DWM_RATE_TOLERANCE


def _query_dwm_clock(path_name: str):
    """Import the Win32 probe only for the path that can use it."""
    if path_name != "win32":
        return None
    from .dwm import query_dwm_presentation_clock

    return query_dwm_presentation_clock()


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
        self._mode_refresh_rate = refresh_rate
        self._refresh_rate = refresh_rate or _ASSUMED_REFRESH
        self._period = 1.0 / self._refresh_rate
        self._phase = 0.0
        self._uses_dwm_clock = False
        self._rejected_dwm_rate: tuple[float, float] | None = None

        presentation_path = detect_presentation_path()
        logger.info(f"Presentation path: {presentation_path.description}.")
        # Win32 reports the current monitor for a window rather than the one
        # it was created on, so a window dragged to another output can have
        # its slot grid corrected. It corrects nothing about the simulation.
        self._tracks_window_output = presentation_path.name == "win32"
        self._presentation_path_name = presentation_path.name
        self._synchronise_grid(refresh_rate)
        self._frame_pacing = frame_pacing_factory(
            target_fps=target_rate,
            refresh_hz=(
                self._refresh_rate
                if refresh_rate is not None or self._uses_dwm_clock
                else None
            ),
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
        self._last_slot_index = None
        self._off_pattern: deque[bool] = deque(maxlen=_PHASE_WINDOW)
        self._phase_shifts = 0
        self._recent_work: deque[float] = deque(maxlen=_RESERVE_WINDOW)
        self._late = 0

        if self._uses_dwm_clock:
            from .dwm import DWM_SUBMIT_LEAD

            configured = (
                f"{refresh_rate:.3f}Hz" if refresh_rate is not None else "unavailable"
            )
            logger.info(
                f"Pacing frames against DWM's {self._refresh_rate:.6f}Hz "
                f"composition clock ({DWM_SUBMIT_LEAD * 1000:.3f}ms before "
                f"vblank; configured mode {configured})."
            )
        else:
            logger.info(
                f"Pacing frames against a {self._refresh_rate:.3f}Hz "
                f"notional slot grid "
                f"({'configured' if refresh_rate else 'assumed'})."
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
        """The first slot boundary at or after `after`.

        On Win32 this is DWM's absolute QPC vblank grid, shifted earlier by
        the measured submission lead.  Its period is live rather than the
        configured mode's nominal value, so it neither starts on DWM's latch
        boundary nor walks onto it over a long run.

        Elsewhere this is a NOTIONAL grid. Its period comes from the panel;
        its phase is wherever `perf_counter` zero happened to fall and has no
        relationship to a real vertical retrace. It does not need one: the
        cadence is carried by the INTERVALS, so two submits two notional slots
        apart are two real periods apart, whatever the grids' offset.

        There is no way to do better on this path in any case. GLX's
        `glXGetSyncValuesOML` looked like a vblank clock and is not: polled
        460 times across 0.5s without swapping, UST and MSC each returned a
        single distinct value (Mesa/radeonsi, XWayland, 2026-08-27). Both are
        tied to swaps, so neither locates a retrace.

        A consequence worth keeping in mind rather than acting on: if this
        grid happens to sit ON the real one, ordinary timing jitter would push
        individual frames across a boundary and they would land a slot early
        or late intermittently. That shows up as intervals off the 2/3
        pattern in `display.frame.ms` -- and the fix, if it ever appears, is
        to shift this grid by an arbitrary fraction of a period. Not measured
        as a problem: p99 20.878 against a 20.833 expectation, no outliers.
        """
        return self._phase + ceil(
            (after - self._phase) / self._period - 1e-9
        ) * self._period

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
        remaining = self.slot_for(frame) - perf_counter()
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

    def observe_work(self, seconds: float) -> None:
        """Feed how long the engine took to produce and compose one frame."""
        self._recent_work.append(seconds)

    def wait_before_tick(self, frame: int) -> None:
        """Sleep until the engine needs to start building `frame`.

        Without this the engine runs the instant the previous frame goes up,
        finishes in a fraction of its budget, and the composed frame then
        sits in the back buffer for the rest of the interval -- measured at
        p50 13.9ms and p90 20.8ms on a 144Hz panel, essentially a whole frame
        of input-to-photon latency spent waiting.

        Starting late instead means input is sampled close to the moment the
        frame is shown. **This deliberately gives up the whole-gap budget**
        the engine otherwise has: it now gets the reserve, and a frame that
        overruns the reserve slips a slot. That is a real trade and not a
        free win -- the reserve is what buys it back, and `late` counts how
        often the bet was wrong.

        **Anchored to the frame's DEADLINE, not to its slot.** The deadline
        is `origin + frame * step`, evenly spaced by construction, so the
        engine is pumped at even 16.67ms intervals. Anchoring to the slot
        instead makes the pump inherit the grid's 2-and-3 alternation --
        measured as a tick interval swinging between 13.84 and 20.89ms, which
        is the game clock itself running unevenly. Sampling input on an even
        cadence matters more than the frame's remaining wait, and that wait
        cannot be made constant anyway: a frame can only be shown on a slot,
        and the slots are not evenly spaced against the tick rate.

        The reserve must therefore cover the work AND the latch margin, since
        a deadline landing exactly on a slot leaves no slack between them.

        Does not sleep for less than `_SLEEP_FLOOR`; below that the wake
        costs more than the wait is worth.
        """
        start_by = self.deadline(frame) - self.reserve
        remaining = start_by - perf_counter()
        if remaining > _SLEEP_FLOOR:
            sleep(remaining)
        elif remaining < 0:
            self._late += 1

    @property
    def reserve(self) -> float:
        """Seconds held back for the engine to build a frame in.

        The worst frame in the recent window plus headroom, capped so it can
        never swallow the whole interval, and floored at the sleep floor
        because a smaller reserve cannot be acted on anyway.
        """
        if not self._recent_work:
            return _SLEEP_FLOOR
        estimate = max(self._recent_work) * _RESERVE_FACTOR
        return min(max(estimate, _SLEEP_FLOOR), self._step * _RESERVE_CEILING)

    @property
    def late(self) -> int:
        """Times the engine was called with no reserve left before its slot."""
        return self._late

    @property
    def phase_shifts(self) -> int:
        """Times the grid has been nudged off a suspected vblank boundary.

        A handful over a long session is the mechanism working. Frequent
        shifting means something else: the grid is DRIFTING against the real
        one, which is a wrong period rather than a wrong phase, and no amount
        of phase nudging fixes it. Read a rising count as "the refresh rate
        we were told is not the rate this panel runs at".
        """
        return self._phase_shifts

    def _watch_phase(self, slot_index: int, slipped: bool) -> None:
        """Notice frames landing on the wrong vblank, and move if enough do.

        The intended gap between two presents is known exactly -- it is the
        difference between their slot indices. If the measured interval is
        most of a slot away from that, the frame went somewhere we did not
        send it, and sitting within jitter of a real boundary is the reason
        that happens. A slipped frame is excluded: that gap is late because
        the engine overran, which is a different fault and already counted.
        """
        if self._uses_dwm_clock:
            # This grid has an external phase reference.  Moving it in
            # response to application-side intervals would discard the thing
            # DWM supplied and recreate the periodic queue-depth shuffle.
            return
        previous, self._last_slot_index = self._last_slot_index, slot_index
        if previous is None or slipped or not self._interval:
            return
        expected = (slot_index - previous) * self._period
        self._off_pattern.append(
            abs(self._interval - expected) > self._period * 0.5
        )
        if sum(self._off_pattern) < _PHASE_TRIGGER:
            return
        # Move a quarter period and start the evidence again. Any offset is
        # as valid as any other -- see `next_slot` -- so this costs nothing
        # but the one frame it lands on.
        self._phase += self._period * _PHASE_STEP
        self._phase_shifts += 1
        self._off_pattern.clear()
        self._logger.info(
            f"Presentation grid appeared to sit on a vblank boundary "
            f"({_PHASE_TRIGGER} frames landed off-pattern); shifted phase by "
            f"{self._period * _PHASE_STEP * 1000:.2f}ms "
            f"(shift {self._phase_shifts})."
        )

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
            else:
                self._synchronise_grid(self._mode_refresh_rate)
        self._monitor.reset()
        self._health = FlipHealth.OK

    def resumed(self, frame: int) -> None:
        """Re-anchor after a suspension: the gap was not simulation."""
        if self._tracks_window_output:
            self._synchronise_grid(self._mode_refresh_rate)
        self._origin = perf_counter() - frame * self._step

    def _adopt(self, refresh_rate: float) -> None:
        """Re-pace for a window that moved to another output."""
        if refresh_rate < 1:
            return
        previous = self._refresh_rate
        previous_source = self._uses_dwm_clock
        self._synchronise_grid(refresh_rate)
        if (
            self._refresh_rate != previous
            or self._uses_dwm_clock != previous_source
        ):
            source = "DWM composition" if self._uses_dwm_clock else "notional"
            self._logger.info(
                f"Presentation clock changed ({previous:.6f} -> "
                f"{self._refresh_rate:.6f}Hz, {source}); new slot grid. "
                f"Simulation is unaffected."
            )

    def _synchronise_grid(self, refresh_rate: float | None) -> None:
        """Select a live DWM grid when it agrees with this window's output."""
        self._mode_refresh_rate = refresh_rate
        clock = _query_dwm_clock(self._presentation_path_name)
        if clock is not None and (
            refresh_rate is None
            or _rates_agree(clock.refresh_rate, refresh_rate)
        ):
            self._refresh_rate = clock.refresh_rate
            self._period = clock.period
            self._phase = clock.phase
            self._uses_dwm_clock = True
            self._rejected_dwm_rate = None
        else:
            self._refresh_rate = refresh_rate or _ASSUMED_REFRESH
            self._period = 1.0 / self._refresh_rate
            self._phase = 0.0
            self._uses_dwm_clock = False
            if clock is not None and refresh_rate is not None:
                mismatch = (clock.refresh_rate, refresh_rate)
                if mismatch != self._rejected_dwm_rate:
                    self._logger.warning(
                        f"DWM composition clock ({clock.refresh_rate:.6f}Hz) "
                        f"does not match the window's configured "
                        f"{refresh_rate:.3f}Hz output; using a notional grid."
                    )
                self._rejected_dwm_rate = mismatch
            else:
                self._rejected_dwm_rate = None
        self._last_slot_index = None
        off_pattern = getattr(self, "_off_pattern", None)
        if off_pattern is not None:
            off_pattern.clear()

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
        slipped_before = self._slipped
        self.anchor(frame, shown)
        self._watch_phase(
            round((self.slot_for(frame) - self._phase) / self._period),
            self._slipped != slipped_before,
        )

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
