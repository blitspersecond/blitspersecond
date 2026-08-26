"""The machine loop: service every subsystem in one controlled order.

Each iteration pumps the window, honours lifecycle requests, snapshots input,
runs any due fixed game ticks, and dispatches the completed result to display.
Display owns the mapping from those ticks to presentation slots. Audio commands
issued by a game tick are consumed independently by the realtime audio worker.
Audio starts as a blocking readiness gate before presentation timing begins,
then runs continuously until terminal engine shutdown.

The loop does not own the state machine, and it no longer starts one: an
engine is already RUNNING when it arrives here, having blocked through
warm-up in its own constructor. The loop brackets its spell of servicing
(`servicing`, `stopped`) and offers `Lifecycle` one boundary per iteration to
apply deferred requests (`settle`); which state may follow which is the
Lifecycle's business, not this file's. Everything else it touches on the
engine is a subsystem it is here to service, in order.
"""

from time import perf_counter
from typing import TYPE_CHECKING

from pyglet import clock

from blitspersecond.lifecycle import EngineState

if TYPE_CHECKING:
    from .blitspersecond import BlitsPerSecond


class Loop:
    """Engine-private controller for the machine's main loop."""

    @staticmethod
    def run(engine: "BlitsPerSecond") -> None:
        """Run input, game, lifecycle, and output at controlled boundaries.

        Ordering contract:

        1. Pump the window event queue.
        2. Apply lifecycle transitions.
        3. Snapshot input, run each fixed game tick due, and advance the
           clips any sprites are playing.
        4. Dispatch the resulting frame to display.
        5. Service pyglet's scheduled callbacks.

        A hidden display withholds frame submission but continues the rest of
        the machine at the configured game rate. Suspension is separate:
        events and presentation remain live while game ticks stop.
        """
        lifecycle = engine._lifecycle
        # Claim the machine before anything can fail: from here a stop request
        # waits for this loop to unwind rather than exiting out from under it.
        lifecycle.servicing()

        try:
            # The pacing baselines come into existence at the moment the loop
            # does, never during the constructor's warm-up -- that is what
            # keeps slow hardware startup from reading as a first-frame stall.
            presentation = engine._display._presentation_session()
        except BaseException as error:
            # The one shutdown that does not leave through `stopped()`: a
            # failed start is worth a traceback, and re-raising gives one
            # (plus its own non-zero status) where a bare exit code would not.
            # A loop is servicing by now, so this marks and unwinds rather
            # than exiting from here.
            lifecycle.fail(f"engine startup failed: {type(error).__name__}")
            raise

        last_tick = perf_counter()
        try:
            while lifecycle.running:
                try:
                    priming_this_frame = presentation.begin_iteration()
                    # Latched by hand because the loop needs the EDGE, not the
                    # level. Reporting that edge is the Lifecycle's job to take
                    # over when the loop's own shape is settled.
                    was_backgrounded = lifecycle.backgrounded

                    engine._display.dispatch_events()
                    presentation.after_dispatch()
                    # The WM-close observer can stop the engine during dispatch.
                    if engine._display.closed or not lifecycle.running:
                        lifecycle.stop("display closed during event dispatch")
                        break

                    if lifecycle.backgrounded and not was_backgrounded:
                        presentation.backgrounded()
                        last_tick = perf_counter()
                        engine._metrics.pace.rebase()
                    elif was_backgrounded and not lifecycle.backgrounded:
                        presentation.foregrounded()
                        priming_this_frame = False

                    # Lifecycle requests land between event dispatch and input,
                    # never part-way through a coherent game tick. Resuming
                    # invalidates the timing baselines -- wall time passed that
                    # no tick accounted for -- so they rebase rather than treat
                    # the gap as a catch-up debt.
                    if lifecycle.settle() is EngineState.RUNNING:
                        engine._metrics.pace.rebase()
                        presentation.resumed()
                        last_tick = perf_counter()

                    tick_count = presentation.ticks_due(
                        running=lifecycle.state is EngineState.RUNNING,
                        priming=priming_this_frame,
                        backgrounded=lifecycle.backgrounded,
                    )
                    if tick_count:
                        # Spread elapsed wall time evenly over a catch-up batch.
                        # Input edges belong only to the first tick because no
                        # event pump occurs between batch ticks.
                        now = perf_counter()
                        per_tick = (now - last_tick) / tick_count
                        last_tick = now
                        for _ in range(tick_count):
                            # Latch the scene before input callbacks; rebinding
                            # loop therefore takes effect next complete tick.
                            # Never None -- an unset loop is a do-nothing
                            # lambda -- so this is a bare call, not a test.
                            game_loop = engine._loop
                            engine._metrics.pace.push(per_tick)
                            engine._kbm._update()
                            engine._pads.update()
                            game_loop()
                            # Clips advance after the game has had its say,
                            # so a play() issued this tick starts on this
                            # tick's frame -- and a catch-up batch advances
                            # animation once per tick it is making up, not
                            # once per rendered frame.
                            engine._display.layers._advance()

                    presentation.after_simulation()

                    # A game tick may have called stop(), closing the surface.
                    if not lifecycle.running or engine._display.closed:
                        break

                    if lifecycle.backgrounded:
                        # Never feed an occlusion-throttled swap chain. Audio's
                        # device-paced worker and all game policy remain live.
                        clock.tick()
                        presentation.wait_backgrounded()
                        continue

                    if presentation.present(tick_count):
                        # Display reports the condition; the lifecycle publishes
                        # visibility and deliberately chooses no pause policy.
                        lifecycle.set_backgrounded(
                            True,
                            "presentations withheld by compositor",
                        )
                        presentation.backgrounded()
                        last_tick = perf_counter()
                        engine._metrics.pace.rebase()

                    clock.tick()
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
        # Report while logging is alive, not from atexit after test runners
        # have already closed their handlers.
        engine._metrics.pace.report()
        engine._logger.info("Main loop has exited.")
        # Terminal: publishes STOPPED now that the epilogue is done, then
        # leaves the process with the status the shutdown earned. `run()`
        # does not return.
        lifecycle.stopped()
