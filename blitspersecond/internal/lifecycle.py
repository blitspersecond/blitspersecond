"""The engine's lifecycle: one state machine, in one object.

Two orthogonal axes live here, deliberately not merged:

  * ``state`` -- explicit engine control (running, suspended, stopping).
  * ``backgrounded`` -- whether the compositor is presenting us at all.

**Backgrounding is NOT an EngineState and must not become one.** It is an
observation about the window system, not a command; games decide for
themselves whether losing visibility means pausing. Folding it into the enum
would make that policy the engine's, and would multiply every state by two.

`bps/reference/engine/lifecycle.md` is the authority -- the machine written
out, the four kinds of state, why stopping is not exiting, and what is still
open about the suspension arc.
"""

from __future__ import annotations

import sys
from enum import Enum, auto
from typing import NoReturn, Optional

from blitspersecond.system import Logger

from .events import EventBus


class EngineState(Enum):
    """Lifecycle of the engine's main loop.

    The spine is CREATED -> STARTING -> RUNNING -> STOPPING -> STOPPED, with a
    suspension arc hanging off RUNNING and FAILED joining the spine at
    STOPPING. Four kinds of state sit on it -- markers that record a passage
    (CREATED, FAILED, STOPPED), working states that take real time (STARTING,
    STOPPING), settled states the engine sits in (RUNNING, SUSPENDED), and
    momentary ones that exist only to give observers a hook mid-transition
    (SUSPENDING, RESUMING). Four rules that a change here could break:

    * **FAILED is the single way in to an unclean shutdown.** Do not add an
      edge from STARTING or RUNNING straight to STOPPING; entering FAILED is
      what makes the status non-zero, so such an edge exits clean on a crash.
    * **STARTING blocks through warm-up inside ``BlitsPerSecond.__init__``**,
      so a constructed engine is already RUNNING -- "never run" cannot be read
      off the state.
    * **Game-level pause is NOT an engine state.** It is a mode the game runs
      while the engine is RUNNING.
    * **Audio is its own clock domain** and deliberately continues across
      suspension.

    OPEN: whether the suspension arc is needed at all. Read
    `bps/reference/engine/lifecycle.md` before removing it -- the arc lifts
    out as one seam, but its single call site is the loop's ONLY
    per-iteration lifecycle boundary, which goes with it.
    """

    CREATED = auto()
    STARTING = auto()
    RUNNING = auto()
    SUSPENDING = auto()
    SUSPENDED = auto()
    RESUMING = auto()
    FAILED = auto()
    STOPPING = auto()
    STOPPED = auto()


# Engine-domain events. Lifecycle state is explicit engine control; background
# and foreground report whether the compositor is currently presenting the
# application, without imposing a game-level pause policy.
EventBus.register_event_type("on_engine_state")
EventBus.register_event_type("on_background")
EventBus.register_event_type("on_foreground")


#: The machine, written out.
#:
#: PROVISIONAL, and deliberately NOT enforced: this describes what the machine
#: does rather than constraining it, so an unexpected move is logged loudly and
#: then MADE. A half-rebuilt engine should not gain a new crash path from rules
#: nobody has signed off on. There are no tests over any of this; see
#: `bps/reference/engine/lifecycle.md`, "Still open", before tightening it.
_LEGAL_TRANSITIONS: dict[EngineState, tuple[EngineState, ...]] = {
    EngineState.CREATED: (EngineState.STARTING,),
    # Warm-up either finishes or fails; it never reaches STOPPING directly.
    EngineState.STARTING: (EngineState.RUNNING, EngineState.FAILED),
    EngineState.RUNNING: (
        EngineState.SUSPENDING,
        EngineState.STOPPING,
        EngineState.FAILED,
    ),
    EngineState.SUSPENDING: (EngineState.SUSPENDED,),
    # A suspended engine can still fail: presentation stays live while ticks
    # are frozen, so the compositor-facing work can throw with nothing
    # simulating -- a resume that errors out, or something going wrong while
    # backgrounded. Only the settled state needs the edge; SUSPENDING and
    # RESUMING are momentary, with nothing running between their two
    # transitions that could break.
    EngineState.SUSPENDED: (
        EngineState.RESUMING,
        EngineState.STOPPING,
        EngineState.FAILED,
    ),
    EngineState.RESUMING: (EngineState.RUNNING,),
    # The single way in to an unclean shutdown, from wherever it happened.
    EngineState.FAILED: (EngineState.STOPPING,),
    EngineState.STOPPING: (EngineState.STOPPED,),
    # Terminal: teardown has closed the display, so there is nothing left to
    # start. Nothing restarts a run today; if that changes, STOPPED ->
    # STARTING is the edge to add.
    EngineState.STOPPED: (),
}


