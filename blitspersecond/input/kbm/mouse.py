"""The pointer, in the space the game actually draws in.

The window is where the pointer physically is; 640x360 is what receives it.
Everything here exists to turn the first into the second.

See `docs/bps/INPUT_STREAM.md`.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import Enum, auto
from math import floor
from typing import TYPE_CHECKING, Callable, Deque

from pyglet.window import mouse as _mouse

from blitspersecond.common import Location, fps
from blitspersecond.common.screen import SCREEN
from blitspersecond.input.stream import InputStream, InputWindow

if TYPE_CHECKING:
    from blitspersecond.internal.events import EventBus
    from blitspersecond.internal.window import PresentationViewport


def screen_location(
    x: float, y: float, viewport: PresentationViewport
) -> Location:
    """Where a window pixel lands on the internal screen.

    Always answers, and always with a pixel that is on the screen. A pointer
    out in the letterbox margin is held at the edge it left through, per axis,
    so it slides along that edge rather than stopping dead.

    **That is the behaviour the player already has when there is no margin.**
    Fill the display exactly and the OS confines the pointer to it: push right
    and x sticks while y keeps tracking. The margin is an artefact of the
    window not being a whole multiple of the screen, and the game should not
    be able to tell the two situations apart -- so the engine synthesises what
    the OS would have done. Reporting the true out-of-viewport coordinate, or
    refusing to answer, would make a pointer behave one way fullscreen and
    another windowed, for a reason nobody can see.

    Nothing is lost by clamping here. `delta` is summed unclamped by the
    ingress precisely so that a drag at the screen edge keeps its distance;
    position answers "where is it", and off the screen there is no better
    answer than the edge.

    The viewport is the only argument because it already carries the screen's
    size -- `width // scale` is the internal width -- so there is no second
    copy of 640x360 to pass in and no way to hand this a viewport and a screen
    that disagree.

    `x` may be fractional: pyglet divides by the display scale on the way out,
    so a HiDPI window delivers floats.
    """

    width = viewport.width // viewport.scale
    height = viewport.height // viewport.scale
    # Pyglet's origin is bottom-left with y up; the screen's is top-left with
    # y down. The `- 1` is the difference between a coordinate and an extent:
    # the topmost row of the viewport is at `y + height - 1`, and it is
    # internal row 0. Floor, not truncate -- `int()` rounds toward zero, which
    # sends a coordinate one short of the left edge to column 0 instead of to
    # the -1 the clamp is expecting.
    column = floor((x - viewport.x) / viewport.scale)
    row = floor((viewport.y + viewport.height - 1 - y) / viewport.scale)
    return Location(
        min(max(column, 0), width - 1),
        min(max(row, 0), height - 1),
    )


#: The buttons the engine reports, named for what they MEAN rather than where
#: they sit. Handedness is the OS's job -- libinput and the compositor apply
#: the left-handed pointer mapping below us, so by the time an event arrives
#: pyglet's LEFT already IS the player's primary button. Swapping again here
#: would double-swap a left-handed setup. This is the same rule the keyboard
#: follows for layout: the OS has applied the player's own configuration and
#: the engine reads the result, never the hardware.
PRIMARY = _mouse.LEFT
SECONDARY = _mouse.RIGHT
MIDDLE = _mouse.MIDDLE
BACK = _mouse.MOUSE4
FORWARD = _mouse.MOUSE5

#: Everything the engine will report, and nothing else. A device that sends
#: more -- an MMO thumb-grid, a sniper trigger -- has those dropped at ingress
#: rather than reaching a window, so `held` stays a set a game can enumerate.
#:
#: PRIMARY and SECONDARY are the floor every real pointer meets. MIDDLE, BACK
#: and FORWARD are reported but must never be a game's only way to do
#: something: the thumb pair is absent on plenty of honest mice, and
#: wheel-click is a button bound onto an axis control -- stiff to press
#: without scrolling, easy to hit while scrolling. That is design law, not a
#: check the engine can make: window events cannot reveal a button's absence,
#: because a mouse only proves a button by pressing it.
BUTTONS = frozenset((PRIMARY, SECONDARY, MIDDLE, BACK, FORWARD))


class MouseEvent(Enum):
    """What one pointer fact is."""

    #: The pointer moved. Carries `delta` as well as `position`, because the
    #: two are not derivable from each other -- see `MouseFact.delta`.
    MOVE = auto()
    PRESS = auto()
    RELEASE = auto()
    #: The wheel turned. Vertical only: horizontal scroll and wheel tilt are
    #: dropped at ingress, on the same grounds as MIDDLE -- the wheel already
    #: has a job, tilt is a second input bound onto the same part, and it is
    #: undetectably absent on many mice, so it could only ever duplicate an
    #: input that has to exist anyway.
    SCROLL = auto()


@dataclass(frozen=True, slots=True)
class MouseFact:
    """One pointer fact, in the order it arrived within a frame.

    **Every fact is positioned.** That is the type's reason for existing: a
    click is a question about where, and collapsing a frame's events down to
    one end-of-interval position -- which the reference did -- answers it with
    wherever the pointer finished. Drag quickly and click, and the click is
    reported somewhere it never happened. `screen_location` always answers, so
    this field is never absent.

    `held` carries the same invariant the keyboard's does, and for the same
    reason -- see `KeyFact`:

        A transition carries the held-set in which its own button is present.

    A press carries the set after its button was added; a release carries the
    set before its button was removed.
    """

    kind: MouseEvent
    #: Where on the 640x360 screen, clamped. Always meaningful.
    position: Location
    #: PRESS and RELEASE only; None otherwise. One of `BUTTONS`.
    button: int | None
    #: MOVE only. Internal pixels moved, float and unclamped -- so a drag that
    #: runs into the screen edge keeps its distance where `position` cannot.
    #: Not derivable from consecutive positions, which are clamped and whole.
    delta: tuple[float, float]
    #: SCROLL only. Wheel clicks: positive away from the player.
    scroll: float
    modifiers: int
    held: frozenset[int]


@dataclass(frozen=True, slots=True)
class MouseScan:
    """The pointer's state at one cut, and the edges of that interval.

    `position` is where it finished; `delta` and `scroll` are what it did.
    They answer different questions and the first cannot produce the other
    two: position is clamped and whole, so at the screen edge consecutive
    positions report a stationary pointer while the hand keeps moving.
    """

    #: Always a pixel on the screen. Before the pointer has been seen at all
    #: this is the centre -- a DECLARED default, not a measurement. There is
    #: nothing to ask: pyglet has no "where is the pointer", and until an
    #: event arrives the engine genuinely does not know. The centre is the
    #: choice a game makes on hardware that only offers relative counters,
    #: and it is conventional because it is the least wrong thing to say --
    #: visible, neutral, and not read by a player as a statement. `(0, 0)`
    #: would look like a reading and put it in a corner.
    position: Location
    #: Internal pixels moved this interval, summed and unclamped, y growing
    #: down. Float: at a 4x window a single window pixel is a quarter of a
    #: screen pixel, and that is real motion which must not round away.
    delta: tuple[float, float]
    #: Wheel clicks this interval, summed. Positive away from the player.
    scroll: float
    held: frozenset[int]
    pressed: frozenset[int]
    released: frozenset[int]
    #: This interval's releases came from focus loss, not from the player
    #: letting go. The buttons may well still be physically down.
    cleared: bool = False


MouseWindow = InputWindow[MouseScan, MouseFact]


#: Arrivals staged in one frame before the excess is dropped and counted.
#: Per device, and the same figure the keyboard uses for the same reason: a
#: subsystem that can exhaust memory when something goes wrong is worse than
#: one that says it is going wrong. A 1000Hz pointer offers about seventeen
#: events per frame at 60Hz, so this is two orders of magnitude clear.
_STAGING_LIMIT = 512


class _Arrival(Enum):
    """Staging kinds. Private: ENTER and CLEAR never reach a reader."""

    MOVE = auto()
    PRESS = auto()
    RELEASE = auto()
    SCROLL = auto()
    #: The pointer came back over the window. Carries a position and no
    #: motion -- it may have travelled any distance while it was away.
    ENTER = auto()
    CLEAR = auto()


class MouseIngress:
    """Subscribes to the bus, stages arrivals, settles them at the cut.

    Whoever builds this must keep it: the bus stores bound methods as weak
    references, so an ingress nothing holds is collected and silently
    unsubscribes itself.

    **Raw in staging, mapped at the cut.** Handlers record window coordinates
    and return; the viewport is read once per cut and the whole interval is
    mapped through it. So a window resized mid-frame maps that frame
    consistently instead of half one way, and the mapping cost is per frame
    rather than per event.
    """

    # `__weakref__` is not optional here: the bus stores bound handlers as
    # weak references, and a slotted class without it cannot be referred to
    # weakly at all -- push_handlers raises outright.
    __slots__ = (
        "__weakref__",
        "_dropped",
        "_held",
        "_position",
        "_staging",
        "_stream",
        "_viewport",
    )

    def __init__(
        self,
        events: EventBus,
        rate: fps,
        viewport: Callable[[], PresentationViewport],
    ) -> None:
        # A callable rather than a Display, because this is built before the
        # window exists -- the bus holds no queue, so an ingress that
        # subscribed later would miss the events the window dispatches as it
        # comes up. It is not a testing affordance; a headless test is simply
        # the beneficiary, and gets to state the geometry it maps against
        # instead of inheriting one.
        self._viewport = viewport
        self._staging: Deque[tuple[_Arrival, float, float, float, float]] = (
            deque()
        )
        self._stream: InputStream[MouseScan, MouseFact] = InputStream(rate)
        self._held: set[int] = set()
        self._position = Location(SCREEN.width // 2, SCREEN.height // 2)
        self._dropped = 0
        events.push_handlers(
            on_mouse_motion=self._on_mouse_motion,
            on_mouse_drag=self._on_mouse_drag,
            on_mouse_press=self._on_mouse_press,
            on_mouse_release=self._on_mouse_release,
            on_mouse_scroll=self._on_mouse_scroll,
            on_mouse_enter=self._on_mouse_enter,
            on_deactivate=self._on_deactivate,
        )

    @property
    def stream(self) -> InputStream[MouseScan, MouseFact]:
        """Settled history. Readers take windows from here; the accumulating
        frame is not reachable from anywhere."""
        return self._stream

    @property
    def dropped(self) -> int:
        """Arrivals lost to the staging limit since the engine started."""
        return self._dropped

    # -- bus handlers: record and return, nothing else --------------------
    #
    # These run inside the window pump. They return None deliberately: a
    # truthy return is EVENT_HANDLED and would stop the event reaching
    # handlers below, including Lifecycle's and the keyboard's.
    #
    # `on_mouse_leave` is deliberately not among them. It said `inside` was
    # False, and `inside` does not exist: 640x360 is the receiving surface,
    # a pointer off it is clamped to its edge, and a pointer off the WINDOW
    # simply stops producing events. There is nothing to record.

    def _stage(
        self,
        kind: _Arrival,
        x: float = 0.0,
        y: float = 0.0,
        a: float = 0.0,
        b: float = 0.0,
    ) -> None:
        if len(self._staging) < _STAGING_LIMIT:
            self._staging.append((kind, x, y, a, b))
        else:
            self._dropped += 1

    def _on_mouse_motion(self, x, y, dx, dy) -> None:
        self._stage(_Arrival.MOVE, x, y, dx, dy)

    def _on_mouse_drag(self, x, y, dx, dy, buttons, modifiers) -> None:
        # A drag is motion with buttons down. The buttons have their own
        # press and release events, so this is recorded as plain motion.
        self._stage(_Arrival.MOVE, x, y, dx, dy)

    def _on_mouse_press(self, x, y, button, modifiers) -> None:
        self._stage(_Arrival.PRESS, x, y, button, modifiers)

    def _on_mouse_release(self, x, y, button, modifiers) -> None:
        self._stage(_Arrival.RELEASE, x, y, button, modifiers)

    def _on_mouse_scroll(self, x, y, scroll_x, scroll_y) -> None:
        # scroll_x is dropped here rather than carried and ignored later.
        self._stage(_Arrival.SCROLL, x, y, scroll_y)

    def _on_mouse_enter(self, x, y) -> None:
        self._stage(_Arrival.ENTER, x, y)

    def _on_deactivate(self) -> None:
        # Queued rather than applied here, so it takes its place in arrival
        # order: a press that landed just before focus was lost still gets
        # its press edge in this interval, and then its release from the
        # clear.
        self._stage(_Arrival.CLEAR)

    # -- the engine's boundary --------------------------------------------

    def flush(self) -> None:
        """Discard staged arrivals and release everything held.

        Called on resume. Ticks stop while an engine is suspended but the
        window keeps pumping, so staging would otherwise land as one enormous
        window describing motion that was never aimed at the game. Position
        is deliberately kept: it is state, not history, and wherever the
        pointer is now is the truth whether or not the game was watching.
        """
        self._staging.clear()
        self._stage(_Arrival.CLEAR)

    def cut(self, frame: int) -> MouseWindow:
        """Settle the interval that just ended into one frozen window.

        Called once per frame by the engine, at the head of the frame, before
        the clock turns -- so `frame` is the number of the frame now closing.
        Called on EVERY frame, including frames where nothing arrived.
        """
        viewport = self._viewport()
        scale = viewport.scale
        pressed: set[int] = set()
        released: set[int] = set()
        facts: list[MouseFact] = []
        moved_x = moved_y = 0.0
        scrolled = 0.0
        cleared = False

        while self._staging:
            kind, x, y, a, b = self._staging.popleft()

            if kind is _Arrival.CLEAR:
                # Unwind one button at a time so each release carries the set
                # as it stood at that moment, rather than everything
                # vanishing at once.
                cleared = True
                before = set(self._held)
                for button in sorted(self._held):
                    facts.append(
                        MouseFact(
                            MouseEvent.RELEASE,
                            self._position,
                            button,
                            (0.0, 0.0),
                            0.0,
                            0,
                            frozenset(before),
                        )
                    )
                    before.remove(button)
                    released.add(button)
                self._held.clear()
                continue

            self._position = screen_location(x, y, viewport)

            if kind is _Arrival.ENTER:
                # A position and nothing else. No fact: the pointer did not
                # travel from where it was last seen to here in any sense a
                # reader could use, and claiming a MOVE would put a jump in
                # the path of anything reconstructing a gesture.
                continue

            if kind is _Arrival.MOVE:
                # Pyglet's dy is up-positive; the screen's y grows down.
                moved_x += a / scale
                moved_y -= b / scale
                facts.append(
                    MouseFact(
                        MouseEvent.MOVE,
                        self._position,
                        None,
                        (a / scale, -b / scale),
                        0.0,
                        0,
                        frozenset(self._held),
                    )
                )

            elif kind is _Arrival.SCROLL:
                scrolled += a
                facts.append(
                    MouseFact(
                        MouseEvent.SCROLL,
                        self._position,
                        None,
                        (0.0, 0.0),
                        a,
                        0,
                        frozenset(self._held),
                    )
                )

            elif kind is _Arrival.PRESS:
                button = int(a)
                # Beyond the ideal mouse: dropped here, so nothing downstream
                # has to know the button existed.
                if button not in BUTTONS:
                    continue
                if button not in self._held:
                    self._held.add(button)
                    pressed.add(button)
                # After adding: the set contains the button pressed.
                facts.append(
                    MouseFact(
                        MouseEvent.PRESS,
                        self._position,
                        button,
                        (0.0, 0.0),
                        0.0,
                        int(b),
                        frozenset(self._held),
                    )
                )

            else:  # RELEASE
                button = int(a)
                if button not in BUTTONS:
                    continue
                # Before removing: the set contains the button released.
                before_release = frozenset(self._held)
                if button in self._held:
                    self._held.remove(button)
                    released.add(button)
                facts.append(
                    MouseFact(
                        MouseEvent.RELEASE,
                        self._position,
                        button,
                        (0.0, 0.0),
                        0.0,
                        int(b),
                        before_release,
                    )
                )

        window: MouseWindow = InputWindow(
            frame=frame,
            scan=MouseScan(
                position=self._position,
                delta=(moved_x, moved_y),
                scroll=scrolled,
                held=frozenset(self._held),
                pressed=frozenset(pressed),
                released=frozenset(released),
                cleared=cleared,
            ),
            facts=tuple(facts),
        )
        self._stream.append(window)
        return window
