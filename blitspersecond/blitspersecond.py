from typing import Callable, Optional


from blitspersecond.common import SingletonMeta
from blitspersecond.system import Logger, Config, EventBus
from blitspersecond.display import Display

from .loop import Loop


class BlitsPerSecond(metaclass=SingletonMeta):
    width = 640
    height = 360
    framerate = 60

    @classmethod
    def current(cls) -> Optional["BlitsPerSecond"]:
        """Return the existing engine, or ``None`` without constructing one."""
        return SingletonMeta._instances.get(cls)

    def __init__(self):
        self._logger = Logger()
        self._config = Config()
        self._event_bus = EventBus()
        self._display = Display(
            width=self.width,
            height=self.height,
            framerate=self.framerate,
            events=self._event_bus,
        )
        self._tick: Optional[Callable[["BlitsPerSecond"], None]] = None

        # Shutdown trigger. OS/WM close requests -- the close button, Alt+F4,
        # and platform equivalents -- arrive as on_close and go to _shutdown,
        # the one place that touches the window, via Display. Pyglet's default
        # on_close is wired to the global app.event_loop rather than our
        # private loop, so it never stops our loop; hence the explicit observer.
        self._event_bus.push_handlers(
            on_close=self._on_window_close,
            # on_hide=self._on_window_background,
            # on_show=self._on_window_foreground,
            # on_expose=self._on_window_foreground,
            # on_activate=self._on_window_foreground,
        )

    def _on_window_close(self) -> bool:
        self._shutdown("window close request (OS/WM)")
        return True

    def _shutdown(self, reason: str = "unspecified") -> None:
        self._running = False
        if not self._display.closed:
            self._display.close()

    @property
    def logger(self) -> Logger:
        return self._logger

    @property
    def config(self) -> Config:
        return self._config

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
