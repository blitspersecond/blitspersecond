# The mouse handler: the EventBus's raw pointer chatter, snapshotted into
# tick-aligned state a game can trust. The first forcing scene was the archived
# rpg_drag.py experiment (click-and-drag the Candlekeep map); its docstring is
# the design record, while this file is the supported design.
#
# DESIGN NOTES (why it's shaped this way):
#   * Buffer + snapshot, same law as the keyboard. Raw window events are
#     appended to a queue as the pump dispatches them; update() -- called by
#     the engine loop once per simulation tick, before the game callback --
#     drains the queue in order into this tick's state. Game code sees ONE
#     coherent mouse per tick: a position, button edges that survive a
#     sub-tick click, per-tick motion and scroll totals.
#   * ONE pointer stream per screen. Pyglet does not identify the physical
#     mouse behind a callback, just as it does not identify the keyboard
#     behind a key callback. The pointer and keyboard are therefore facets of
#     one local KeyboardMouse device. They route together but keep separate
#     APIs; a position is still a pointer fact, not a generic input action.
#   * Answers in INTERNAL pixels, origin top-left. Game code never sees
#     window coordinates: the snapshot inverse-maps through the present
#     transform (window scale + the framebuffer's Y flip) into the 640x360
#     screen the game actually draws. The same integer-viewport geometry used
#     by the post-pass removes letterbox/pillarbox margins here.
#   * Raw in the buffer, mapped at the snapshot. Events are buffered in
#     window coordinates and converted once per tick with the CURRENT window
#     size -- a resize mid-tick maps the whole tick consistently, and the
#     conversion cost is per-tick, not per-event.
#   * THE IDEAL MOUSE (the idealised-pad move, again), anchored in the
#     IntelliMouse lineage. The GUARANTEED mouse is PRIMARY, SECONDARY,
#     one vertical wheel, one position -- the floor every real device
#     meets (a Razer Abyssus, a laptop trackpad, the cheapest cost-shaved
#     office mouse). Everything else is the CONVENIENCE tier: a game may
#     listen, never require. That covers the 1999 Explorer thumb pair --
#     BACK and FORWARD, the one extension that became universal, but
#     absent on plenty of honest mice -- and, deliberately, MIDDLE:
#     wheel-click is never physically missing, but it is a click bound
#     onto an axis control, the same ergonomic crime as L3/R3 on pads
#     (stiff to press without scrolling, accidental while scrolling) and
#     it lives in the same class of hatred. Require-never is design law,
#     not a runtime check -- window events cannot reveal a button's
#     absence (a mouse only proves buttons by pressing them), and no law
#     can make wheel-click feel good. Everything else a device
#     offers (MMO twelve-grids, sniper triggers) is DROPPED at the
#     snapshot -- never enters held/pressed/released, held_buttons stays
#     clean -- same doctrine as L3/R3 on pads. (An MMO thumb-grid usually
#     masquerades as a KEYBOARD anyway; those events land in the keyboard
#     handler as the physical truth they are.) Wheel tilt / horizontal
#     scroll is likewise REFUSED, permanently: the same overbinding crime
#     as L3/R3 and MIDDLE -- the wheel already has a job (vertical
#     scroll), and tilt is a second input bound onto the same part. And
#     tilt wheels are absent on plenty of honest mice with absence
#     undetectable, so the axis can never be required -- it could only
#     duplicate an input that must exist anyway. scroll_x is dropped at
#     the snapshot. The position/
#     modifiers riding on an ignored button's event are still real pointer
#     facts and are kept.
#   * Buttons follow keyboard button law. held() is physical truth,
#     pressed()/released() are this-tick edges, held_for() counts ticks.
#     PRIMARY/SECONDARY are aliases of pyglet's LEFT/RIGHT values, renamed
#     for what they MEAN. Handedness is the OS's job: libinput/the
#     compositor applies the left-handed pointer mapping BELOW us, so by
#     the time an event arrives, "left" already means "the user's primary
#     button" -- swapping here would double-swap a lefty's setup (verified
#     live: an OS swap mid-session takes effect on the next click). Same
#     law as keysyms-after-OS-layout on the keyboard. Never add a swap
#     toggle; per-game rebinding is the binding layer's job, above.
#     Focus loss
#     (on_deactivate) force-releases everything held, so a drag watched via
#     released() cannot get stuck. A drag that leaves the window keeps
#     working: the implicit pointer grab delivers motion and the release
#     wherever they happen, which is exactly what map-dragging wants.
#   * Motion is TWO facts, kept separate. position is where the pointer IS
#     (clamped to the internal screen, so it is always a valid pixel to draw
#     a cursor at); delta is how far it MOVED this tick (float, unclamped,
#     summed over the tick's motion+drag events) -- clamping the position
#     must not eat drag motion at the screen edge. inside says whether the
#     OS pointer is actually over the window; position freezes at its last
#     known pixel while it is away.
#   * Observes, never consumes. Every bus handler returns None so events
#     continue down the stack, same as every other input sibling.

from __future__ import annotations

from collections import deque
from typing import Callable, Optional, TYPE_CHECKING

