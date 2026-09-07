from time import perf_counter
from typing import Optional

from pyglet import clock as pyglet_scheduler
from pyglet.gl import (
    GL_BLEND,
    GL_ONE_MINUS_SRC_ALPHA,
    GL_SRC_ALPHA,
    glBlendFunc,
    glDisable,
    glEnable,
    glViewport,
)
from pyglet.math import Mat4

from blitspersecond.system import Logger
from blitspersecond.system.frame_pacing import FramePacingRecorder

from .events import EventBus
from .framebuffer import Framebuffer, FramebufferPair
from .pacing.session import PresentationSession
from .window.surface import Surface


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
        self._framebuffer = FramebufferPair(
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

        Both queues, deliberately in one call: the surface's own events, and
        pyglet's scheduled callbacks. The latter used to be a bare
        `clock.tick()` in the machine loop, which read as a second engine
        clock sitting beside the real one. It is neither -- it is window-
        system housekeeping, it belongs at iteration rate, and this is where
        iteration-rate window chores live.
        """
        self._surface.dispatch_events()
        pyglet_scheduler.tick()

    def _presentation_session(
        self, *, target_rate: int, step: float
    ) -> PresentationSession:
        """Build the display-owned presentation pacing for one engine run.

        `target_rate` is a per-run argument rather than display state: it is
        capture metadata, not configuration. Display has no use for the
        authored rate and deliberately does not keep one.
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
        """The buffer free to write into: composed this frame, not yet shown.

        The display's whole part in composition -- it owns the targets and
        hands the writable one over. `present` flips which of the two this
        is, so nothing that writes a frame ever learns a swap happened, and a
        buffer declares its own render space when drawn into, so nothing that
        writes one needs to know a window exists either.

        **Read it, never keep it.** There are two buffers, built once and
        alive for the process; this returns whichever is currently free. A
        present that PROMOTES exchanges the two references, so a reference
        held across one would write into the buffer being shown. Nothing is
        allocated to make that happen and no pixels are copied -- see
        `FramebufferPair.promote`.

        Nothing in the engine has to remember the rule, because the order
        makes it moot: the pacer swaps and only then issues the tick, so the
        tick resolves this property fresh and always gets the buffer that
        just stopped being displayed. The game never meets a buffer.
        """
        return self._framebuffer.pending

    @property
    def current(self) -> Framebuffer:
        """The buffer on the display surface -- the frame being shown."""
        return self._framebuffer.current

    def frame_complete(self) -> bool:
        """Flag the drawn frame finished and ready to be shown.

        Returns whether a finished frame was discarded unshown -- the engine
        outran the panel and overwrote one the display never promoted.
        Expected below the rate crossover; above it, an overrun.
        """
        return self._framebuffer.complete()

    @property
    def swap_duration(self) -> float:
        """Seconds the last window-system swap blocked, excluding compose and
        the post-process draw. This is the vsync-block signal the engine's
        presentation health monitor watches: composition cost must not be able
        to masquerade as a presentation stall."""
        return self._swap_duration

    @property
    def dropped(self) -> int:
        """Frames composed but never shown. See `FramebufferPair.dropped`."""
        return self._framebuffer.dropped

    @property
    def repeated(self) -> int:
        """Presents that re-showed the held frame. See `FramebufferPair`."""
        return self._framebuffer.repeated

    def present(self) -> bool:
        """Display side: promote the newest finished frame, draw it, swap.

        Returns whether the picture actually moved -- False means the held
        frame was re-presented because the engine had nothing new ready.

        Promotion happens HERE, immediately before the draw, rather than when
        the engine flagged completion -- see `FramebufferPair` for why that
        costs a frame of latency if moved earlier. If no new frame is ready
        the held one is drawn again, which is the whole of the above-crossover
        behaviour.

        The swap is measured as `swap_duration`. Nothing blocks in it now
        that client vsync is off on every path; a long one means the
        compositor is withholding presentations.
        """
        promoted = self._framebuffer.promote()
        self._framebuffer.draw(self._surface)
        swap_started = perf_counter()
        self._surface.flip()
        self._swap_duration = perf_counter() - swap_started
        return promoted
