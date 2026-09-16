"""The frame pacer: one question, asked once per presentation slot.

**Is this slot the one the pending frame belongs on?** Yes, and it goes up
and the engine is called for the next one. No, and it is held.

A frame belongs on the first slot at or after its deadline, and its deadline
is `origin + frame * step` -- exact, from the engine's own frame count. Two
properties fall out of that, and editing anything here means keeping both:

* **Never early.** A frame cannot be shown before its deadline, so the game
  can never run fast, and a frame the engine finished early is HELD rather
  than shown into the previous frame's slot.
* **The pacer decides WHEN a tick happens and never HOW MANY.** Both of this
  module's sleeps release ticks, which makes it the engine's clock in
  practice -- but `clock.frame` advances once per pass and the wait is
  computed from a quantity with no panel term in it. A withheld present
  cannot withhold simulation, and re-coupling those is the one change this
  file must never accept.

`step` is the PACED step, which is not always `1/rate`: on a panel whose rate
does not divide the authored one it is re-timed so the two grids stay
commensurate. See `accepted_rate.py` -- the simulation is untouched, and what
moves is how long one second lasts.

The whole design, the measurements, and the alternatives it refuses:
`bps/reference/engine/pacing.md`.
"""
from collections import deque
from math import ceil, isfinite
from time import perf_counter, perf_counter_ns, sleep
from typing import TYPE_CHECKING

from blitspersecond.system import Logger

from .accepted_rate import RATE_TOLERANCE, AcceptedRate, accept_rate
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