from pyglet.window import mouse as _mouse

from blitspersecond.common import Location
from blitspersecond.display.internal.presentation import integer_viewport
from blitspersecond.system import Config

if TYPE_CHECKING:
    from blitspersecond.system.events import EventBus

# The button vocabulary: what the buttons MEAN, not where they sit. The OS
# applies the left-handed swap below us, so pyglet's LEFT is always the
# user's primary button -- these names just stop the code lying about it.
# PRIMARY/SECONDARY are the guaranteed floor; MIDDLE and BACK/FORWARD are
# the convenience tier -- never a game's sole way to do anything (the thumb
# pair because many real mice lack it, MIDDLE because wheel-click is the
# L3/R3 of mice: always present, always miserable).
PRIMARY = _mouse.LEFT
SECONDARY = _mouse.RIGHT
MIDDLE = _mouse.MIDDLE
BACK = _mouse.MOUSE4
FORWARD = _mouse.MOUSE5

# The ideal mouse's entire button set. Anything a device sends beyond these
# (MMO grids, sniper triggers) is dropped at the snapshot, pad-junk doctrine.
_IDEAL_BUTTONS = frozenset((PRIMARY, SECONDARY, MIDDLE, BACK, FORWARD))


def _window_size() -> Optional[tuple[int, int]]:
    """The current window's size in OS pixels, or None when there's no
    window to ask (headless tests). Found via pyglet's open-window registry
    -- one window is an engine invariant, so no plumbing is worth the
    coupling (same reasoning as the keyboard's X11 probe)."""
    try:
        import pyglet

        for window in pyglet.app.windows:
            return window.get_size()
    except Exception:
        return None
    return None


