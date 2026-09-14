"""One pad's settled interval: what it was doing, and what it did.

The same two halves every device in this engine produces -- a scan of state,
and ordered facts -- so a reader who has understood the keyboard or the
pointer has understood this. See `docs/bps/INPUT_STREAM.md`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from types import MappingProxyType
from typing import Mapping

from blitspersecond.common import fps
from blitspersecond.input.stream import InputStream, InputWindow

from .button import Button
from .control import Axis, Direction, direction
from .evdev import (
    ABS_HAT0X,
    ABS_HAT0Y,
    AXIS_NAMES,
    BUTTONS,
    EV_ABS,
    EV_KEY,
    TRIGGERS,
    Snapshot,
)

#: Every axis, always present, neutral when the pad has nothing to say. A pad
#: does not admit which controls it lacks -- see `Axis` -- so an absent one is
#: indistinguishable from one at rest, and pretending otherwise by leaving it
#: out would only move the problem to a `KeyError`.
NEUTRAL_AXES: Mapping[Axis, float] = MappingProxyType({axis: 0.0 for axis in Axis})


class PadEvent(Enum):
    """What one pad fact is."""

    PRESS = auto()
    RELEASE = auto()
    #: A continuous control moved. Carries which, and what it moved TO.
    AXIS = auto()
    #: The d-pad moved. One fact per position, not one per component -- a
    #: diagonal is a single change because the transport says the two halves
    #: of it happened together.
    DIRECTION = auto()


@dataclass(frozen=True, slots=True)
class PadFact:
    """One pad fact, in the order it arrived within a frame.

    `held` carries the invariant the keyboard states and the pointer repeats:

        A transition carries the held-set in which its own button is present.

    A press carries the set after its button was added; a release carries the
    set before its button was removed.
    """

    kind: PadEvent
    #: PRESS and RELEASE only.
    button: Button | None
    #: AXIS only, with `value` as the position it moved to, normalised.
    axis: Axis | None
    value: float
    #: DIRECTION only.
    direction: Direction | None
    held: frozenset[Button]


@dataclass(frozen=True, slots=True)
class PadScan:
    """The pad's state at one cut, and the edges of that interval."""

    held: frozenset[Button]
    pressed: frozenset[Button]
    released: frozenset[Button]
    #: Where every continuous control settled. Sticks are -1..1 with neutral
    #: at 0; triggers are 0..1 travel. Raw in the sense that matters: no
    #: deadzone is applied, because a deadzone is a judgement about intent and
    #: belongs to whatever reads this, not to the record of what happened.
    axes: Mapping[Axis, float]
    #: Where the d-pad settled. `Direction.NEUTRAL` when it is not held.
    direction: Direction
    #: This interval's edges did not all come from the player. The transport
    #: dropped events and the pad was asked what was true instead, so a
    #: release here may be a button nobody let go of -- it was simply not
    #: down when we looked. The keyboard's `cleared` says the same thing for
    #: the same reason, and the two should read alike.
    resynced: bool = False
    #: The pad went away during this interval. Its held buttons were released
    #: as real edges so a drag or a charge cannot get stuck, and nothing more
    #: will arrive.
    disconnected: bool = False


PadWindow = InputWindow[PadScan, PadFact]


#: Travel past which a trigger reads as a button. XInput's own figure.
#:
#: Triggers arrive as an axis and rest at zero. Many fightpads -- the Hori
#: among them -- wire a face button to a trigger and report it DIGITALLY, 0 or
#: full, so one threshold turns the axis into clean edges; a genuinely
#: analogue trigger becomes a slightly light button. No hysteresis: digital
#: does not need it, and an analogue trigger held exactly on the line has not
#: been seen to chatter. Revisit if one does.
TRIGGER_ON = 30 / 255


