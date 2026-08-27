from time import perf_counter
from typing import Optional

from blitspersecond.system import Logger, EventBus  # Metrics

from blitspersecond.display.internal import Framebuffer, Surface

# from blitspersecond.system.monitor.frame_pacing import (
#     FramePacingRecorder,
#     start_deadline_from_environment,
# )

# from .internal import Surface, Framebuffer, PresentationSession
# from .layers import Layers


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
        self._framebuffer = Framebuffer(
            self.width,
            self.height,
        )
        # self._layers = Layers()
        self._logger = Logger()
        self._swap_duration = 0.0

    # State Management
    @property
    def closed(self):
        return self._surface.closed

    @property
    def refresh_rate(self) -> float | None:
        return self._surface.refresh_rate

    def poll_refresh_rate(self, *, force: bool = False) -> float | None:
        """Return a newly observed current-window refresh rate, if any."""
        return self._surface.poll_refresh_rate(force=force)

    def close(self):
        self._surface.close()

    # @property
    # def layers(self) -> Layers:
    #     return self._layers

    # event dispatcher stuff
    def dispatch_events(self):
        self._surface.dispatch_events()

    # def _presentation_session(self) -> PresentationSession:
    #     """Build the display-owned pacing state for one engine run."""
    #     return PresentationSession(
    #         self,
    #         target_rate=self.framerate,
    #         spin_pacing=True,  # FIXME: this is what we wanted to test - do we need this anymore?
    #         logger=self._logger,
    #         frame_pacing_factory=FramePacingRecorder.from_environment,
    #         diagnostic_deadline=start_deadline_from_environment(),
    #     )

    # def _compose(self) -> None:
    #     """Draw each enabled layer into the framebuffer, background to foreground.

    #     The render space is declared explicitly from the framebuffer's own size
    #     rather than inherited from whatever projection/viewport the window last
    #     left active. Layers are authored at this internal resolution, so the
    #     projection is a 1:1 ortho over it and the viewport matches it exactly --
    #     independent of the on-screen window size or scale.
    #     """
    #     fbo = self._framebuffer
    #     w, h = fbo.width, fbo.height

    #     start = perf_counter()
    #     fbo.clear()
    #     fbo.bind()
    #     self._surface.projection = Mat4.orthogonal_projection(0, w, 0, h, -1, 1)
    #     glViewport(0, 0, w, h)
    #     for layer in self._layers:
    #         if layer.enabled:
    #             layer._composite()  # drawable syncs GPU state + executes its own batch
    #     fbo.unbind()
    #     # Compose cost only -- excludes the post-process draw + vsync/present.
    #     # Declared as `display.compose.ms` and already carried by Metrics;
    #     # it reads as NaN -- not measured -- until this pass comes back.
    #     Metrics().compose_ms.push((perf_counter() - start) * 1000.0)

    @property
    def swap_duration(self) -> float:
        """Seconds the last window-system swap blocked, excluding compose and
        the post-process draw. This is the vsync-block signal the engine's
        presentation health monitor watches: composition cost must not be able
        to masquerade as a presentation stall."""
        return self._swap_duration

    def prepare(self, recompose: bool = True) -> None:
        """Compose the layers and queue the post-process draw. After this,
        the frame is fully built and only the swap remains -- so the caller
        can hold a prepared frame and present() it on its own schedule
        (the engine's unthrottled fallback waits out its deadline between
        the two calls).

        recompose=False skips the layer pass and queues the post-process
        draw from the framebuffer's existing contents. That's the path for
        presentations that carried no simulation tick: the picture can only
        change at tick boundaries, so a 60-on-120 cadence re-presents the
        held frame and composition bills at GAME rate, not display rate."""
        # if recompose:
        #     self._compose()
        self._framebuffer.draw(self._surface)

    def present(self) -> None:
        """Swap the prepared frame to the screen. Blocks on vsync when the
        driver throttles; the block is measured as swap_duration."""
        swap_started = perf_counter()
        self._surface.flip()
        self._swap_duration = perf_counter() - swap_started
