from time import perf_counter
from typing import Optional

from pyglet.gl import glViewport
from pyglet.math import Mat4

from blitspersecond.system import Config, Logger, EventBus, Metrics
from blitspersecond.system.monitor.frame_pacing import (
    FramePacingRecorder,
    start_deadline_from_environment,
)

from .internal import Surface, Framebuffer, PresentationSession
from .layers import Layers


class Display:
    def __init__(
        self,
        events: Optional[EventBus] = None,
    ) -> None:
        self._surface = Surface(
            events=events,
        )
        display = Config().display
        self._framebuffer = Framebuffer(
            display.width,
            display.height,
        )
        self._layers = Layers()
        self._logger = Logger()
        self._swap_duration = 0.0

    # State Management
    @property
    def closed(self):
        return self._surface.closed

    @property
    def refresh_rate(self) -> float | None:
        return self._surface.refresh_rate

    def output_refresh_rates(self) -> tuple[float, ...]:
        """Current mode rate of every connected output (see Surface)."""
        return self._surface.output_refresh_rates()

    def poll_refresh_rate(self, *, force: bool = False) -> float | None:
        """Return a newly observed current-window refresh rate, if any."""
        return self._surface.poll_refresh_rate(force=force)

    def close(self):
        self._surface.close()

    @property
    def layers(self) -> Layers:
        return self._layers

    # event dispatcher stuff
    def dispatch_events(self):
        self._surface.dispatch_events()

    def _presentation_session(self) -> PresentationSession:
        """Build the display-owned pacing state for one engine run."""
        config = Config().display
        return PresentationSession(
            self,
            target_rate=config.fps,
            spin_pacing=config.spin_pacing,
            logger=self._logger,
            frame_pacing_factory=FramePacingRecorder.from_environment,
            diagnostic_deadline=start_deadline_from_environment(),
        )

    def _compose(self) -> None:
        """Draw each enabled layer into the framebuffer, background to foreground.

        The render space is declared explicitly from the framebuffer's own size
        rather than inherited from whatever projection/viewport the window last
        left active. Layers are authored at this internal resolution, so the
        projection is a 1:1 ortho over it and the viewport matches it exactly --
        independent of the on-screen window size or scale.
        """
        fbo = self._framebuffer
        w, h = fbo.width, fbo.height

        start = perf_counter()
        fbo.clear()
        fbo.bind()
        self._surface.projection = Mat4.orthogonal_projection(0, w, 0, h, -1, 1)
        glViewport(0, 0, w, h)
        for layer in self._layers:
            if layer.enabled:
                layer._composite()  # drawable syncs GPU state + executes its own batch
        fbo.unbind()
        # Compose cost only -- excludes the post-process draw + vsync/present.
        Metrics().render.push(perf_counter() - start)

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
        if recompose:
            self._compose()
        self._framebuffer.draw(self._surface)

    def present(self) -> None:
        """Swap the prepared frame to the screen. Blocks on vsync when the
        driver throttles; the block is measured as swap_duration."""
        swap_started = perf_counter()
        self._surface.flip()
        self._swap_duration = perf_counter() - swap_started

    def flip(self) -> None:
        """Prepare and present back-to-back (the vsync-paced normal path)."""
        self.prepare()
        self.present()
