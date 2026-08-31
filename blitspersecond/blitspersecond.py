from typing import Callable, Optional, cast


from blitspersecond.common import SCREEN, SingletonMeta, fps
from blitspersecond.graphics.internal import Layers
from blitspersecond.system import Logger, Config, Metrics

from .internal import Clock, Display, EngineState, EventBus, Lifecycle, Loop


#: The rate every game is authored at until there is a config to say
#: otherwise. A constant rather than a constructor argument: `BlitsPerSecond()`
#: is the idempotent "engine exists" barrier -- calling it is how you GET the
#: engine, from anywhere -- so an argument there reads as configuration and
#: would be quietly dropped by every call after the first. Rather than guard
#: against that, the shape simply does not allow it. Config replaces this line;
#: see the board card "Let the game author declare its tick rate".
_FRAMERATE = fps.SIXTY


def _NOTHING() -> None:
    """The default loop: a game that has not yet said what to do."""


def _as_loop(loop: object) -> Callable[[], None]:
    """Reduce whatever `bps.loop` was handed to one zero-argument callable.

    Done once, at assignment, so the sixty-times-a-second path is a bare call
    with no type inspection in it. Accepts a plain function or lambda, a
    GameLoop subclass (constructed here), or an already-built GameLoop; None
    puts the do-nothing default back.

    A class is constructed EVERY time it is assigned, deliberately. Caching
    one instance per class was tried and reverted: it made an unconditional
    transition -- `self.bps.loop = next_scene` with no guard around it --
    allocation-free, but the program was still switching scene sixty times a
    second, which is the actual defect. All it removed was the symptom that
    would have led someone to the bug, and in exchange a re-entered scene
    came back holding the previous visit's state. A game that rebuilds a
    scene every tick should feel like one.

    Deliberately duck-typed rather than an isinstance check against GameLoop:
    `gameloop` imports the engine, so importing it back would be a cycle --
    and the engine has no business depending on the class we happen to teach.
    """
    if loop is None:
        return _NOTHING
    # A class is itself callable, so this has to be settled before the
    # callable test, or `bps.loop = Title` would store the constructor.
    if isinstance(loop, type):
        loop = loop()
    # Cast rather than constrain: a loop is welcome to return something, we
    # simply ignore it. Demanding `-> None` would reject `lambda: bps.stop()`.
    if callable(loop):
        return cast(Callable[[], None], loop)
    run = getattr(loop, "run", None)
    if callable(run):
        return cast(Callable[[], None], run)
    raise TypeError(
        "loop must be a callable, a GameLoop, or None -- "
        f"got {type(loop).__name__}"
    )


