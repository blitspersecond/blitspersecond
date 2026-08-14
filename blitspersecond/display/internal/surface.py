import pyglet
from typing import Optional

from blitspersecond.system import Config, Logger, EventBus

from .presentation.path import detect_presentation_path
from .refresh_rate import get_refresh_rate


class Surface(pyglet.window.Window):
    def __init__(
        self,
        events: Optional[EventBus] = None,
    ) -> None:
        # Set before super().__init__ -- pyglet dispatches events (e.g.
        # on_resize) during surface construction, and dispatch_event tees onto
        # this. A None bus simply means "no observers yet".
        self._events = events
        display = Config().display
        self._width = display.width
        self._height = display.height
        self._scale = display.scale
        presentation_path = detect_presentation_path()

        screen = pyglet.display.get_display().get_default_screen()
        if display.fullscreen:
            # "Fullscreen" is deliberately borderless at the active desktop
            # mode. Passing fullscreen=True together with BPS's logical size
            # makes pyglet call ChangeDisplaySettingsEx on Windows, selecting
            # an exclusive low-resolution mode. BPS never owns display modes.
            width = screen.width
            height = screen.height
            style = self.WINDOW_STYLE_BORDERLESS
            visible = False
        else:
            width = int(self._width * self._scale)
            height = int(self._height * self._scale)
            style = self.WINDOW_STYLE_DEFAULT
            visible = True

        super().__init__(
            width,
            height,
            screen=screen,
            style=style,
            fullscreen=False,
            visible=visible,
            # Native X11 asks GLX to pace swaps. Direct XWayland deliberately
            # does not: a narrowly missed Mutter vblank otherwise costs a full
            # extra refresh, so PresentationSession owns a monotonic deadline
            # grid there just as it does for the validated Gamescope path.
            vsync=presentation_path.requests_vsync,
            config=pyglet.gl.Config(double_buffer=True),
        )
        self.set_caption("BlitsPerSecond")
        if display.fullscreen:
            self.set_location(screen.x, screen.y)
            self.set_visible(True)
        self.switch_to()  # activate the context early
        pyglet.gl.gl_info.set_active_context()  # now GL is safe

        # Query the screen pyglet actually selected for this window. Win32's
        # legacy pyglet mode rate is integer-only, so the helper prefers the
        # active display path's rational rate there. Other backends and Win32
        # query failures retain pyglet's existing mode-rate behaviour.
        self._refresh_rate = get_refresh_rate(self.screen)

        self._closed = False

    @property
    def closed(self):
        return self._closed

    @property
    def refresh_rate(self) -> float | None:
        return self._refresh_rate

    def output_refresh_rates(self) -> tuple[float, ...]:
        """Current mode rate of every connected output, freshly enumerated.

        This is the candidate set a measured presentation rate is checked
        against, so it must be re-read rather than cached here: displays are
        plugged in and modes are changed while the engine runs. Enumerating
        screens is a round trip to the window system -- callers treat it as
        expensive and ask rarely.

        Under Gamescope this is the single synthetic screen, which is correct:
        that path owns the mapping to real outputs and never exposes them.
        """
        try:
            screens = pyglet.display.get_display().get_screens()
        except Exception as error:  # pragma: no cover -- platform dependent
            Logger().error(f"Could not enumerate display outputs: {error}")
            return ()
        rates = (get_refresh_rate(screen) for screen in screens)
        return tuple(rate for rate in rates if rate)

    def dispatch_event(self, *args) -> None:
        """Tee every event this Surface dispatches onto the EventBus.

        The Surface stays the sole producer of raw OS events; observers
        (input, audio, ...) subscribe to the bus instead of to the Surface,
        so nothing outside `display/` depends on the concrete Surface.

        Signature mirrors BaseWindow.dispatch_event(*args) -- args[0] is the
        event type. The queue-drain path (dispatch_pending_events) calls
        EventDispatcher.dispatch_event directly, bypassing this override, so
        the tee fires exactly once per event.
        """
        super().dispatch_event(*args)
        if self._events is not None and args and args[0] in self._events.event_types:
            self._events.dispatch_event(*args)

    def dispatch_events(self):
        """Override to prevent errors during shutdown."""
        if not self._closed:
            try:
                super().dispatch_events()
            except Exception as e:
                Logger().error(f"Error dispatching events: {e}")
                self._closed = True

    def on_key_press(self, symbol: int, modifiers: int) -> None:
        """Leave every key, including Escape, to the engine input stream.

        Pyglet's BaseWindow default translates an unmodified Escape press into
        on_close. BPS reserves on_close for genuine OS/window-manager close
        requests; gameplay keys are observed through the EventBus instead.
        """

    def close(self):
        """Close the surface itself only.

        The surface does NOT own the EventLoop or the frame schedule, so it does
        not touch them here -- it just flags itself closed and releases the GL
        surface. BlitsPerSecond (which created the loop) observes `closed` and
        performs loop teardown. See BlitsPerSecond._shutdown.
        """
        if not self._closed:
            self._closed = True
            Logger().info("Surface closing.")
            super().close()
