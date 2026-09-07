"""The machine loop: service every subsystem in one controlled order.

The loop walks presentation slots and asks the pacer one question at each:
**does the pending frame belong on this slot?** Yes, and it goes up and the
engine is called for the next one. No, and the frame is held and the slot
passes. Everything the engine does happens in the "yes" branch, immediately
after the swap, which is what gives it the whole interval to the next slot it
must make rather than the sliver of slack before an ideal instant.

The engine's clock counts frames and nothing else -- it has no deadline, no
grid and no wall clock. WHEN a frame is due is `origin + frame * step`, held
by the pacer because the pacer is the only thing that knows the grid it has
to be reconciled against. What comes back the other way is just the count.

That separation is the point. Presentation used to GRANT ticks: the loop
asked display how many simulation steps it was allowed, so a late present was
a late simulation, and a compositor charging a whole extra refresh for a
narrowly missed vblank ran the game at 75% speed. Nothing here can do that
now -- the pacer decides when a frame is SHOWN and never how often the engine
runs.

Audio commands issued by a tick are consumed independently by the realtime
audio worker; it is a third clock domain and references neither of these.

The loop does not own the state machine. An engine is already RUNNING when it
arrives here, having blocked through warm-up in its own constructor. The loop
brackets its spell of servicing (`servicing`, `stopped`) and offers
`Lifecycle` one boundary per slot to apply deferred requests (`settle`).
"""

from time import perf_counter
from typing import TYPE_CHECKING

from .lifecycle import EngineState

if TYPE_CHECKING:
    from ..blitspersecond import BlitsPerSecond


def _frame(engine, clock, metrics, display, layers, inputs) -> None:
    """One frame, end to end: prepare, run the game, commit.

    The shape is a transaction. Everything the engine needs true before the
    game looks happens in PREPARE; the game's tick then mutates the world;
    COMMIT draws what that world implies and leaves each layer ready for next
    time. The game therefore sees a coherent world and can never observe a
    half-written frame.

        prepare   close the interval, turn the clock, bring layers current
        ---       the game's tick runs and mutates
        commit    draw the frame, flag it complete

    Composition happens once per TICK, never per slot -- it bills at game
    rate whatever the panel is doing, which is what the retired `recompose`
    flag used to arrange. The frame index turns FIRST: everything staged
    since the last tick belongs to the frame now closing, including the
    swaps that showed it, so a tick and its presentations share one slot.

    The loop names the two phases and no per-layer step. Which layers are
    prepared, which are drawn and what each does is the compositor's, and the
    compositor is `Layers` -- see `graphics/internal/layers.py`.

    **This runs AFTER the slot's present, which is what makes `pending` free.**
    The pacer swaps and then issues the tick, so by the time COMMIT resolves
    `display.pending` it is the buffer that just stopped being shown. Nobody
    reasons about which of the two they hold, because nobody holds one across
    a present that could exchange them.
    """
    metrics.advance()
    # The third frame-closing operation, beside the other two and for the same
    # reason. Metrics closes the interval's measurements, input closes the
    # interval's events, and then the clock turns. Before the tick, so
    # `clock.frame` still holds the number of the frame now closing and the cut
    # stamps it directly rather than computing it -- the rule MetricBuffer
    # already states: only the flush knows a frame number, once.
    #
    # Unconditional. A frame where nothing arrived still cuts an empty window,
    # or "the last fifteen frames" quietly becomes "the last fifteen frames
    # that happened to have input" -- which reads correctly in any test that
    # presses a key, and is wrong exactly when the player pauses mid-motion.
    inputs.cut(clock.frame)
    clock.tick()
    # BEGIN. Every ATTACHED layer is brought current before the game looks at
    # it, so a sprite's pose and a console's echoed line are this tick's when
    # the game reads them -- the same guarantee the input cut above gives.
    # Attachment is the statement of participation; `enabled` decides only
    # whether a layer is drawn, so a hidden layer keeps advancing.
    layers.prepare()
    # Latch the scene before the call; rebinding `loop` therefore takes
    # effect next complete tick. Never None -- an unset loop is a do-nothing
    # lambda -- so this is a bare call, not a test.
    game_loop = engine._loop
    started = perf_counter()
    game_loop()
    elapsed = perf_counter() - started
    metrics.gameloop_ms.push(elapsed * 1000.0)
    if display.closed:
        # The tick called stop(). Composing into a dead GL context raises
        # rather than draws.
        return
    # COMMIT. The compositor writes the buffer the display handed over; the
    # display decides when that counts as a finished frame.
    layers.commit(display.pending)
    if display.frame_complete():
        # The previous frame was overwritten before it was ever shown.
        # Expected below the crossover; above it, an overrun.
        metrics.frames_dropped.push()
    metrics.compose_ms.push(layers.commit_duration * 1000.0)


