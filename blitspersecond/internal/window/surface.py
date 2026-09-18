"""The one window, and the sole producer of raw OS events.

Why the window model is pyglet's and the event loop is not, what the tee
guarantees, and what this class decides on its own:
`bps/reference/engine/display.md`.
"""

import pyglet
import sys
from typing import Optional

from blitspersecond.common import environment_flag
from blitspersecond.system import Logger

from ..events import EventBus
from ..pacing.path import detect_presentation_path
from ..pacing.refresh_rate import get_refresh_rate

#: What the window says before a game says anything. Deliberately the
#: engine's name rather than pyglet's default of `sys.argv[0]`: an unnamed
#: game is running on BPS, and a taskbar entry reading `main.py` says nothing
#: about what it is.
DEFAULT_TITLE = "BlitsPerSecond"

if sys.platform == "win32":
    from pyglet.libs.win32.constants import WM_DISPLAYCHANGE
    from pyglet.window.win32 import Win32EventHandler

    _display_change_event = Win32EventHandler(WM_DISPLAYCHANGE)
else:

    def _display_change_event(function):
        return function


class Surface(pyglet.window.Window):
    def __init__(
        self,
        internal_width: int,
        internal_height: int,
        events: Optional[EventBus] = None,
    ) -> None:
        # Set before super().__init__ -- pyglet dispatches events (e.g.
        # on_resize) during surface construction, and dispatch_event tees onto
        # this. A None bus simply means "no observers yet".
        self._events = events
        # display = Config().display
        self._scale = 4  # display.scale FIXME: hardcoded
        presentation_path = detect_presentation_path()
        self._tracks_window_output = presentation_path.name == "win32"
        self._refresh_check_needed = False

        screen = pyglet.display.get_display().get_default_screen()
        # One NULL-HWND desktop clock, established for the PRIMARY output; a
        # same-rate secondary is not evidence of shared phase.
        self._dwm_clock_eligible = self._tracks_window_output and bool(
            getattr(screen, "is_primary", False)
        )
        if False:  # FIXME force windowed: display.fullscreen:
            # Borderless at the active desktop mode. Never fullscreen=True
            # with a logical size: that makes pyglet call
            # ChangeDisplaySettingsEx and take an exclusive low-res mode.
            # BPS never owns display modes.
            width = screen.width
            height = screen.height
            style = self.WINDOW_STYLE_BORDERLESS
            visible = False
        else:
            width = int(internal_width * self._scale)
            height = int(internal_height * self._scale)
            style = self.WINDOW_STYLE_DEFAULT
            visible = True

        super().__init__(
            width,
            height,
            screen=screen,
            style=style,
            fullscreen=False,
            visible=visible,
            # Off on every path: the simulation is not paced by the swap.
            # `BPS_VSYNC=1` turns it back on, and exists because a blocking
            # flip returns AT a vblank and is a free phase reference on
            # platforms that genuinely block. It does nothing under DWM --
            # see `display.md` before spending time on it.
            vsync=environment_flag("BPS_VSYNC"),
            config=pyglet.gl.Config(double_buffer=True),
        )
        self.set_caption(DEFAULT_TITLE)
        if False:  # FIXME: hardcoded display.fullscreen:
            self.set_location(screen.x, screen.y)
            self.set_visible(True)
        self.switch_to()  # activate the context early
        pyglet.gl.gl_info.set_active_context()  # now GL is safe

        # Win32's legacy pyglet mode rate is integer-only, so the helper
        # prefers the active display path's rational rate there.
        self._refresh_rate = get_refresh_rate(self.screen)
        self._refresh_check_needed = False

        self._closed = False

    @property
    def closed(self):
        return self._closed

    @property
    def refresh_rate(self) -> float | None:
        return self._refresh_rate

    @property
    def dwm_clock_eligible(self) -> bool:
        """Whether the current Win32 output may use the desktop DWM clock."""
        return self._dwm_clock_eligible

    def poll_refresh_rate(self, *, force: bool = False) -> float | None:
        """Return the Win32 window's current output rate when it needs a check.

        `Window.screen` is fixed at construction and answers where the window
        STARTED. `get_window_screen()` uses `MonitorFromWindow`, which is the
        OS's current assignment for this HWND -- that is why it is asked
        instead. `force` covers a change made while event delivery was
        unavailable.
        """
        if not self._tracks_window_output:
            return None
        if not force and not self._refresh_check_needed:
            return None
        self._refresh_check_needed = False
        try:
            get_window_screen = getattr(self, "get_window_screen", None)
            if not callable(get_window_screen):
                self._dwm_clock_eligible = False
                return None
            screen = get_window_screen()
            self._dwm_clock_eligible = bool(getattr(screen, "is_primary", False))
            refresh_rate = get_refresh_rate(screen)
        except (AttributeError, IndexError, OSError):
            # A failed topology query must not leave a previously accepted
            # absolute clock attached to a window whose output is now unknown.
            self._dwm_clock_eligible = False
            return None
        if refresh_rate is not None:
            self._refresh_rate = refresh_rate
        return refresh_rate

    def on_move(self, x: int, y: int) -> None:
        """Re-query Win32's monitor assignment after the event batch."""
        self._refresh_check_needed = True

    @_display_change_event
    def _event_display_change(
        self,
        _message: int,
        _bits_per_pixel: int,
        _size: int,
    ) -> None:
        """Notice Win32 mode/topology changes without owning display policy."""
        self._refresh_check_needed = True

    def dispatch_event(self, *args) -> None:
        """Tee every event this Surface dispatches onto the EventBus.

        The Surface stays the sole producer of raw OS events; observers
        subscribe to the bus, so nothing outside this package depends on the
        concrete Surface.

        **The queue-drain path (`dispatch_pending_events`) calls
        `EventDispatcher.dispatch_event` directly and bypasses this
        override**, which is what makes the tee fire exactly once. Teeing
        from the drain path as well doubles every queued event.
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

        Marks itself closed and releases the GL surface. It does NOT own the
        EventLoop or the frame schedule and does not touch them;
        `BlitsPerSecond` observes `closed` and tears the loop down.
        """
        if not self._closed:
            self._closed = True
            Logger().info("Surface closing.")
            super().close()
