"""The simulation clock: what a frame IS, and when the next one is due.

The engine owns the authored rate. Nothing else does. Display realises
pictures on a panel, audio feeds a device-paced worker, and both are
consumers of this clock rather than sources for it -- which is what makes an
engine-owned clock the neutral arbiter two real-time domains can both
reference. See `docs/bps/DECOUPLED_PRESENTATION.md` for the decision and what
it replaced.

**Nominal time is `frame * step`, exactly.** `perf_counter` is used to decide
WHEN to tick and never to decide what a tick is WORTH. This used to fall out
of the arithmetic: ticks were distributed over presentation slots as a
rational, so there was no wall-clock quantity to be tempted by. Pacing
against a timer removes that protection, so the rule is now held here
deliberately. A game that reads `time` gets an exact multiple of its own
timestep on every platform, in every run, and a replay of the same inputs
produces the same numbers. `dt` exists for metrics and for nothing else --
it is what the wall clock DID, not what the frame MEANS.

**No catch-up. A frame is never asked for in less than its budget.** The
next tick falls one full step after the moment this one began, so an overrun
costs exactly the time it overran by and the frame after it still gets a
whole step to run in. The alternative -- advancing a fixed grid, letting a
late tick fire the instant it is owed -- buys the lost time back by squeezing
the following frame into whatever remains of its interval, which is the
precise thing the ceiling origin exists to prevent. Under sustained load the
simulation therefore runs SLOW rather than compressed, which is the same call
the presentation cadence made when it chose a slightly slow game over ticks
scattered onto adjacent frames.

That is affordable because the engine measures in elapsed FRAMES, not wall
time. There is no authoritative wall clock for `time` to fall behind: a
second is sixty frames, and it is sixty frames whatever the hardware did to
earn them. Ticks are never batched within an iteration either -- each gets
its own event pump and input snapshot, so edge detection can never degrade
to a batch rate.

The clock has NO hardware dependency: it is a rate and a timer, with no
window, no GL context and no refresh rate. That is what lets it be built
before every other subsystem, so `Metrics` and anything else needing the
authored rate can read it from here instead of holding a copy and racing to
be constructed first.
"""

from time import perf_counter, sleep

from blitspersecond.common import fps


#: How early the coarse sleep hands over to spin-polling. The spin burns at
#: most this much CPU per frame and buys the accuracy `sleep` will not give:
#: schedulers routinely overshoot a requested sleep by a millisecond or more,
#: and an overshoot is a missed frame deadline. Paid on every platform now
#: that presentation never blocks -- see the decision record.
_SPIN_MARGIN = 0.002

