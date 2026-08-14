"""The machine loop: service every subsystem in one controlled order.

Each iteration pumps the window, honours lifecycle requests, snapshots input,
runs any due fixed game ticks, and dispatches the completed result to display.
Display owns the mapping from those ticks to presentation slots. Audio commands
issued by a game tick are consumed independently by the realtime audio worker.
Audio starts as a blocking readiness gate before presentation timing begins,
then runs continuously until terminal engine shutdown.
"""

from time import perf_counter
from typing import Callable, Optional, TYPE_CHECKING

from pyglet import clock

from blitspersecond.lifecycle import EngineState

if TYPE_CHECKING:
    from blitspersecond.blitspersecond import BlitsPerSecond


class Loop:
    """Engine-private controller for the machine's main loop."""

    @staticmethod
    def run(
        engine: "BlitsPerSecond",
        callback: Optional[Callable[["BlitsPerSecond"], None]] = None,
    ) -> None:
        """Run input, game, lifecycle, and output at controlled boundaries.

        Ordering contract:

        1. Pump the window event queue.
        2. Apply lifecycle transitions.
        3. Snapshot input and run each fixed game tick due.
        4. Dispatch the resulting frame to display.
        5. Service pyglet's scheduled callbacks.

        A hidden display withholds frame submission but continues the rest of
        the machine at the configured game rate. Suspension is separate:
        events and presentation remain live while game ticks stop.
        """
        self = engine
        self._set_state(EngineState.STARTING)
        if callback is not None:
            self.tick = callback

        try:
            # Device activation and any future kernel warm-up belong entirely
            # to STARTING. Presentation deadlines and the first tick baseline
            # do not exist yet, so slow hardware startup cannot become a false
            # first-frame stall or catch-up batch.
            self._audio_driver.start()
            presentation = self._display._presentation_session()
        except BaseException as error:
            self._shutdown(
                f"engine startup failed: {type(error).__name__}"
            )
            self._set_state(EngineState.STOPPED)
            raise

        self._running = True
        last_tick = perf_counter()
        self._set_state(EngineState.RUNNING)
        try:
            while self._running:
                try:
                    priming_this_frame = presentation.begin_iteration()
                    was_backgrounded = self._backgrounded

                    self._display.dispatch_events()
                    presentation.after_dispatch()
                    # The WM-close observer can call _shutdown during dispatch.
                    if self._display.closed or not self._running:
                        self._shutdown("display closed during event dispatch")
                        break

                    if self._backgrounded and not was_backgrounded:
                        presentation.backgrounded()
                        last_tick = perf_counter()
                        self._metrics.pace.rebase()
                    elif was_backgrounded and not self._backgrounded:
                        presentation.foregrounded()
                        priming_this_frame = False

                    # Lifecycle requests land between event dispatch and input,
                    # never part-way through a coherent game tick.
                    suspended = self._user_suspended
                    if suspended and self._state is EngineState.RUNNING:
                        self._set_state(EngineState.SUSPENDING)
                        self._set_state(EngineState.SUSPENDED)
                    elif not suspended and self._state is EngineState.SUSPENDED:
                        self._set_state(EngineState.RESUMING)
                        self._metrics.pace.rebase()
                        presentation.resumed()
                        last_tick = perf_counter()
                        self._set_state(EngineState.RUNNING)

                    tick_count = presentation.ticks_due(
                        running=self._state is EngineState.RUNNING,
                        priming=priming_this_frame,
                        backgrounded=self._backgrounded,
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
                            # tick therefore takes effect next complete tick.
                            tick = self._tick
                            self._metrics.pace.push(per_tick)
                            self._kbm._update()
                            self._pads.update()
                            if tick is not None:
                                tick(self)

                    presentation.after_simulation()

                    # A game tick may have called stop(), closing the surface.
                    if not self._running or self._display.closed:
                        break

                    if self._backgrounded:
                        # Never feed an occlusion-throttled swap chain. Audio's
                        # device-paced worker and all game policy remain live.
                        clock.tick()
                        presentation.wait_backgrounded()
                        continue

                    if presentation.present(tick_count):
                        # Display reports the condition; the engine publishes
                        # visibility and deliberately chooses no pause policy.
                        self._set_backgrounded(
                            True,
                            "presentations withheld by compositor",
                        )
                        presentation.backgrounded()
                        last_tick = perf_counter()
                        self._metrics.pace.rebase()

                    clock.tick()
                except Exception as error:
                    self._logger.error(f"Error during main loop: {error}")
                    self._shutdown(
                        f"main loop exception: {type(error).__name__}"
                    )
                    break
        except KeyboardInterrupt:
            self._shutdown("KeyboardInterrupt")

        self._set_state(EngineState.STOPPED)
        presentation.finish()
        # Report while logging is alive, not from atexit after test runners
        # have already closed their handlers.
        self._metrics.pace.report()
        self._logger.info("Main loop has exited.")