class MouseHandler:
    """Tick-aligned mouse state over the EventBus, in INTERNAL pixels.

    The engine calls update() once per simulation tick (before the game
    callback); game code then queries this tick's snapshot:

        mouse.position           -- Location on the 640x360 screen, top-left
        mouse.delta              -- (dx, dy) internal px moved this tick
        mouse.held(PRIMARY)      -- is the button down (input.mouse vocabulary)
        mouse.pressed(PRIMARY)   -- did it go down THIS tick (edge)
        mouse.released(PRIMARY)  -- did it come up this tick (edge)
        mouse.held_for(PRIMARY)  -- ticks held so far (0 on the press tick)
        mouse.scroll             -- wheel clicks this tick (+up, -down)
        mouse.inside             -- is the OS pointer over the window

    One pointer stream per screen: this is the mouse facet of bps.kbm.
    """

    def __init__(
        self,
        events: "EventBus",
        probe: Optional[Callable[[], Optional[tuple[int, int]]]] = None,
    ) -> None:
        self._probe = _window_size if probe is None else probe
        display = Config().display
        self._internal = (display.width, display.height)
        # Headless fallback: the configured window is width*scale x
        # height*scale, so tests without a window still map deterministically.
        self._fallback = (
            display.width * display.scale,
            display.height * display.scale,
        )
        self._queue: deque = deque()
        self._held: dict[int, int] = {}  # button -> press tick, press order
        self._pressed: set[int] = set()
        self._released: set[int] = set()
        self._position = Location(0, 0)  # last known; (0,0) until first event
        self._delta = (0.0, 0.0)
        self._scroll = 0.0
        self._inside = False
        self._modifiers = 0
        self._pointer_visible = True
        self._ticks = 0
        events.push_handlers(
            on_mouse_motion=self._on_mouse_motion,
            on_mouse_drag=self._on_mouse_drag,
            on_mouse_press=self._on_mouse_press,
            on_mouse_release=self._on_mouse_release,
            on_mouse_scroll=self._on_mouse_scroll,
            on_mouse_enter=self._on_mouse_enter,
            on_mouse_leave=self._on_mouse_leave,
            on_deactivate=self._on_deactivate,
        )

    # -- bus observers: buffer raw window coords, consume nothing ------------

    def _on_mouse_motion(self, x: int, y: int, dx: int, dy: int) -> None:
        self._queue.append(("move", x, y, dx, dy))

    def _on_mouse_drag(self, x, y, dx, dy, buttons, modifiers) -> None:
        # A drag is a motion with buttons down; the buttons themselves are
        # tracked by their own press/release events.
        self._queue.append(("move", x, y, dx, dy))

    def _on_mouse_press(self, x, y, button, modifiers) -> None:
        self._queue.append(("press", x, y, button, modifiers))

    def _on_mouse_release(self, x, y, button, modifiers) -> None:
        self._queue.append(("release", x, y, button, modifiers))

    def _on_mouse_scroll(self, x, y, scroll_x, scroll_y) -> None:
        self._queue.append(("scroll", x, y, scroll_x, scroll_y))

    def _on_mouse_enter(self, x: int, y: int) -> None:
        self._queue.append(("enter", x, y, 0, 0))

    def _on_mouse_leave(self, x: int, y: int) -> None:
        self._queue.append(("leave", x, y, 0, 0))

    def _on_deactivate(self) -> None:
        self._queue.append(("clear", 0, 0, 0, 0))

    # -- the present transform, inverted -------------------------------------

    def _to_internal(self, x: float, y: float, ww: int, wh: int) -> Location:
        """A window coordinate (pyglet: origin bottom-left, OS pixels) as an
        internal pixel (origin top-left, clamped onto the screen). The
        post-pass's centred integer viewport is inverted here; positions in
        black margins clamp to the nearest logical edge."""
        iw, ih = self._internal
        viewport = integer_viewport(iw, ih, ww, wh)
        ix = int((x - viewport.x) / viewport.scale)
        iy = int(
            (viewport.y + viewport.height - 1 - y) / viewport.scale
        )
        return Location(
            min(max(ix, 0), iw - 1),
            min(max(iy, 0), ih - 1),
        )

    # -- the snapshot (engine loop, once per tick, before the callback) -------

    def update(self) -> None:
        """Drain buffered events into this tick's snapshot. Engine plumbing:
        the loop calls it; game code just reads the queries."""
        self._ticks += 1
        self._pressed.clear()
        self._released.clear()
        ww, wh = self._probe() or self._fallback
        iw, ih = self._internal
        viewport = integer_viewport(iw, ih, ww, wh)
        sx = sy = 1.0 / viewport.scale  # window px -> internal px
        dx = dy = scroll = 0.0
        raw_pos: Optional[tuple[float, float]] = None
        while self._queue:
            kind, a, b, c, d = self._queue.popleft()
            if kind == "move":
                raw_pos = (a, b)
                dx += c * sx
                dy -= d * sy  # pyglet dy is up-positive; internal y grows down
            elif kind == "press":
                raw_pos = (a, b)
                self._modifiers = d
                if c in _IDEAL_BUTTONS and c not in self._held:
                    self._held[c] = self._ticks
                    self._pressed.add(c)
            elif kind == "release":
                raw_pos = (a, b)
                self._modifiers = d
                if self._held.pop(c, None) is not None:
                    self._released.add(c)
            elif kind == "scroll":
                raw_pos = (a, b)
                scroll += d
            elif kind == "enter":
                raw_pos = (a, b)
                self._inside = True
            elif kind == "leave":
                self._inside = False
            elif kind == "clear":
                self._released.update(self._held)
                self._held.clear()
        if raw_pos is not None:
            self._position = self._to_internal(*raw_pos, ww, wh)
        self._delta = (dx, dy)
        self._scroll = scroll

    # -- the OS pointer ------------------------------------------------------

    @property
    def pointer_visible(self) -> bool:
        """Whether the OS cursor shows while over our window. Set False to
        draw your own cursor (a sprite at mouse.position: palettised,
        CRT-shaded, collidable -- and context-sensitive, which a hardware
        cursor can never be). The OS pointer comes back by itself the
        moment it leaves the window or focus moves away -- hiding is
        scoped to our window by the windowing system, no bookkeeping."""
        return self._pointer_visible

    @pointer_visible.setter
    def pointer_visible(self, value: bool) -> None:
        self._pointer_visible = bool(value)
        try:
            import pyglet

            for window in pyglet.app.windows:
                window.set_mouse_visible(self._pointer_visible)
        except Exception:
            pass  # headless: remembered, applied to no window

    # -- queries (this tick's snapshot) ---------------------------------------

    @property
    def position(self) -> Location:
        """Where the pointer is, as an internal pixel (origin top-left,
        always on the 640x360 screen). Freezes at its last known pixel while
        the pointer is away -- check inside to tell the difference."""
        return self._position

    @property
    def delta(self) -> tuple[float, float]:
        """How far the pointer moved this tick, in internal pixels (float:
        sub-pixel motion at high window scales is real motion and must not
        round away). Unclamped -- edge-of-screen drags keep their distance."""
        return self._delta

    @property
    def scroll(self) -> float:
        """Wheel movement this tick, in clicks: positive up/away, negative
        down/toward, summed over the tick. 0.0 on a quiet tick."""
        return self._scroll

    @property
    def inside(self) -> bool:
        """Is the OS pointer over the window. False before the first event."""
        return self._inside

    @property
    def modifiers(self) -> int:
        """The last-seen pyglet modifier bitfield from a button event
        (MOD_SHIFT | ...) -- for shift-click and friends."""
        return self._modifiers

    def held(self, button: int) -> bool:
        """Is the button physically down (pyglet.window.mouse.LEFT etc.)."""
        return button in self._held

    def pressed(self, button: int) -> bool:
        """Did the button go down this tick (edge). A sub-tick click still
        edges."""
        return button in self._pressed

    def released(self, button: int) -> bool:
        """Did the button come up this tick (edge). Focus loss releases
        everything, so a drag watched here cannot get stuck."""
        return button in self._released

    def held_for(self, button: int) -> int:
        """Ticks the button has been held: 0 on its press tick, growing by
        one per tick. 0 if not held (disambiguate with held())."""
        press_tick = self._held.get(button)
        return 0 if press_tick is None else self._ticks - press_tick

    @property
    def held_buttons(self) -> tuple[int, ...]:
        """Every button currently down, in press order."""
        return tuple(self._held)
