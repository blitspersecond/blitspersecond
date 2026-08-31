"""Keyboard ingress: window callbacks in, settled windows out.

The bus delivers keyboard events synchronously, inside the window pump, on
whatever thread pumped it. So the handlers here do the least possible work --
record the arrival and return -- and everything that needs ordering or state
happens later, at the cut, on the engine's own boundary.

Pyglet's key symbols are kept as they are. There is one keyboard transport and
there will only ever be one, so translating its symbols into a parallel BPS
numbering would be a table mapping a hundred and fifty values to themselves.
`key.W` is a predicate over the pyglet symbol; game code still never writes a
raw one. What this module adds is what pyglet does not have: ordering that
survives the frame, edges, and a held set that outlives the ring.

See `docs/bps/INPUT_STREAM.md`.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import Enum, auto
from typing import TYPE_CHECKING, Deque

from blitspersecond.common import fps
from blitspersecond.input.stream import InputStream, InputWindow

if TYPE_CHECKING:
    from blitspersecond.input.kbm.text import TextInput
    from blitspersecond.internal.events import EventBus


#: Arrivals staged in one frame before the excess is dropped and counted.
#: Bounded for the reason `MetricBuffer` is bounded: a subsystem that can
#: exhaust memory when something goes wrong is worse than one that says it is
#: going wrong. Far above any real frame's typing.
_STAGING_LIMIT = 512


class KeyEvent(Enum):
    """What one keyboard fact is."""

    PRESS = auto()
    RELEASE = auto()
    #: The OS repeating a key that is already down. A fact, never a fresh
    #: edge -- holding a key must not read as pressing it sixty times.
    REPEAT = auto()
    #: One character. A single callback may carry several, and they are split
    #: so `"name\r"` spells the name before Return completes it.
    TEXT = auto()
    #: An editing motion -- arrows, home, end, backspace. Pyglet resolves
    #: these from the platform's own key bindings, so they are not derivable
    #: from the key symbols and have to be carried separately.
    MOTION = auto()


class _Arrival(Enum):
    """Staging kinds. Private: `CLEAR` never reaches a reader as a fact."""

    PRESS = auto()
    RELEASE = auto()
    TEXT = auto()
    MOTION = auto()
    CLEAR = auto()


@dataclass(frozen=True, slots=True)
class KeyFact:
    """One keyboard fact, in the order it arrived within a frame.

    `held` carries the invariant the whole predicate layer rests on:

        A transition carries the held-set in which its own key is present.

    A press carries the set *after* its key was added; a release carries the
    set *before* its key was removed. Both therefore contain the key the fact
    is about, which is what lets a reader evaluate a chord in either
    direction -- "did adding this make the expression true", and "was it true
    while this was present, and is it not now". Publishing the after-state for
    a release instead would lose the second question.
    """

    kind: KeyEvent
    #: A pyglet key symbol, a pyglet motion constant, or one character.
    value: int | str
    modifiers: int
    held: frozenset[int]


@dataclass(frozen=True, slots=True)
class KeyScan:
    """The keyboard's state at one cut, and the edges of that interval.

    The scan survives the ring. `held` is folded forward from every event ever
    seen, so a key weighed down for an hour still reports held long after the
    press that started it cycled out of history. Do not infer held-ness from
    `KeyEvent.REPEAT`: repeat generally applies only to the most recently
    pressed key, needs focus, and can be turned off entirely.
    """

    held: frozenset[int]
    pressed: frozenset[int]
    released: frozenset[int]
    #: This interval's releases came from focus loss or a resume, not from the
    #: player letting go. The keys may well still be physically down.
    cleared: bool = False


KeyboardWindow = InputWindow[KeyScan, KeyFact]


class KeyboardIngress:
    """Subscribes to the bus, stages arrivals, settles them at the cut.

    Whoever builds this must keep it: the bus stores bound methods as weak
    references, so an ingress nothing holds is collected and silently
    unsubscribes itself.
    """

    # `__weakref__` is not optional here: the bus stores bound handlers as
    # weak references, and a slotted class without it cannot be referred to
    # weakly at all -- push_handlers raises outright.
    __slots__ = (
        "__weakref__",
        "_dropped",
        "_held",
        "_line",
        "_staging",
        "_stream",
    )

    def __init__(self, events: EventBus, rate: fps) -> None:
        self._staging: Deque[tuple[_Arrival, int | str, int]] = deque()
        self._stream: InputStream[KeyScan, KeyFact] = InputStream(rate)
        self._held: set[int] = set()
        self._dropped = 0
        # The active line, if a console is asking a question. Fed directly by
        # the cut rather than reading the ring: it is the one consumer whose
        # correctness depends on missing nothing, and it must not depend on a
        # retention depth sized for quarter-circles.
        self._line: TextInput | None = None
        events.push_handlers(
            on_key_press=self._on_key_press,
            on_key_release=self._on_key_release,
            on_text=self._on_text,
            on_text_motion=self._on_text_motion,
            on_deactivate=self._on_deactivate,
        )

    @property
    def stream(self) -> InputStream[KeyScan, KeyFact]:
        """Settled history. Readers take windows from here; the accumulating
        frame is not reachable from anywhere."""
        return self._stream

    @property
    def dropped(self) -> int:
        """Arrivals lost to the staging limit since the engine started."""
        return self._dropped

    def input(self, initial: str = "") -> "TextInput":
        """Begin a line of typing, fed from each cut until it finishes.

        One at a time. Asking for another interrupts the one before it with
        reason `REPLACED`, because a finished question is a finished thing and
        the next one needs its own line rather than a share of this one.
        """
        from blitspersecond.input.kbm.text import TextInput

        if self._line is not None:
            self._line._interrupt("REPLACED")
        self._line = TextInput(initial)
        return self._line

    # -- bus handlers: record and return, nothing else --------------------
    #
    # These run inside the window pump. They return None deliberately: a
    # truthy return is EVENT_HANDLED and would stop the event reaching
    # handlers below, including Lifecycle's and any other device's.

    def _stage(self, kind: _Arrival, value: int | str, modifiers: int) -> None:
        if len(self._staging) < _STAGING_LIMIT:
            self._staging.append((kind, value, modifiers))
        else:
            self._dropped += 1

    def _on_key_press(self, symbol: int, modifiers: int) -> None:
        self._stage(_Arrival.PRESS, symbol, modifiers)

    def _on_key_release(self, symbol: int, modifiers: int) -> None:
        self._stage(_Arrival.RELEASE, symbol, modifiers)

    def _on_text(self, text: str) -> None:
        self._stage(_Arrival.TEXT, text, 0)

    def _on_text_motion(self, motion: int) -> None:
        self._stage(_Arrival.MOTION, motion, 0)

    def _on_deactivate(self) -> None:
        # Queued rather than applied here, so it takes its place in arrival
        # order: a press that landed just before focus was lost still gets its
        # press edge in this interval, and then its release from the clear.
        self._stage(_Arrival.CLEAR, 0, 0)

    # -- the engine's boundary --------------------------------------------

    def flush(self) -> None:
        """Discard staged arrivals and release everything held.

        Called on resume. Ticks stop while an engine is suspended but the
        window keeps pumping, so staging would otherwise land as one enormous
        window describing input that was never aimed at the game.

        Dropping the events alone would leave the held set stale for good --
        anything pressed before the suspend and released during it would stay
        held until something cleared it. So this queues the same clear that
        focus loss uses. The keyboard assumes neutral because it cannot ask
        the OS what is still down; a pad can, and resyncs instead.
        """
        self._staging.clear()
        self._stage(_Arrival.CLEAR, 0, 0)

    def cut(self, frame: int) -> KeyboardWindow:
        """Settle the interval that just ended into one frozen window.

        Called once per frame by the engine, at the head of the frame, before
        the clock turns -- so `frame` is the number of the frame now closing.
        Called on EVERY frame, including frames where nothing arrived: an
        empty window is what keeps "the last fifteen frames" meaning fifteen
        frames rather than fifteen frames that happened to have input.
        """
        pressed: set[int] = set()
        released: set[int] = set()
        facts: list[KeyFact] = []
        cleared = False

        while self._staging:
            kind, value, modifiers = self._staging.popleft()

            if kind is _Arrival.PRESS:
                assert isinstance(value, int)
                if value in self._held:
                    facts.append(
                        KeyFact(
                            KeyEvent.REPEAT,
                            value,
                            modifiers,
                            frozenset(self._held),
                        )
                    )
                else:
                    self._held.add(value)
                    pressed.add(value)
                    # After adding: the set contains the key pressed.
                    facts.append(
                        KeyFact(
                            KeyEvent.PRESS,
                            value,
                            modifiers,
                            frozenset(self._held),
                        )
                    )

            elif kind is _Arrival.RELEASE:
                assert isinstance(value, int)
                # Before removing: the set contains the key released.
                before = frozenset(self._held)
                if value in self._held:
                    self._held.remove(value)
                    released.add(value)
                facts.append(
                    KeyFact(KeyEvent.RELEASE, value, modifiers, before)
                )

            elif kind is _Arrival.TEXT:
                assert isinstance(value, str)
                held = frozenset(self._held)
                for character in value:
                    facts.append(KeyFact(KeyEvent.TEXT, character, 0, held))

            elif kind is _Arrival.MOTION:
                assert isinstance(value, int)
                facts.append(
                    KeyFact(
                        KeyEvent.MOTION, value, 0, frozenset(self._held)
                    )
                )

            else:
                # CLEAR. Unwind one key at a time so each release carries the
                # set as it stood at that moment: a chord recogniser watching
                # the unwind sees a coherent sequence rather than everything
                # vanishing at once.
                cleared = True
                before = set(self._held)
                for symbol in sorted(self._held):
                    facts.append(
                        KeyFact(
                            KeyEvent.RELEASE, symbol, 0, frozenset(before)
                        )
                    )
                    before.remove(symbol)
                    released.add(symbol)
                self._held.clear()

        window: KeyboardWindow = InputWindow(
            frame=frame,
            scan=KeyScan(
                held=frozenset(self._held),
                pressed=frozenset(pressed),
                released=frozenset(released),
                cleared=cleared,
            ),
            facts=tuple(facts),
        )
        self._stream.append(window)
        # The active line is handed its events here, as they are frozen, and
        # before the game is given control -- so a tick reading `value` or
        # `completed` sees a settled answer rather than one still filling in.
        line = self._line
        if line is not None:
            line._consume(window)
            if not line.listening:
                self._line = None
        return window