# How often a DWM-referenced grid re-reads its own reference.
#
# DWM reports `qpcRefreshPeriod` as an INTEGER count of QPC ticks. At a 10MHz
# QPC and a 144Hz panel the true period is 69444.44 ticks, which is not
# representable, so the reported value dithers -- 69443 to 69448 measured over
# 4,000 samples, a 72ppm spread. A session sampling that once keeps whatever
# it got, and the resulting rate error walks the grid off DWM's real vblank at
# up to 0.015ms/s: half a slot period in under four minutes, which is the slow
# beat a short run cannot see.
#
# Re-reading once a second holds each correction to about 0.015ms -- an order
# of magnitude under the frame-to-frame jitter it is correcting within. That
# is what makes a snap safe here and a slew unnecessary.
_REANCHOR_SECONDS = 1.0

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
        # The authored step, and the fallback. The simulation is 1/60 and that
        # never changes; what moves is how long the pacer makes one second
        # last, so that ticks and slots stay commensurate. See
        # `accepted_rate.py`.
        self._desired_rate = target_rate
        self._desired_step = step
        self._step = step
        self._accepted: AcceptedRate | None = None
        self._reported_accepted: float | None = None

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
        self._overran = 0
        self._last_shown = 0.0
        self._interval = 0.0
        self._last_slot_index = None
        self._off_pattern: deque[bool] = deque(maxlen=_PHASE_WINDOW)
        self._phase_shifts = 0
        self._recent_work: deque[float] = deque(maxlen=_RESERVE_WINDOW)
        self._late = 0
        self._reanchors = 0
        self._reanchor_worst = 0.0

        # The re-timed rate rides on the grid line rather than getting one of
        # its own: two adjacent "Pacing ..." lines said the same thing twice,
        # and the reasoning belongs in `bps/reference/engine/pacing.md`.
        accepted = self._accepted
        # Only when it was actually adopted. A rate outside tolerance is
        # refused and the authored step stands, so reporting it here would
        # name a rate nothing is ticking at.
        retimed = (
            f", ticking at {accepted.rate:.3f}fps ({accepted.error:+.2%})"
            if accepted is not None
            and accepted.within_tolerance
            and not accepted.exact
            else ""
        )
        if self._uses_dwm_clock:
            from .dwm import DWM_SUBMIT_LEAD

            configured = (
                f"{refresh_rate:.3f}Hz" if refresh_rate is not None else "unavailable"
            )
            logger.info(
                f"Pacing frames against DWM's {self._refresh_rate:.6f}Hz "
                f"composition clock ({DWM_SUBMIT_LEAD * 1000:.3f}ms before "
                f"vblank; configured mode {configured}){retimed}."
            )
        else:
            logger.info(
                f"Pacing frames against a {self._refresh_rate:.3f}Hz "
                f"notional slot grid "
                f"({'configured' if refresh_rate else 'assumed'}){retimed}."
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
        the measured submission lead. Elsewhere the period comes from the
        panel and **the phase is arbitrary** -- wherever `perf_counter` zero
        fell, with no relationship to a real retrace.

        That is sufficient rather than a compromise, and the reasoning is
        worth having before changing it: the target is the interval between
        two vblanks, not an instant, so equal spacing puts submits in
        successive real intervals whatever the offset. `_watch_phase` handles
        the one case where an arbitrary phase can still be wrong. See
        `bps/reference/engine/pacing.md`, "The slot grid is notional".
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

        **No spin, and no submission lead on this path.** Both follow from
        the target being an interval rather than an instant: `sleep` lands
        well inside a slot, so sharpening it buys accuracy against a
        requirement that does not exist. The margin is not the same on every
        platform -- Linux overshoots by 0.080ms worst case against a 6.944ms
        slot, Windows by up to 1.022ms -- so the conclusion holds on both, but
        on Windows by a factor of seven rather than eighty. Measured figures
        are in `bps/reference/engine/pacing.md`. The only lead anywhere is
        Win32's, folded into the phase by `dwm.py`.

        Wakes at the FRAME rate rather than the panel rate, because the slot
        is computed rather than polled for.

        Returns immediately when the slot has already passed, which is the
        overrun case: the frame goes up at once and `anchor` restarts the
        cycle from where it actually landed.
        """
        if self._uses_dwm_clock:
            self._reanchor()
        remaining = self.slot_for(frame) - perf_counter()
        if remaining > 0:
            sleep(remaining)

    def anchor(self, frame: int, shown: float) -> None:
        """Re-anchor the grid when the engine overran a frame's window.

        Restarts the cycle from where the frame actually went up, rather than
        leaving every subsequent deadline in the past -- which would make the
        pacer answer "yes" at every slot and sprint at panel rate, the
        catch-up this design exists to refuse.
        """
        ideal = self.deadline(frame)
        if shown > ideal + self._period:
            self._overran += 1
            self._origin = shown - frame * self._step

    def observe_work(self, seconds: float) -> None:
        """Feed how long the engine took to produce and compose one frame."""
        self._recent_work.append(seconds)

    def wait_before_tick(self, frame: int) -> None:
        """Sleep until the engine needs to start building `frame`.

        Starting late is what keeps input sampled close to the moment the
        frame is shown. It is a trade, not a free win: the engine gets the
        reserve rather than the interval to the next slot, and a frame that
        overruns the reserve slips one. `late` counts how often that bet was
        wrong.

        **The target comes from the frame's DEADLINE, never from its slot.**
        Deadlines are evenly spaced by construction; slots alternate two and
        three. Taking the slot makes the pump inherit that alternation, which
        is the game clock itself running unevenly -- measured swinging
        between 13.84 and 20.89ms. This is the single easiest thing to get
        wrong in this file and it reads as a harmless simplification.

        The reserve must cover the work and whatever the sleep overshoots by,
        which is why it is a doubled worst case rather than a tight estimate.

        Does not sleep for less than `_SLEEP_FLOOR`; below that the wake
        costs more than the wait is worth. Why this trade is worth making, and
        what it costs: `bps/reference/engine/pacing.md`.
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

    def _watch_phase(self, slot_index: int, overran: bool) -> None:
        """Notice frames landing on the wrong vblank, and move if enough do.

        The intended gap between two presents is known exactly -- it is the
        difference between their slot indices. If the measured interval is
        most of a slot away from that, the frame went somewhere we did not
        send it, and sitting within jitter of a real boundary is the reason
        that happens. An OVERRUN frame is excluded: that gap is late because
        the engine took too long, which is a different fault and already
        counted. This is a deliberate correction; an overrun is a failure.
        """
        if self._uses_dwm_clock:
            # This grid has an external phase reference.  Moving it in
            # response to application-side intervals would discard the thing
            # DWM supplied and recreate the periodic queue-depth shuffle.
            return
        previous, self._last_slot_index = self._last_slot_index, slot_index
        if previous is None or overran or not self._interval:
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

    def _reanchor(self) -> None:
        """Move a DWM-referenced grid back onto DWM's live vblank.

        `_watch_phase` refuses to correct this grid, and is right to: an
        external reference must not be overridden by guesses made from
        application-side intervals. But that reference was read ONCE, at
        session start, and the rate it came with is quantised (see
        `_REANCHOR_SECONDS`). Re-reading the same reference is not the thing
        `_watch_phase` refuses; it is the opposite of it.

        **Only the phase moves.** Re-timing the period mid-run would change
        slot spacing under a running game and drag `_accept_rate` with it.
        Phase alone is sufficient: resetting the accumulated offset every
        second holds the residual to whatever one second of drift produces.

        The correction is folded into plus or minus half a period, so the grid
        stays congruent to the one existing slot indices were computed against
        and the numbering never jumps. That also makes a stale DWM prediction
        harmless -- DWM can retain a composition prediction while the desktop
        needs no new work, and a `qpcVBlank` in the past is congruent to the
        live one.

        Silent when DWM stops answering: the grid it already has is better
        than no grid, and `_synchronise_grid` owns giving up on this path.
        """
        now = perf_counter()
        if now < self._next_reanchor:
            return
        self._next_reanchor = now + _REANCHOR_SECONDS
        clock = _query_dwm_clock(self._presentation_path_name)
        if clock is None:
            return
        offset = (clock.phase - self._phase) % self._period
        if offset > self._period * 0.5:
            offset -= self._period
        self._phase += offset
        self._reanchors += 1
        self._reanchor_worst = max(self._reanchor_worst, abs(offset))

    @property
    def reanchors(self) -> int:
        """Times the DWM-referenced grid was moved back onto its reference."""
        return self._reanchors

    @property
    def reanchor_worst(self) -> float:
        """The largest single phase correction, in seconds.

        Worth reading: a correction much larger than a second of drift means
        something moved the grid that was not the quantised rate.
        """
        return self._reanchor_worst

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
    def overran(self) -> int:
        """Frames the engine failed to present inside their budgeted window.

        The frame went up more than a panel period after its deadline
        because building it took too long, so the previous frame stayed on
        screen for an extra window. **A fault, not a manoeuvre** --
        `phase_shifts` is the deliberate one, and the two read alike only if
        this is misnamed.
        """
        return self._overran

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
        clock = (
            _query_dwm_clock(self._presentation_path_name)
            if getattr(self._display, "dwm_clock_eligible", True)
            else None
        )
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
        # A grid that was just anchored does not need re-reading yet. Set
        # here so construction, an output change and a resume all agree.
        self._next_reanchor = perf_counter() + _REANCHOR_SECONDS
        off_pattern = getattr(self, "_off_pattern", None)
        if off_pattern is not None:
            off_pattern.clear()
        self._accept_rate()

    def _accept_rate(self) -> None:
        """Re-time the deadline grid so it stays commensurate with the slots.

        Called from `_synchronise_grid`, so every route that settles a refresh
        rate re-times with it -- construction, a window moving to another
        output, and a resume.

        **This changes the pacer's step and never the clock's.** A game
        authored at 60 ticks a second still gets exactly sixty ticks in every
        one of its seconds; on a 59.94Hz panel that second takes 1001ms of
        wall time instead of 1000. `pace` reports the difference, which is
        what it is for.

        An accepted rate outside tolerance is refused rather than used: the
        authored step goes back, which is the behaviour without any of this,
        so a panel this cannot help is left exactly as it was.
        """
        accepted = accept_rate(self._desired_rate, self._refresh_rate)
        self._accepted = accepted
        self._step = (
            accepted.step if accepted.within_tolerance else self._desired_step
        )
        if self._reported_accepted == accepted.rate:
            return
        self._reported_accepted = accepted.rate
        if not accepted.within_tolerance:
            self._logger.warning(
                f"No rate within {RATE_TOLERANCE * 100:.0f}% of "
                f"{accepted.desired:.0f}fps divides a {accepted.refresh:.3f}Hz "
                f"panel (nearest {accepted.rate:.3f}fps, {accepted.error:+.2%}); "
                f"pacing at {accepted.desired:.0f}fps and accepting a periodic "
                f"repeat."
            )

    @property
    def accepted(self) -> AcceptedRate | None:
        """The rate this panel is being paced at, and how far off it is."""
        return self._accepted

    # -- instrumentation hooks ----------------------------------------------

    def begin_iteration(self) -> None:
        if self._frame_pacing is not None:
            self._frame_pacing.begin_iteration()

    def after_dispatch(self) -> None:
        if self._tracks_window_output:
            refresh_rate = self._display.poll_refresh_rate()
            if refresh_rate is not None:
                self._adopt(refresh_rate)
            elif not getattr(self._display, "dwm_clock_eligible", True):
                self._synchronise_grid(self._mode_refresh_rate)
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
        overran_before = self._overran
        self.anchor(frame, shown)
        self._watch_phase(
            round((self.slot_for(frame) - self._phase) / self._period),
            self._overran != overran_before,
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
