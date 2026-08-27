"""The machine loop: service every subsystem in one controlled order.

Two independent deadlines drive this loop and neither divides the other. The
engine's `Clock` says when the next simulation tick is due, at the rate the
game was authored at. Display's `PresentationSession` says when the next
frame is due, at the rate the panel runs at. The loop waits on whichever
falls sooner, then services whatever has come due.

That is the whole shape, and it is what the decoupling bought. Previously
presentation GRANTED ticks -- the loop asked display how many simulation
steps it was allowed this iteration -- so a presentation that arrived late
was a simulation that ran late, and a compositor that charged an extra
refresh for a narrowly missed vblank made the game run at 75% speed. The two
clocks are separate now, so a late frame costs a late frame.

Consequences worth knowing before changing anything here:

* **No catch-up batch.** The clock owes at most one tick at a time, so there
  is no loop over `tick_count` and no wall time to spread across it. Every
  tick gets its own event pump and input snapshot, which is what keeps input
  edge detection at the authored rate rather than the panel's.
* **Above the crossover** (panel faster than the game) the loop iterates on
  presentation and simply does not tick on most iterations.
* **Below it** (panel slower) the loop iterates on the clock and presents on
  some iterations. Frames the panel cannot show are never composed. The
  simulation is unaffected either way, which is the point.

Audio commands issued by a game tick are consumed independently by the
realtime audio worker; it is a third clock domain and it references neither
of these two.

The loop does not own the state machine. An engine is already RUNNING when it
arrives here, having blocked through warm-up in its own constructor. The loop
brackets its spell of servicing (`servicing`, `stopped`) and offers
`Lifecycle` one boundary per iteration to apply deferred requests (`settle`).
"""

from time import perf_counter, sleep
from typing import TYPE_CHECKING

from blitspersecond.lifecycle import EngineState

if TYPE_CHECKING:
    from .blitspersecond import BlitsPerSecond


# How early the coarse sleep hands over to spin-polling. Matches the margin
# the clock and the presentation session each use for their own waits; this
# is the combined wait, on whichever deadline is nearer.
_SPIN_MARGIN = 0.002


def _wait_until(deadline: float) -> None:
    """Hold until `deadline`: coarse sleep, then a short spin.

    `sleep` alone routinely overshoots by a millisecond or more, and an
    overshoot is a missed deadline. The spin costs at most `_SPIN_MARGIN` of
    CPU and is paid on every platform now that nothing blocks in the swap.
    """
    remaining = deadline - perf_counter()
    if remaining > _SPIN_MARGIN:
        sleep(remaining - _SPIN_MARGIN)
    while perf_counter() < deadline:
        pass