class Lifecycle:
    """The engine's state machine, its requests, and its visibility axis.

    Constructed by ``BlitsPerSecond``, which keeps it private and forwards the
    game-facing verbs (``bps.stop()``, ``bps.suspend()``, ``bps.state``) to it.
    The engine walks it through warm-up (``starting`` / ``started``) during
    its own construction, so a returned engine is already RUNNING. ``Loop``
    then drives it at the two places a loop legitimately can: the brackets
    around servicing (``servicing`` / ``stopped``) and the one boundary per
    iteration where deferred requests are applied (``settle``).

    Requests are deferred rather than immediate. ``suspend()`` from inside a
    game tick must not freeze the machine half-way through that tick, so it
    sets an intent and ``settle()`` acts on it between event dispatch and the
    swap -- never part-way through a coherent simulation step. ``stop()`` is
    the exception: shutdown is published immediately so subsystems tear down
    at the moment of the request, and the loop then unwinds on its own.
    """

    def __init__(
        self,
        events: EventBus,
        logger: Optional[Logger] = None,
    ) -> None:
        self._events = events
        self._logger = logger or Logger()
        self._state = EngineState.CREATED
        self._backgrounded = False
        self._suspend_requested = False
        self._exit_code = 0
        self._servicing = False

        # Window-system events that mean something to the lifecycle are wired
        # here rather than on the engine, which used to carry three handler
        # methods that did nothing but forward. OS/WM close requests -- the
        # close button, Alt+F4, and platform equivalents -- arrive as on_close.
        # Pyglet's default on_close is wired to the global app.event_loop
        # rather than our private loop, so it never stops our loop; hence the
        # explicit observer.
        events.push_handlers(
            on_close=self._on_window_close,
            on_hide=self._on_window_background,
            on_show=self._on_window_foreground,
            on_expose=self._on_window_foreground,
            on_activate=self._on_window_foreground,
        )

    # -- observation ------------------------------------------------------

    @property
    def state(self) -> EngineState:
        return self._state

    @property
    def running(self) -> bool:
        """Whether the main loop should keep going.

        Derived, never stored. The engine used to carry a ``_running`` flag
        beside ``_state`` and write both in the same breath, which is one
        fact in two places and no answer to which one wins.
        """
        return self._state in (
            EngineState.RUNNING,
            EngineState.SUSPENDING,
            EngineState.SUSPENDED,
            EngineState.RESUMING,
        )

    @property
    def backgrounded(self) -> bool:
        """Whether presentation is hidden or being withheld by the compositor."""
        return self._backgrounded

    @property
    def exit_code(self) -> int:
        """The status this shutdown has earned so far: 0 clean, non-zero not.

        Set by whoever requested the stop, because only they know whether it
        was one. A game asking to quit and a window being closed are clean; a
        main-loop exception and a failed start are not.
        """
        return self._exit_code

    # -- requests ---------------------------------------------------------

    def suspend(self) -> None:
        """Request suspension: simulation ticks freeze, but the loop keeps
        pumping events and presenting (the last composed frame stays up).
        Takes effect at the next loop boundary; safe to call from a game
        callback or an event handler. Backgrounding never calls this
        automatically; games choose whether visibility changes imply pause."""
        self._suspend_requested = True

    def resume(self) -> None:
        """Clear a suspend() request at the next lifecycle boundary."""
        self._suspend_requested = False

    def stop(self, reason: str = "unspecified", code: int = 0) -> None:
        """Request engine shutdown, and leave the process with ``code``.

        Idempotent; safe from callbacks and event handlers. Every shutdown
        names its trigger in the log so an unexpected one is diagnosable, and
        carries a status so a caller outside the process can tell a clean
        quit from a crash without reading that log: 0 for a clean stop,
        non-zero for one forced by something going wrong.

        This does NOT exit; ``stopped()`` does, once everything is actually
        down. Requesting a stop can happen anywhere -- inside a game tick,
        inside an event handler -- and leaving the process from there would
        unwind straight past the loop's epilogue, losing
        ``presentation.finish()`` and the pacing report. So the status is
        recorded, the machine moves to STOPPING, and the loop exits on its
        own terms once it has finished unwinding.

        The exception is an engine with no loop servicing it -- constructed
        but never run, or already unwound. Nothing is coming to call
        ``stopped()`` on its behalf, so this completes the cycle itself. That
        is what makes ``BlitsPerSecond().stop()`` terminal on an engine that
        was built and then thought better of. Note this cannot be read off the
        state: warm-up happens in the constructor, so an engine that has never
        run is RUNNING, exactly like one that is being serviced.
        """
        if not self._begin_stop(reason, code):
            return
        if not self._servicing:
            self.stopped()

    def fail(self, reason: str, code: int = 1) -> NoReturn | None:
        """Mark the run as broken, then stop it.

        The failure counterpart to ``stop()``, and the only route to a
        non-zero exit: entering FAILED is what makes the shutdown unclean, so
        no caller has to remember a status. Everything else about the
        shutdown is ``stop()``'s -- including whether the process leaves from
        here (nothing servicing the lifecycle, as in a constructor that
        cannot finish warming up) or once the loop has unwound.
        """
        if self._state in (
            EngineState.FAILED,
            EngineState.STOPPING,
            EngineState.STOPPED,
        ):
            return None
        self._transition(EngineState.FAILED)
        self.stop(reason, code)

    def exit(self) -> NoReturn:
        """Leave the process with the status this shutdown earned.

        Split out from ``stopped()`` so a caller that wants the transition
        without the exit has somewhere to go. Raises SystemExit, which is a
        BaseException and so passes through the loop's ``except Exception``
        untouched -- deliberately: a requested exit is not a main-loop error
        to be logged and recovered from.
        """
        sys.exit(self._exit_code)

    def _begin_stop(self, reason: str, code: Optional[int] = None) -> bool:
        """Record the status and move to STOPPING. False if already stopping.

        ``code=None`` leaves the recorded status alone, so a loop reaching its
        own exit cannot overwrite the non-zero status of whatever stopped it.
        """
        if self._state in (EngineState.STOPPING, EngineState.STOPPED):
            return False
        if code is not None:
            self._exit_code = code
        self._logger.info(f"Shutdown requested: {reason}.")
        self._transition(EngineState.STOPPING)
        return True

    # -- loop brackets ----------------------------------------------------

    def starting(self) -> None:
        """Begin warm-up, leaving the CREATED marker behind.

        Everything slow belongs in the stretch this opens: device activation,
        and any future kernel warm-up. Presentation deadlines and the first
        tick baseline deliberately do not exist yet, so slow hardware startup
        cannot become a false first-frame stall or a catch-up batch.
        """
        self._transition(EngineState.STARTING)

    def started(self) -> None:
        """Warm-up finished; simulation begins."""
        self._transition(EngineState.RUNNING)

    def servicing(self) -> None:
        """The loop has taken charge: it will call ``stopped()`` when it
        unwinds, so ``stop()`` must not short-circuit to the exit and skip
        the epilogue."""
        self._servicing = True

    def stopped(self) -> NoReturn:
        """Declare everything down, and leave the process.

        The terminal call, and the last thing a run does: STOPPED is
        published only once the loop's epilogue has finished, so an observer
        seeing it knows the machine really is down rather than merely
        committed to coming down. Then the process leaves with the status the
        shutdown earned.

        Routes through STOPPING when the loop left without anyone having
        requested it -- a surface that closed underneath us, say -- so
        teardown still runs exactly once and observers never see a state
        skipped.
        """
        if self._state is not EngineState.STOPPED:
            self._begin_stop("main loop exited")
            self._transition(EngineState.STOPPED)
        self._servicing = False
        self.exit()

    # -- per-iteration boundary -------------------------------------------

    def settle(self) -> Optional[EngineState]:
        """Apply any deferred suspend/resume request; report what changed.

        Returns the settled state this call arrived at (SUSPENDED or RUNNING),
        or None if nothing changed. The caller needs the answer because
        resuming invalidates its timing baselines -- wall time passed that no
        tick accounted for, so pacing must rebase rather than treat the gap as
        a catch-up debt.
        """
        if self._suspend_requested and self._state is EngineState.RUNNING:
            self._transition(EngineState.SUSPENDING)
            self._transition(EngineState.SUSPENDED)
            return EngineState.SUSPENDED
        if not self._suspend_requested and self._state is EngineState.SUSPENDED:
            self._transition(EngineState.RESUMING)
            self._transition(EngineState.RUNNING)
            return EngineState.RUNNING
        return None

    # -- visibility -------------------------------------------------------

    def set_backgrounded(self, backgrounded: bool, reason: str) -> None:
        """Publish a coalesced presentation-visibility transition.

        Driven both by window events (observed here) and by the loop, which
        discovers the same condition the hard way when the compositor starts
        withholding presentations. Both routes funnel through this one
        coalescing setter so observers see one edge, not two.
        """
        if backgrounded == self._backgrounded:
            return
        self._backgrounded = backgrounded
        if backgrounded:
            self._logger.info(f"Application backgrounded ({reason}).")
            self._events.dispatch_event("on_background")
        else:
            self._logger.info(f"Application foregrounded ({reason}).")
            self._events.dispatch_event("on_foreground")

    # -- internals --------------------------------------------------------

    def _on_window_close(self) -> bool:
        self.stop("window close request (OS/WM)")
        return True

    def _on_window_background(self) -> None:
        self.set_backgrounded(True, "window hidden")

    def _on_window_foreground(self) -> None:
        self.set_backgrounded(False, "window exposed")

    def _transition(self, state: EngineState) -> None:
        """Move the machine, log it, and publish it as
        ``on_engine_state(previous, current)``. No-op if already there; an
        unexpected move is reported and then made (see _LEGAL_TRANSITIONS)."""
        if state is self._state:
            return
        if state not in _LEGAL_TRANSITIONS[self._state]:
            self._logger.warning(
                f"Unexpected engine transition: "
                f"{self._state.name} -> {state.name}"
            )
        previous, self._state = self._state, state
        self._logger.info(f"Engine state: {previous.name} -> {state.name}")
        self._events.dispatch_event("on_engine_state", previous, state)
