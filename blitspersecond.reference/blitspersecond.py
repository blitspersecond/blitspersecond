from functools import cached_property
from typing import Callable, Optional

from blitspersecond.audio.driver import Driver
from blitspersecond.audio.engine import AudioEngine
from blitspersecond.common import SingletonMeta
from blitspersecond.display import Display, Layers
from blitspersecond.input import Pad, Pads, Ports
from blitspersecond.input.kbm import Keyboard, KeyboardMouse, Mouse
from blitspersecond.lifecycle import EngineState
from blitspersecond.loop import Loop
from blitspersecond.system import Config, Logger, Metrics, EventBus


class BlitsPerSecond(metaclass=SingletonMeta):
    """The engine -- a 640x360 screen you draw to sixty times a second.

    A whole program is three lines: make the engine, write a tick, run it.

        from blitspersecond import BlitsPerSecond

        def tick(bps):
            ...  # move things, draw things -- runs once per frame

        BlitsPerSecond().run(tick)

    Everything you need hangs off the engine `bps` your tick is handed:

        bps.layers     the screens you draw onto (back to front)
        bps.kbm        the one local keyboard-and-pointer device
        bps.keyboard   the keyboard mapped into Ports
        bps.mouse      the mapped pointer
        bps.pads       the game controllers
        bps.gamepad    the first routed gamepad, or the lowest-id active pad
        bps.ports      the four virtual input stations
        bps.audio      the audio engine
        bps.tick       the current scene's fixed-step function
        bps.stop()     end the run (OS/WM close requests do this too)

    Each of those is its own little manual -- open one to see what it can do.

    ---
    Under the hood it's a singleton: the first `BlitsPerSecond()` boots the
    display and makes a GL context current on this thread, so a later
    `BlitsPerSecond()` just hands back the same engine -- an idempotent
    "engine exists" barrier. Context *currency* stays per-thread; GL work
    belongs on the mainloop thread, which lazy texture upload at compose
    time guarantees.
    """

    @classmethod
    def current(cls) -> Optional["BlitsPerSecond"]:
        """Return the existing engine, or ``None`` without constructing one."""
        return SingletonMeta._instances.get(cls)

    def __init__(self):
        self._config = Config()
        self._logger = Logger()
        self._metrics = Metrics()
        self._events = EventBus()
        self._running = False
        self._tick: Optional[Callable[["BlitsPerSecond"], None]] = None
        self._state = EngineState.STOPPED
        self._user_suspended = False
        self._backgrounded = False
        self._display = Display(events=self._events)
        self._audio = AudioEngine()
        self._audio_driver = Driver(self._audio)

        # Shutdown trigger. OS/WM close requests -- the close button, Alt+F4,
        # and platform equivalents -- arrive as on_close and go to _shutdown,
        # the one place that touches the window, via Display. Pyglet's default
        # on_close is wired to the global app.event_loop rather than our
        # private loop, so it never stops our loop; hence the explicit observer.
        self._events.push_handlers(
            on_close=self._on_window_close,
            on_hide=self._on_window_background,
            on_show=self._on_window_foreground,
            on_expose=self._on_window_foreground,
            on_activate=self._on_window_foreground,
        )

        # Pyglet exposes one keyboard stream and one pointer stream without
        # physical-device identity, so they are one local input device with
        # two honest facets. It negotiates its EventBus connection with this
        # engine rather than accepting the bus at construction.
        self._kbm = KeyboardMouse()._attach(self)
        # Gamepads are raw evdev, unseen by the window loop: a reader thread
        # buffers their events and Pads snapshots them beside the others.
        self._pads = Pads(events=self._events, logger=self._logger)

        self._logger.info("BlitsPerSecond initialized.")

    def _on_window_close(self) -> bool:
        self._shutdown("window close request (OS/WM)")
        return True

    def _on_window_background(self) -> None:
        self._set_backgrounded(True, "window hidden")

    def _on_window_foreground(self) -> None:
        self._set_backgrounded(False, "window exposed")

    def _set_backgrounded(self, backgrounded: bool, reason: str) -> None:
        """Publish a coalesced presentation-visibility transition.

        This is deliberately independent of EngineState. Games may turn the
        notification into suspend()/resume(), a pause scene, or no policy at
        all. The engine itself keeps fixed-step simulation running.
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

    def _set_state(self, state: EngineState) -> None:
        """Transition the lifecycle state, log it, and publish it on the bus
        as on_engine_state(previous, current). No-op if already there."""
        if state is self._state:
            return
        previous, self._state = self._state, state
        self._logger.info(f"Engine state: {previous.name} -> {state.name}")
        self._events.dispatch_event("on_engine_state", previous, state)

    @property
    def state(self) -> EngineState:
        return self._state

    @property
    def backgrounded(self) -> bool:
        """Whether presentation is hidden or being withheld by the compositor."""
        return self._backgrounded

    def suspend(self) -> None:
        """Request suspension: simulation ticks freeze, but the loop keeps
        pumping events and presenting (the last composed frame stays up).
        Takes effect at the next loop boundary; safe to call from a game
        callback or an event handler. Backgrounding never calls this
        automatically; games choose whether visibility changes imply pause."""
        self._user_suspended = True

    def resume(self) -> None:
        """Clear a suspend() request at the next lifecycle boundary."""
        self._user_suspended = False

    def stop(self) -> None:
        """Request engine shutdown. Idempotent; safe from callbacks and
        event handlers."""
        self._shutdown("stop() called by game code")

    @property
    def tick(self) -> Optional[Callable[["BlitsPerSecond"], None]]:
        """The current scene's fixed-step function, or None.

        Rebinding during a tick takes effect on the following tick, so scene
        transitions never replace code halfway through one simulation step.
        """
        return self._tick

    @tick.setter
    def tick(self, callback: Optional[Callable[["BlitsPerSecond"], None]]) -> None:
        if callback is not None and not callable(callback):
            raise TypeError("tick must be callable or None")
        self._tick = callback

    def _shutdown(self, reason: str = "unspecified") -> None:
        """Tear down the loop this instance owns. Idempotent. Every shutdown
        names its trigger in the log so an unexpected one is diagnosable."""
        if self._state not in (EngineState.STOPPING, EngineState.STOPPED):
            self._logger.info(f"Shutdown requested: {reason}.")
            self._set_state(EngineState.STOPPING)
        self._running = False
        self._audio_driver.close()
        if not self._display.closed:
            self._display.close()
        ports = self.__dict__.get("ports")
        if ports is not None:
            ports._disconnect_all()

    def run(
        self,
        callback: Optional[Callable[["BlitsPerSecond"], None]] = None,
    ) -> None:
        """Run the machine on its display-owned presentation cadence.

        Pass a callback as shorthand for assigning `bps.tick`, or bind and
        rebind `bps.tick` yourself as scenes change.
        """
        Loop.run(self, callback)

    @property
    def logger(self) -> Logger:
        return self._logger

    @property
    def config(self) -> Config:
        return self._config

    @property
    def metrics(self) -> Metrics:
        return self._metrics

    @property
    def events(self) -> EventBus:
        return self._events

    @property
    def layers(self) -> Layers:
        return self._display.layers

    @property
    def audio(self) -> AudioEngine:
        return self._audio

    @property
    def keyboard(self) -> Keyboard:
        """The keyboard currently mapped into Ports."""
        return self.ports.keyboard

    @property
    def kbm(self) -> KeyboardMouse:
        return self._kbm

    @property
    def mouse(self) -> Mouse:
        """The mouse currently mapped into Ports."""
        return self.ports.mouse

    @property
    def pads(self) -> Pads:
        return self._pads

    @property
    def gamepad(self) -> Pad | None:
        """The first routed gamepad, then the lowest-id active Pad, or None."""
        return self.ports.gamepad or self._pads.gamepad

    @cached_property
    def ports(self) -> Ports:
        """The four virtual input stations, created on first use.

        The one local keyboard-and-pointer device begins on P1.
        """
        ports = Ports()
        ports.p1.connect(self._kbm)
        return ports
