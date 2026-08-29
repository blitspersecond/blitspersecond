import pyglet
import sys
from typing import Optional

from blitspersecond.common import environment_flag
from blitspersecond.system import Logger, EventBus

from ..pacing.path import detect_presentation_path
from ..pacing.refresh_rate import get_refresh_rate

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
        # DwmGetCompositionTimingInfo has exposed only one NULL-HWND desktop
        # clock since Windows 8.1.  The reference capture establishes that
        # clock for the primary output; a same-rate secondary is not enough
        # evidence that it shares the same phase, particularly across adapters.
        self._dwm_clock_eligible = self._tracks_window_output and bool(
            getattr(screen, "is_primary", False)
        )
        if False:  # FIXME force windowed: display.fullscreen:
            # "Fullscreen" is deliberately borderless at the active desktop
            # mode. Passing fullscreen=True together with BPS's logical size
            # makes pyglet call ChangeDisplaySettingsEx on Windows, selecting
            # an exclusive low-resolution mode. BPS never owns display modes.
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
            # Off by default. Began as an XWayland measure -- Mutter charges
            # a full extra refresh for a narrowly missed vblank -- and became
            # the rule when simulation stopped being paced by the swap.
            #
            # `BPS_VSYNC=1` turns it back on, and exists because a blocking
            # flip returns AT a vblank and is therefore a free phase
            # reference on platforms without a compositor clock.
            #
            # MEASURED 2026-08-27, Win32/WGL under DWM: it does nothing here.
            # pyglet applies it (`window.vsync` reads True) but `swap.ms` is
            # unchanged at 0.032ms, because the present mode is "Composed:
            # Copy with GPU GDI" -- DWM composites on its own vblank and the
            # client SwapBuffers returns immediately. There is no blocking
            # swap to be had on this path, so no phase reference from it. BPS
            # now obtains that reference from `DwmGetCompositionTimingInfo`;
            # its SDK structure requires four-byte packing (the earlier
            # natural-alignment probe was rejected with size-mismatch HRESULT
            # 0x88980090).
            #
            # Kept switchable because native X11 genuinely does block, and
            # that path is still unvalidated. DWM's clock gives BPS an honest
            # period and prevents phase drift; it does not make DWM's variable
            # queue-depth assignment deterministic for an irregular 2/3
            # client submission stream.
            vsync=environment_flag("BPS_VSYNC"),
            config=pyglet.gl.Config(double_buffer=True),
        )
        self.set_caption("BlitsPerSecond")
        if False:  # FIXME: hardcoded display.fullscreen:
            self.set_location(screen.x, screen.y)
            self.set_visible(True)
        self.switch_to()  # activate the context early
        pyglet.gl.gl_info.set_active_context()  # now GL is safe

        # Query the screen pyglet actually selected for this window. Win32's
        # legacy pyglet mode rate is integer-only, so the helper prefers the
        # active display path's rational rate there. Other backends and Win32
        # query failures retain pyglet's existing mode-rate behaviour.
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

        ``Window.screen`` is fixed at construction. Win32's
        ``get_window_screen()`` instead uses ``MonitorFromWindow``, which is
        the operating system's current monitor assignment for this HWND.
        Ordinary moves and ``WM_DISPLAYCHANGE`` mark the cached rate dirty;
        foreground restoration forces a check in case a change happened while
        event delivery was unavailable.
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