class PadIngress:
    """One pad's reports, folded into settled windows.

    Everything the transport says arrives as `Report`s -- the kernel's own
    simultaneity groups -- and this turns them into the two halves a window
    has. **Folding happens per report rather than per event**, which is what
    the report boundary is for: a diagonal is two axis changes the transport
    is telling us happened together, so it becomes one `DIRECTION` fact rather
    than a spurious intermediate position.

    No deadzone is applied. The scan records what the pad reported; a deadzone
    is a judgement about intent and belongs to whatever reads it.
    """

    __slots__ = (
        "_axes",
        "_direction",
        "_disconnecting",
        "_hat",
        "_held",
        "_resyncing",
        "_reader",
        "_stream",
    )

    def __init__(self, reader, rate: fps) -> None:
        self._reader = reader
        self._stream: InputStream[PadScan, PadFact] = InputStream(rate)
        self._held: set[Button] = set()
        self._axes: dict[Axis, float] = dict(NEUTRAL_AXES)
        self._hat: tuple[int, int] = (0, 0)
        self._direction = Direction.NEUTRAL
        self._disconnecting = False
        self._resyncing = False

    @property
    def stream(self) -> InputStream[PadScan, PadFact]:
        """Settled history. The accumulating frame is not reachable."""
        return self._stream

    def retire(self, reader) -> None:
        """Read from somewhere else from now on.

        Used when a pad goes away and when the same one comes back: the ring
        and the held-set stay with the ingress, and only the source under it
        changes.
        """
        self._reader = reader

    def resync(self) -> None:
        """Replace state with what the device says, at the next cut.

        The pad is asked rather than assumed, which is the whole difference
        between this device and the keyboard on a resume: a player holding a
        direction through a pause still holds it.
        """
        self._resyncing = True

    def disconnect(self) -> None:
        """The pad went away. Release what it was holding, as real edges.

        A dead battery mid-hold must read as a release rather than a stuck
        button, and it must be readable on the frame it happened -- so the
        edges land in the next window and `disconnected` says why they are
        there.
        """
        self._disconnecting = True

    def cut(self, frame: int) -> PadWindow:
        """Settle the interval that just ended into one frozen window."""
        pressed: set[Button] = set()
        released: set[Button] = set()
        facts: list[PadFact] = []
        resynced = False

        if self._resyncing:
            self._resyncing = False
            resynced = True
            self._replace(self._reader.snapshot(), pressed, released, facts)

        for item in self._reader.read():
            if isinstance(item, Snapshot):
                resynced = True
                self._replace(item, pressed, released, facts)
            else:
                self._apply(item, pressed, released, facts)

        disconnected = self._disconnecting
        if disconnected:
            self._disconnecting = False
            self._release_all(released, facts)
            self._axes = dict(NEUTRAL_AXES)
            self._hat, self._direction = (0, 0), Direction.NEUTRAL

        window: PadWindow = InputWindow(
            frame=frame,
            scan=PadScan(
                held=frozenset(self._held),
                pressed=frozenset(pressed),
                released=frozenset(released),
                axes=MappingProxyType(dict(self._axes)),
                direction=self._direction,
                resynced=resynced,
                disconnected=disconnected,
            ),
            facts=tuple(facts),
        )
        self._stream.append(window)
        return window

    # -- folding ----------------------------------------------------------

    def _apply(self, report, pressed, released, facts) -> None:
        """One simultaneity group. Buttons and axes in order, direction once."""
        hat = self._hat
        for kind, code, value in report.events:
            if kind == EV_KEY:
                button = self._reader.layout.buttons.get(code)
                # value 2 is autorepeat: a fact about the OS, never an edge.
                if button is not None and value in (0, 1):
                    self._edge(button, value == 1, pressed, released, facts)
            elif kind == EV_ABS:
                if code == ABS_HAT0X:
                    hat = (value, hat[1])
                elif code == ABS_HAT0Y:
                    hat = (hat[0], value)
                else:
                    self._axis(code, value, pressed, released, facts)

        if hat != self._hat:
            # Emitted after the report's other facts and exactly once, because
            # the two halves of a diagonal are simultaneous rather than
            # sequential. Emitting per component would invent a position the
            # player never passed through.
            self._hat = hat
            self._direction = direction(*hat)
            facts.append(
                PadFact(
                    PadEvent.DIRECTION,
                    None,
                    None,
                    0.0,
                    self._direction,
                    frozenset(self._held),
                )
            )

    def _axis(self, code: int, raw: int, pressed, released, facts) -> None:
        name = self._reader.layout.axes.get(code)
        if name is None:
            return  # a control outside the vocabulary; dropped here
        span = self._reader.ranges.get(code)
        trigger = self._reader.layout.triggers.get(code)
        if span is None:
            # No calibration, so no idea what the number means. Reporting it
            # raw would be worse than not reporting it.
            return
        value = span.travel(raw) if trigger else span.centred(raw)
        self._axes[name] = value
        facts.append(
            PadFact(PadEvent.AXIS, None, name, value, None, frozenset(self._held))
        )
        if trigger is not None:
            self._edge(
                trigger, value > TRIGGER_ON, pressed, released, facts
            )

    def _edge(self, button, down, pressed, released, facts) -> None:
        if down:
            if button in self._held:
                return  # already down: not a fresh edge
            self._held.add(button)
            pressed.add(button)
            # After adding: the set contains the button pressed.
            facts.append(
                PadFact(
                    PadEvent.PRESS, button, None, 0.0, None, frozenset(self._held)
                )
            )
        else:
            if button not in self._held:
                return
            # Before removing: the set contains the button released.
            before = frozenset(self._held)
            self._held.discard(button)
            released.add(button)
            facts.append(
                PadFact(PadEvent.RELEASE, button, None, 0.0, None, before)
            )

    def _replace(self, snapshot, pressed, released, facts) -> None:
        """Apply what the pad says is true, discarding what we thought.

        A snapshot REPLACES rather than adds: a button absent from it is up,
        including one we believed was held. That is the whole point -- a
        release lost in a dropped run leaves a button held for good, and only
        a wholesale replacement heals it.

        The differences still become facts, so a reader watching edges sees
        them. `PadScan.resynced` is what says they were not observed.
        """
        now = {BUTTONS[code] for code in snapshot.buttons if code in BUTTONS}
        for button in sorted(self._held - now, key=lambda b: b.name):
            self._edge(button, False, pressed, released, facts)
        for button in sorted(now - self._held, key=lambda b: b.name):
            self._edge(button, True, pressed, released, facts)

        hat = self._hat
        for code, raw in snapshot.axes.items():
            if code == ABS_HAT0X:
                hat = (raw, hat[1])
            elif code == ABS_HAT0Y:
                hat = (hat[0], raw)
            else:
                self._axis(code, raw, pressed, released, facts)
        if hat != self._hat:
            self._hat = hat
            self._direction = direction(*hat)
            facts.append(
                PadFact(
                    PadEvent.DIRECTION,
                    None,
                    None,
                    0.0,
                    self._direction,
                    frozenset(self._held),
                )
            )

    def _release_all(self, released, facts) -> None:
        """Unwind the held-set one button at a time.

        Each release carries the set as it stood at that moment rather than
        the empty end state, so a recogniser watching the unwind sees a
        coherent sequence instead of everything vanishing at once. The
        keyboard's focus-loss clear does exactly this.
        """
        for button in sorted(self._held, key=lambda b: b.name):
            self._edge(button, False, set(), released, facts)