class Loop:
    """Engine-private controller for the machine's main loop."""

    @staticmethod
    def run(engine: "BlitsPerSecond") -> None:
        """Run input, game, lifecycle, and output at controlled boundaries.

        Ordering contract, per presentation slot:

        1. Sleep until the pending frame's slot, less the latch margin.
        2. Pump the window event queue.
        3. Apply lifecycle transitions.
        4. Put the frame up.
        5. Run one game tick and compose the next.

        The pacer's question -- which slot does this frame belong on -- is
        answered by the sleep target rather than polled at each slot, so the
        loop runs at the FRAME rate, not the panel rate.

        A backgrounded display stops presenting but keeps ticking -- hiding a
        window changes what is displayed, not the simulation rate. Suspension
        is the opposite: events and presentation stay live while ticks stop.
        """
        lifecycle = engine._lifecycle
        clock = engine._clock
        metrics = engine._metrics
        display = engine._display
        layers = engine._layers
        inputs = engine._input
        # Claim the machine before anything can fail: from here a stop request
        # waits for this loop to unwind rather than exiting out from under it.
        lifecycle.servicing()

        try:
            # The baselines come into existence at the moment the loop does,
            # never during the constructor's warm-up -- that is what keeps
            # slow hardware startup from reading as a first-frame stall.
            presentation = display._presentation_session(
                target_rate=int(clock.rate), step=clock.step
            )
        except BaseException as error:
            # The one shutdown that does not leave through `stopped()`: a
            # failed start is worth a traceback, and re-raising gives one.
            lifecycle.fail(f"engine startup failed: {type(error).__name__}")
            raise

        presentation.start()
        last_tick = 0.0
        # Frame one is composed before the loop can show anything: the pacer
        # holds it until its deadline, which is one full step in. That is the
        # ceiling prime, and it arrives out of the arithmetic rather than
        # being arranged -- on a 144Hz panel the first slot at or after
        # 16.67ms is slot 3, so frame one gets a whole interval on screen.
        _frame(engine, clock, metrics, display, layers, inputs)
        try:
            while lifecycle.running:
                try:
                    presentation.wait_for(clock.frame)
                    presentation.begin_iteration()
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
                    # swap, never part-way through a coherent game tick.
                    # Resuming invalidates the grid -- wall time passed that
                    # no tick accounted for -- so it re-anchors rather than
                    # treating the gap as frames it owes.
                    if lifecycle.settle() is EngineState.RUNNING:
                        metrics.rebase()
                        # Wall time passed that no frame is charged for, and
                        # the window kept pumping through all of it. Discard
                        # what accumulated rather than delivering a window
                        # holding thirty seconds of input nobody aimed at the
                        # game -- and release held keys with it, or a key let
                        # go during the pause stays down for good.
                        inputs.flush()
                        presentation.resumed(clock.frame)

                    if lifecycle.state is not EngineState.RUNNING:
                        continue

                    presentation.after_simulation()

                    # Never feed an occlusion-throttled swap chain. The engine
                    # carries on regardless, on its own frame count.
                    if not lifecycle.backgrounded:
                        stalled = presentation.present(clock.frame)
                        metrics.presents.push()
                        if not presentation.promoted:
                            metrics.frames_repeated.push()
                        metrics.swap_ms.push(display.swap_duration * 1000.0)
                        if presentation.interval:
                            # Screen-to-screen. Bimodal by design -- frames
                            # land on slots -- and deliberately NOT the same
                            # quantity as engine.frame.ms, which is the tick
                            # interval and should be flat.
                            metrics.display_frame_ms.push(
                                presentation.interval * 1000.0
                            )
                        if stalled:
                            # Display reports the condition; the lifecycle
                            # publishes visibility and deliberately chooses no
                            # pause policy.
                            lifecycle.set_backgrounded(
                                True,
                                "presentations withheld by compositor",
                            )
                            presentation.backgrounded()

                    # A stop() from the WM or a prior tick closes the surface.
                    if not lifecycle.running or display.closed:
                        break

                    # The frame is up. Rather than build the next one now
                    # and let it sit in the back buffer for the rest of the
                    # interval, wait until the engine actually needs to start
                    # -- input is then sampled close to the moment the frame
                    # is shown. See `wait_before_tick`.
                    presentation.wait_before_tick(clock.frame + 1)
                    started = perf_counter()
                    if last_tick:
                        # The engine clock itself, measured tick to tick. Flat
                        # at the authored period; see the metric's own note.
                        metrics.frame_ms.push((started - last_tick) * 1000.0)
                    last_tick = started
                    _frame(engine, clock, metrics, display, layers, inputs)
                    presentation.observe_work(perf_counter() - started)
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
