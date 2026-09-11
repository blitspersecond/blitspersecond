"""The engine's logical screen, realised on a physical one.

The chain, the present pass, the window as an event source, and what the
counters below really count: `bps/reference/engine/display.md`.
"""

from time import perf_counter
from typing import Optional

from pyglet import clock as pyglet_scheduler

from blitspersecond.system import Logger
from blitspersecond.system.frame_pacing import FramePacingRecorder

from .events import EventBus
from .framebuffer import Framebuffer, FrameHandoff
from .pacing.session import PresentationSession
from .window.surface import Surface
from .window.viewport import PresentationViewport, integer_viewport


class Display:
    """Realise an engine's logical screen on a physical display."""

    def __init__(
        self,
        width: int,
        height: int,
        events: Optional[EventBus] = None,
    ) -> None:
        self.width = width
        self.height = height
        self._surface = Surface(
            self.width,
            self.height,
            events=events,
        )
        self._framebuffer = FrameHandoff(
            self.width,
            self.height,
            self._surface,
        )
        self._logger = Logger()
        self._swap_duration = 0.0

    # State Management
    @property
    def closed(self):
        return self._surface.closed

    @property
    def viewport(self) -> PresentationViewport:
        """Where the internal screen currently sits on the window.

        Computed on every read from `surface.get_size()`, never cached: a
        window that has been resized needs no notification and there is no
        stale geometry to go wrong. The present pass does the same thing for
        the outbound direction.

        This is the accessor the pointer reads back through. Both directions
        go through `integer_viewport`, so they cannot disagree about where the
        picture is -- and a disagreement there is a cursor that does not land
        where it points, visible only as a slight, unreproducible offset.
        """
        return integer_viewport(self.width, self.height, *self._surface.get_size())

    @property
    def refresh_rate(self) -> float | None:
        return self._surface.refresh_rate

    @property
    def dwm_clock_eligible(self) -> bool:
        return self._surface.dwm_clock_eligible

    def poll_refresh_rate(self, *, force: bool = False) -> float | None:
        """Return a newly observed current-window refresh rate, if any."""
        return self._surface.poll_refresh_rate(force=force)

    def close(self):
        self._surface.close()

    # event dispatcher stuff
    def dispatch_events(self):
        """Pump everything the window system has queued for us.

        Both queues in one call: the surface's own events, and pyglet's
        scheduled callbacks. The second is window-system housekeeping at
        iteration rate, NOT a second engine clock -- which is what keeping
        them together prevents anyone reading it as.
        """
        self._surface.dispatch_events()
        pyglet_scheduler.tick()

    def _presentation_session(
        self, *, target_rate: int, step: float
    ) -> PresentationSession:
        """Build the display-owned presentation pacing for one engine run.

        `target_rate` is a per-run argument rather than display state: it is
        capture metadata, and display has no use for the authored rate.
        """
        return PresentationSession(
            self,
            target_rate=target_rate,
            step=step,
            logger=self._logger,
            frame_pacing_factory=FramePacingRecorder.from_environment,
        )

    @property
    def pending(self) -> Framebuffer:
        """The buffer the engine composes into.

        A buffer declares its own render space when drawn into, so nothing
        that writes one needs to know a window exists, or when it is shown.
        """
        return self._framebuffer.pending

    def frame_complete(self) -> bool:
        """Flag the drawn frame finished and ready to be shown.

        Returns whether a finished frame was composed over unshown. Reachable
        only while backgrounded -- see `display.md`, *What the counters
        count*.
        """
        return self._framebuffer.complete()

    @property
    def swap_duration(self) -> float:
        """Seconds the last window-system swap blocked.

        Excludes compose and the post-process draw, deliberately: composition
        cost must not be able to masquerade as a presentation stall. This is
        what the presentation health monitor watches.
        """
        return self._swap_duration

    @property
    def dropped(self) -> int:
        """Frames composed over before being shown. See `FrameHandoff`."""
        return self._framebuffer.dropped

    @property
    def repeated(self) -> int:
        """Presents that re-showed the standing frame. See `FrameHandoff`."""
        return self._framebuffer.repeated

    def present(self) -> bool:
        """Display side: take the finished frame, draw it, swap.

        Returns whether the picture moved -- False means nothing new was
        ready and the standing frame was drawn again. That is the same code
        path, not a re-present branch.

        Promotion happens HERE, immediately before the draw, never when the
        engine flags completion. The reason on record does not survive
        tracing the loop; the rule stands until it is settled, and the
        argument is in `display.md`.

        The swap is measured as `swap_duration`.
        """
        promoted = self._framebuffer.promote()
        self._framebuffer.draw(self._surface)
        swap_started = perf_counter()
        self._surface.flip()
        self._swap_duration = perf_counter() - swap_started
        return promoted