class BlitsPerSecond(metaclass=SingletonMeta):
    #: Read from `common.SCREEN` rather than spelled again here. The console
    #: divides the same number by a cell size and cannot ask the engine for
    #: it -- the engine imports graphics -- so one owner or two that drift.
    width = SCREEN.width
    height = SCREEN.height

    @classmethod
    def current(cls) -> Optional["BlitsPerSecond"]:
        """Return the existing engine, or ``None`` without constructing one."""
        return SingletonMeta._instances.get(cls)

    def __init__(self):
        # FIRST, before anything else the engine owns. The clock is a rate
        # and a timer -- no window, no GL context, no refresh rate -- so it
        # has nothing to wait for and everything that needs the authored rate
        # can read it from here instead of holding a copy. That is what
        # retires the `Metrics` ordering trap below rather than guarding it.
        #
        # Read once. No setter and no override hook: every tuned constant in
        # a game -- jump arcs, input windows, animation timing -- is counted
        # in frames, so changing the rate mid-run would silently redefine all
        # of them at once. There is no coherent thing a setter could do,
        # which is why there is not one.
        self._clock = Clock(_FRAMERATE)
        self._logger = Logger()
        self._config = Config()
        self._event_bus = EventBus()
        # Built before warm-up so subsystems can declare their metrics while
        # they are constructed. Nothing is recorded yet: the frame index
        # only turns once Loop starts servicing, which is what keeps slow
        # hardware startup from reading as a stalled first frame -- the same
        # rule the presentation baselines follow.
        self._metrics = Metrics(framerate=self._clock.rate)
        # The state machine is its own object, not a scatter of flags on the
        # engine. It owns the states, the transitions between them, and the
        # requests that drive them -- including the window-system events that
        # feed it, which it subscribes to itself. The engine holds it, forwards
        # the game-facing verbs to it, and otherwise reacts to it.
        self._lifecycle = Lifecycle(events=self._event_bus, logger=self._logger)
        # Never None: an unset loop is a do-nothing lambda, so the hot path
        # calls it unconditionally instead of testing it sixty times a second.
        self._loop: Callable[[], None] = _NOTHING
        # The draw order, built before warm-up because it needs nothing to
        # exist -- it is an ordered list. Built HERE rather than by Display
        # for the same reason the clock is: a game reaches it constantly, and
        # the things a game reaches for should not be owned by the screen it
        # is deliberately not allowed to name.
        self._layers = Layers()
        # Declared before warm-up so teardown has something to look at if
        # warm-up dies half-built. Every subsystem added below gets the same
        # treatment: named here, built during STARTING, checked in _teardown.
        self._display: Optional[Display] = None

        # Teardown is driven BY the state machine rather than racing it. One
        # direction of causation: something requests a stop, the lifecycle
        # publishes STOPPING, and the subsystems the engine owns come down
        # here. Previously _shutdown both moved the state and closed the
        # window, from four call sites, each of which had to remember both.
        # Subscribed before warm-up, so a stop during it still tears down.
        self._event_bus.push_handlers(on_engine_state=self._on_engine_state)

        # Warm-up happens HERE, not in the loop: constructing an engine blocks
        # until it is ready to be run. Opening the display today; the audio
        # readiness gate will block in the same stretch. Presentation
        # deadlines and the first tick baseline deliberately do not exist yet,
        # so slow hardware startup cannot become a false first-frame stall or
        # a catch-up batch -- Loop establishes those when it actually starts.
        self._lifecycle.starting()
        try:
            self._display = Display(
                width=self.width,
                height=self.height,
                layers=self._layers,
                events=self._event_bus,
            )
        except Exception as error:
            # An engine that cannot start is an engine that stops. On a single
            # thread there is no loop servicing the lifecycle yet, so the
            # failure runs the whole shutdown -- teardown of whatever did get
            # built, then exit -- without returning here. The traceback goes to the
            # log first, because a non-zero status alone says nothing about
            # WHICH part of warm-up failed. Nothing is cached by the
            # metaclass either way, so no half-built engine outlives this.
            self._logger.exception(f"Engine start-up failed: {error}")
            self._lifecycle.fail(f"start-up failed: {type(error).__name__}")
            raise  # unreachable: the failure leaves the process
        self._lifecycle.started()

    def _on_engine_state(self, previous: EngineState, current: EngineState) -> None:
        if current is EngineState.STOPPING:
            self._teardown()

    def _teardown(self) -> None:
        """Bring down the subsystems this engine owns. Runs once, inside the
        transition to STOPPING.

        Every subsystem is checked for existence, not assumed: warm-up can
        fail part-way and stop from inside the constructor, so this runs
        against a half-built engine as readily as a fully-built one.
        """
        if self._display is not None and not self._display.closed:
            self._display.close()

    @property
    def clock(self) -> Clock:
        """The simulation clock: `frame`, `time`, `step`, and the rate.

        The engine measures in elapsed frames, not wall seconds -- `time` is
        `frame * step` exactly. See `blitspersecond.clock`.
        """
        return self._clock

    @property
    def framerate(self) -> fps:
        """The rate this game was authored at. Read-only for the reasons in
        `common.framerate`: it is the unit every tuned constant is expressed
        in, so it is settled at construction and never after. Held by the
        clock, which owns it; this is a forward for convenience."""
        return self._clock.rate

    @property
    def layers(self) -> Layers:
        """The draw order: what gets composited each frame, background to
        foreground.

            bps.layers.add(background)
            bps.layers.add(actors)
            actors.after(background)

        There is one of these and this is it. No setter, deliberately:
        replacing the collection would leave every layer in the old one
        holding a back-reference to an order nothing draws, which is a black
        screen with no error attached to it. Emptying the stack is
        `remove()`, and hiding it for a frame is `layer.enabled = False`.

        A game never constructs one -- see `graphics.internal`.
        """
        return self._layers

    # No public `display`. A game starts the engine and the engine sorts the
    # surface out; nothing a game does needs to name the screen, and with
    # layers reached through `bps.layers` above there is nothing left on
    # Display a game would want. Instruments measuring the engine reach
    # `_display` deliberately -- see `internal/__init__.py`.

    @property
    def background(self) -> tuple[float, float, float]:
        """The colour the screen clears to. TEMPORARY -- delete this the
        moment a real engine layer can draw.

        DELETING IT: drop this property and its setter, drop
        `Display._background`, and let `_compose` call `fbo.clear()` bare --
        it already defaults to black. A game wanting a coloured backdrop then
        makes a layer that draws one, which is the only thing that can
        resolve a palette index in the first place. Nothing else reads it.

        The trigger is a real ENGINE layer, not the layer machinery, which is
        already here -- `Layer`, `Layers` and the compose pass all landed
        2026-08-28 and this outlived them. Until a tile or sprite layer can
        put a pixel on screen, this is the only thing a game can make happen,
        and losing it would leave the engine with nothing to demonstrate.

        It takes a float triple rather than a palette index because the clear
        is a raw `glClearColor` with no palette bound -- see
        `graphics/palette.py`. That mismatch is a reason to delete it, not a
        reason to give the clear path a palette.
        """
        return self._display.background

    @background.setter
    def background(self, colour: tuple[float, float, float]) -> None:
        self._display.background = colour

    @property
    def logger(self) -> Logger:
        return self._logger

    @property
    def config(self) -> Config:
        return self._config

    @property
    def events(self) -> EventBus:
        return self._event_bus

    @property
    def metrics(self) -> Metrics:
        """What the engine has measured about itself. Read it for a HUD,
        declare your own counters and gauges on it, or ask it for a report."""
        return self._metrics

    def start(self, loop: object = None) -> None:
        """Start the game: service `bps.loop` on the display's cadence.

        Starts the GAME, not the engine -- the engine is already RUNNING by
        the time you get here, having blocked through warm-up during
        construction. What begins is the machine servicing you.

        Pass a loop as shorthand for assigning `bps.loop`, or assign that
        yourself and call this bare. Either way it does not return: the run
        ends by leaving the process, when the window closes or the game
        calls `stop()`.
        """
        if loop is not None:
            self.loop = loop
        Loop.run(self)

    # -- lifecycle, forwarded ------------------------------------------------
    # The verbs a game reaches for live on the object its tick is handed, but
    # the machine behind them is the Lifecycle's. These are the whole public
    # surface of it; nothing else should reach `_lifecycle`.

    @property
    def state(self) -> EngineState:
        return self._lifecycle.state

    @property
    def backgrounded(self) -> bool:
        """Whether presentation is hidden or being withheld by the compositor."""
        return self._lifecycle.backgrounded

    def suspend(self) -> None:
        """Request suspension: simulation ticks freeze, but the loop keeps
        pumping events and presenting (the last composed frame stays up).
        Takes effect at the next loop boundary; safe to call from a game
        callback or an event handler. Backgrounding never calls this
        automatically; games choose whether visibility changes imply pause."""
        self._lifecycle.suspend()

    def resume(self) -> None:
        """Clear a suspend() request at the next lifecycle boundary."""
        self._lifecycle.resume()

    def stop(self) -> None:
        """Request engine shutdown. Idempotent; safe from callbacks and
        event handlers."""
        self._lifecycle.stop("stop() called by game code")

    @property
    def loop(self) -> Callable[[], None]:
        """The current scene's fixed step, as a zero-argument callable.

        Reading this back gives you what the engine will actually call, which
        is not always what you assigned: a GameLoop comes back as its bound
        `run`, and a GameLoop subclass comes back as the bound `run` of an
        instance built at assignment. Rebinding during a step takes effect on
        the following one, so scene transitions never replace code halfway
        through a simulation step.
        """
        return self._loop

    @loop.setter
    def loop(self, loop: object) -> None:
        self._loop = _as_loop(loop)