class Clock:
    """The engine's fixed-step simulation clock."""

    def __init__(self, rate: fps) -> None:
        self._rate = rate
        self._step = 1.0 / float(rate)
        self._frame = 0
        # Deliberately NOT started here. Construction happens during warm-up,
        # which opens a window and may block on slow hardware; a deadline set
        # now would be long past by the time the loop began and would read as
        # a first-frame stall or a catch-up burst. `start()` is called by the
        # loop when it actually starts servicing -- the same rule the frame
        # index and the metrics baselines follow.
        self._next_tick = 0.0
        self._last_tick = 0.0
        self._dt = 0.0
        self._running = False

    # -- what a frame is -----------------------------------------------------

    @property
    def rate(self) -> fps:
        """The rate this game was authored at."""
        return self._rate

    @property
    def step(self) -> float:
        """Seconds of nominal simulation time in one tick."""
        return self._step

    @property
    def frame(self) -> int:
        """Completed simulation ticks since the loop started servicing."""
        return self._frame

    @property
    def time(self) -> float:
        """Nominal simulation time, in seconds: `frame * step`, exactly.

        Not measured. Two runs that tick the same number of times report the
        same time here, on any hardware, whatever the wall clock did in
        between. This is the quantity game logic should use for anything it
        expects to be reproducible.
        """
        return self._frame * self._step

    @property
    def dt(self) -> float:
        """Wall seconds the last tick actually took. Diagnostic only.

        Deliberately not offered as the game's timestep. Reading this into
        simulation makes behaviour depend on machine speed and scheduler
        noise, which is exactly what a fixed step exists to prevent. Metrics
        wants it; game logic wants `step`.
        """
        return self._dt

    # -- when the next one is due -------------------------------------------

    def start(self) -> None:
        """Begin timekeeping. Called once, by the loop, as servicing begins.

        **Frame one is due one full step in, at the ceiling of the cycle.**
        Settled here rather than in three places, which is what the engine
        used to do: the cadence path primed (the first `ceil(refresh/target)
        - 1` presentations carried no tick), the wall-time fallback started a
        period in credit and ticked at once, and the backgrounded path took a
        third answer again. The ceiling origin was the answer already chosen
        for the cadence; deciding it ONCE means keeping it when the mechanism
        underneath changes, not re-deriving it per path -- which is how three
        answers happened in the first place.

        It also keeps nominal and wall time in agreement. After n ticks,
        `time` reads `n * step` and roughly `n * step` of wall time has
        actually passed. Ticking immediately would leave the simulation
        permanently one frame ahead of the clock on the wall, and audio is a
        second real-time domain that will compare the two.

        Measured: ticking immediately instead costs a constant -1.00 frame
        offset -- identical at 30, 120 and 600 ticks, so a phase error rather
        than drift, but a standing one.
        """
        now = perf_counter()
        self._next_tick = now + self._step
        self._last_tick = now
        self._running = True

    def rebase(self) -> None:
        """Discard wall time that no tick accounted for.

        Called at the lifecycle edges where an interval describes something
        other than the simulation running: resuming from suspension, arriving
        back in the foreground, or restarting after presentation was withheld.
        Without it that gap reads as tick debt and the engine sprints to catch
        up on time the game deliberately did not spend.

        This is the one fact three separate objects used to be told
        individually, with the clock itself living on a stack frame. It has an
        owner now.
        """
        now = perf_counter()
        # Re-enters at the cycle origin exactly as `start()` does, so a resume
        # gets the same full first interval a cold start gets. One policy.
        self._next_tick = now + self._step
        self._last_tick = now
        self._dt = 0.0

    @property
    def deadline(self) -> float:
        """`perf_counter` value at which the next tick is due.

        Published so the loop can service whichever of its deadlines falls
        first. The simulation and presentation run on independent timebases
        and neither divides the other, so the loop waits on the sooner of the
        two rather than assuming one paces the other.
        """
        return self._next_tick

    def due(self) -> bool:
        """Whether a tick is owed now.

        One tick at most, deliberately. A backlog is worked off across
        successive iterations -- each with its own event pump and input
        snapshot -- rather than in a batch that would sample input once for
        all of them.
        """
        if not self._running:
            return False
        return perf_counter() >= self._next_tick

    def wait(self) -> None:
        """Hold until the next tick is due: coarse sleep, then a short spin."""
        if not self._running:
            return
        remaining = self._next_tick - perf_counter()
        if remaining > _SPIN_MARGIN:
            sleep(remaining - _SPIN_MARGIN)
        while perf_counter() < self._next_tick:
            pass

    def tick(self) -> None:
        """Advance one fixed step. The imperative; `bps.loop` is what runs.

        The next deadline is one step from NOW, not one step along a fixed
        grid. A tick that overrun does not leave a debt for the next one to
        pay: time that was lost stays lost, and every frame is guaranteed a
        whole step to run in. See the module docstring -- this is the ceiling
        origin applied to every frame rather than only to the first.

        The cost is that the frame grid slides against the wall clock by any
        overshoot, including the sub-microsecond one the spin exit carries.
        That is deliberate and it is why the engine measures in frames.
        """
        now = perf_counter()
        self._dt = now - self._last_tick
        self._last_tick = now
        self._frame += 1
        self._next_tick = now + self._step