class Loop:
    """Engine-private controller for the machine's main loop."""

    @staticmethod
    def run(engine: "BlitsPerSecond") -> None:
        """Run input, game, lifecycle, and output at controlled boundaries.

        Ordering contract, per iteration:

        1. Wait for the sooner of the two deadlines.
        2. Pump the window event queue.
        3. Apply lifecycle transitions.
        4. If a tick is due, advance the clock and run one game step.
        5. If a presentation is due, put the frame up.

        A backgrounded display stops presenting but keeps ticking: hiding a
        window changes what is DISPLAYED, not what the simulation rate is.
        Suspension is the opposite -- events and presentation stay live while
        game ticks stop.
        """
        lifecycle = engine._lifecycle
        clock = engine._clock
        metrics = engine._metrics
        display = engine._display
        # Claim the machine before anything can fail: from here a stop request
        # waits for this loop to unwind rather than exiting out from under it.
        lifecycle.servicing()

        try:
            # The baselines come into existence at the moment the loop does,
            # never during the constructor's warm-up -- that is what keeps
            # slow hardware startup from reading as a first-frame stall.
            presentation = display._presentation_session(
                target_rate=int(clock.rate)
            )
        except BaseException as error:
            # The one shutdown that does not leave through `stopped()`: a
            # failed start is worth a traceback, and re-raising gives one.
            lifecycle.fail(f"engine startup failed: {type(error).__name__}")
            raise

        clock.start()
        try:
            while lifecycle.running:
                try:
                    _wait_until(min(clock.deadline, presentation.deadline))
                    presentation.begin_iteration()
                    ticked = False
                    # Latched by hand because the loop needs the EDGE, not the
                    # level. Reporting that edge is the Lifecycle's job to take
                    # over when the loop's own shape is settled.
                    was_backgrounded = lifecycle.backgrounded

                    display.dispatch_events()
                    presentation.after_dispatch()
                    # The WM-close observer can stop the engine during dispatch.
                    if display.closed or not lifecycle.running:
                        lifecycle.stop("display closed during event dispatch")
                        break

                    if lifecycle.backgrounded and not was_backgrounded:
                        presentation.backgrounded()
                    elif was_backgrounded and not lifecycle.backgrounded:
                        presentation.foregrounded()

                    # Lifecycle requests land between event dispatch and the
                    # tick, never part-way through a coherent game step.
                    # Resuming invalidates the timing baselines -- wall time
                    # passed that no tick accounted for -- so they rebase
                    # rather than treat the gap as a catch-up debt.
                    if lifecycle.settle() is EngineState.RUNNING:
                        clock.rebase()
                        metrics.rebase()
                        presentation.resumed()

                    if lifecycle.state is EngineState.RUNNING and clock.due():
                        # Turn the frame index FIRST. Everything staged since
                        # the last tick belongs to the frame now closing --
                        # including the presentations that followed it, so a
                        # tick and the swaps that showed it share one slot.
                        metrics.advance()
                        clock.tick()
                        metrics.frame_ms.push(clock.dt * 1000.0)
                        # Latch the scene before the call; rebinding `loop`
                        # therefore takes effect next complete tick. Never
                        # None -- an unset loop is a do-nothing lambda -- so
                        # this is a bare call, not a test.
                        game_loop = engine._loop
                        started = perf_counter()
                        game_loop()
                        # Measured around the call rather than taken from
                        # `clock.dt`: dt is the whole iteration including the
                        # wait, so only this sees a game-side spike.
                        metrics.gameloop_ms.push(
                            (perf_counter() - started) * 1000.0
                        )
                        ticked = True

                    presentation.after_simulation()

                    # A game tick may have called stop(), closing the surface.
                    if not lifecycle.running or display.closed:
                        break

                    # The tick built a world state; turn it into a picture and
                    # hand it to display. Once per TICK, never per present --
                    # composition bills at game rate whatever the panel is
                    # doing, which is what the old `recompose` flag existed to
                    # arrange. Deliberately AFTER the shutdown guard above: a
                    # tick that called stop() has already torn the surface
                    # down, and composing into a dead GL context raises rather
                    # than draws.
                    if ticked:
                        if display.compose():
                            # The previous frame was overwritten before it was
                            # ever shown. Expected below the crossover; a real
                            # finding on a panel faster than the game.
                            metrics.frames_dropped.push()
                        metrics.compose_ms.push(
                            display.compose_duration * 1000.0
                        )

                    # Never feed an occlusion-throttled swap chain. The
                    # simulation carries on regardless, on its own clock.
                    if lifecycle.backgrounded or not presentation.due():
                        continue

                    stalled = presentation.present()
                    metrics.presents.push()
                    if not presentation.promoted:
                        # The held frame went up again because the engine had
                        # nothing new ready. Expected above the crossover --
                        # at 60fps on 144Hz most presents are repeats.
                        metrics.frames_repeated.push()
                    metrics.swap_ms.push(display.swap_duration * 1000.0)
                    if stalled:
                        # Display reports the condition; the lifecycle
                        # publishes visibility and deliberately chooses no
                        # pause policy.
                        lifecycle.set_backgrounded(
                            True,
                            "presentations withheld by compositor",
                        )
                        presentation.backgrounded()
                except Exception as error:
                    engine._logger.error(f"Error during main loop: {error}")
                    lifecycle.fail(
                        f"main loop exception: {type(error).__name__}"
                    )
                    break
        except KeyboardInterrupt:
            # 128 + SIGINT, the shell's convention for "killed by Ctrl-C".
            lifecycle.stop("KeyboardInterrupt", 130)

        presentation.finish()
        # Reported while logging is alive, not from atexit after test runners
        # have already closed their handlers. Advance first, or the last
        # tick's work is still in the staging buffer and goes unreported.
        metrics.advance()
        metrics.report().log(engine._logger)
        engine._logger.info("Main loop has exited.")
        # Terminal: publishes STOPPED now that the epilogue is done, then
        # leaves the process with the status the shutdown earned. `run()`
        # does not return.
        lifecycle.stopped()
