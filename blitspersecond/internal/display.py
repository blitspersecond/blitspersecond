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

from blitspersecond.graphics.internal import Layers
from blitspersecond.system import Logger
from blitspersecond.system.frame_pacing import FramePacingRecorder

from .events import EventBus
from .framebuffer import FramebufferPair
from .pacing.session import PresentationSession
from .window.surface import Surface


class Display:
    """Realise an engine's logical screen on a physical display."""

    def __init__(
        self,
        width: int,
        height: int,
        layers: Layers,
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
        )
        # Handed in, not built here. `BlitsPerSecond` owns the one draw order
        # there is and lends it out as `bps.layers`; display is where it gets
        # WALKED, which is not the same as where it lives. Building it here
        # would put the object a game reaches for behind a screen the game
        # cannot name.
        self._layers = layers
        self._logger = Logger()
        self._swap_duration = 0.0
        self._compose_duration = 0.0
        # Stands in for the layer compose pass while the graphics rebuild is
        # out. Every frame is this colour; a game that sets it is the
        # simplest possible proof that ticks reach the panel.
        self._background = (0.0, 0.0, 0.0)

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

    @property
    def background(self) -> tuple[float, float, float]:
        """The colour the screen clears to, until layers return."""
        return self._background

    @background.setter
    def background(self, colour: tuple[float, float, float]) -> None:
        self._background = colour

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

    def _compose(self) -> None:
        """Draw each enabled layer into the framebuffer, background to foreground.

        The render space is declared explicitly from the framebuffer's own size
        rather than inherited from whatever projection/viewport the window last
        left active. Layers are authored at this internal resolution, so the
        projection is a 1:1 ortho over it and the viewport matches it exactly --
        independent of the on-screen window size or scale.

        An empty draw order returns after the clear, so a game that has added
        no layers costs exactly what it did before this pass existed: one
        clear. That is deliberate rather than incidental -- the frame-pacing
        numbers in `docs/bps/DECOUPLED_PRESENTATION.md` were all measured
        against a clear-and-swap, and they stay comparable only while the
        no-layer case still IS a clear and a swap.
        """
        fbo = self._framebuffer.next
        fbo.clear(self._background)
        if not len(self._layers):
            return

        w, h = fbo.width, fbo.height
        fbo.bind()
        self._surface.projection = Mat4.orthogonal_projection(0, w, 0, h, -1, 1)
        glViewport(0, 0, w, h)
        # Blend is COMPOSITING state, so it is set here rather than by each
        # layer. Every pillar wanted the identical two calls -- a layer drawn
        # over another must respect its alpha, which is what compositing means
        # -- and repeating them per layer per frame was N redundant state
        # changes and N places for one of them to drift. The present pass
        # disables blend again for its own quad, which is why this has to be
        # established here rather than once at startup.
        #
        # A layer wanting a different blend mode is the unbuilt `Layer.blend`,
        # and this loop is where it will be read.
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        for layer in self._layers:
            if layer.enabled:
                layer._composite()  # syncs its own GPU state, executes its own draw
        glDisable(GL_BLEND)
        fbo.unbind()

    @property
    def swap_duration(self) -> float:
        """Seconds the last window-system swap blocked, excluding compose and
        the post-process draw. This is the vsync-block signal the engine's
        presentation health monitor watches: composition cost must not be able
        to masquerade as a presentation stall."""
        return self._swap_duration

    @property
    def compose_duration(self) -> float:
        """Seconds the last compose took, excluding the present path entirely.

        Billed at game rate: one compose per tick, whatever the panel does.
        """
        return self._compose_duration

    @property
    def dropped(self) -> int:
        """Frames composed but never shown. See `FramebufferPair.dropped`."""
        return self._framebuffer.dropped

    @property
    def repeated(self) -> int:
        """Presents that re-showed the held frame. See `FramebufferPair`."""
        return self._framebuffer.repeated

    def compose(self) -> bool:
        """Engine side: build one frame into `next` and flag it complete.

        Returns whether a finished frame was discarded unshown -- the engine
        outran the panel and overwrote a frame display never promoted.

        Called once per simulation tick, never per present. Composition
        therefore bills at GAME rate whatever the panel is doing, which is
        what the old `recompose=False` flag existed to arrange and now falls
        out of the buffer pair instead.

        The engine does not wait for this frame to be shown, and does not
        learn whether it ever was. Overwriting an unpromoted frame is
        decimation working as designed above the crossover.
        """
        start = perf_counter()
        self._compose()
        self._compose_duration = perf_counter() - start
        return self._framebuffer.complete()

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
