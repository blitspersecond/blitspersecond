"""One pad's settled interval: what it was doing, and what it did.

The same two halves every device in this engine produces -- a scan of state,
and ordered facts -- so a reader who has understood the keyboard or the
pointer has understood this.

Design record: `bps/reference/input/pads.md`, *A pad's settled interval*.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from types import MappingProxyType
from typing import Mapping, NamedTuple

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
#: does not admit which controls it lacks -- see `Axis` -- so leaving an absent
#: one out would only move the problem to a `KeyError`.
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

    `held` carries the invariant the keyboard states and the pointer repeats,
    and it holds without exception:

        A transition carries the held-set in which its own button is present.
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

    @property
    def control(self) -> Button | None:
        """What this fact is about, under the name every device uses.

        The query verbs walk facts from any device with one piece of code, so
        they need one spelling for the button a transition concerns.
        """
        return self.button


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


class Reading(NamedTuple):
    """One observed full-state packet from a snapshot-shaped transport.

    GameInput readings are device packets, but each carries a complete
    gamepad state. Keeping this type distinct from `Snapshot` preserves the
    important truth at the cut: walking packets is observed history, while a
    snapshot is state asked for after history was unavailable.
    """

    buttons: frozenset[int]
    axes: dict[int, int]


#: Travel past which a trigger reads as a button. XInput's own figure.
#:
#: No hysteresis: many fightpads report a trigger DIGITALLY so it needs none,
#: and an analogue trigger held exactly on the line has not been seen to
#: chatter. Revisit if one does. See `bps/reference/input/pads.md`,
#: *The continuous controls*.
TRIGGER_ON = 30 / 255


class PadIngress:
    """One pad's reports, folded into settled windows.

    **Folding happens per REPORT rather than per event**, which is what the
    transport's simultaneity groups are for: a diagonal is two axis changes
    the kernel says happened together, so it becomes ONE `DIRECTION` fact
    rather than an intermediate position the player never passed through.

    No deadzone is applied -- that is a judgement about intent and belongs to
    whatever reads this. See `bps/reference/input/pads.md`.
    """

    __slots__ = (
        "_axes",
        "_direction",
        "_desyncs",
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
        self._desyncs = 0

    @property
    def stream(self) -> InputStream[PadScan, PadFact]:
        """Settled history. The accumulating frame is not reachable."""
        return self._stream

    @property
    def desyncs(self) -> int:
        """Times the kernel dropped events on this pad in the interval cut.

        This connection's, and this frame's. A reconnected pad reads from a
        fresh reader with a fresh tally, which is correct -- it is a new
        connection rather than a continuation.
        """
        return self._desyncs

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
        # Asked before and after the drain, so what is counted is the kernel
        # dropping events -- not a resync the engine asked for on resume.
        dropped_before = self._reader.drops

        if self._resyncing:
            self._resyncing = False
            resynced = True
            self._replace(self._reader.snapshot(), pressed, released, facts)

        for item in self._reader.read():
            if isinstance(item, (Snapshot, Reading)):
                resynced = resynced or isinstance(item, Snapshot)
                self._replace(
                    item,
                    pressed,
                    released,
                    facts,
                    changed_only=isinstance(item, Reading),
                )
            else:
                self._apply(item, pressed, released, facts)

        self._desyncs = self._reader.drops - dropped_before
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

    def _axis(
        self,
        code: int,
        raw: int,
        pressed,
        released,
        facts,
        *,
        changed_only: bool = False,
    ) -> None:
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
        if changed_only and self._axes[name] == value:
            return
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

    def _replace(
        self,
        snapshot,
        pressed,
        released,
        facts,
        *,
        changed_only: bool = False,
    ) -> None:
        """Apply what the pad says is true, discarding what we thought.

        A snapshot REPLACES rather than adds: a button absent from it is up,
        including one we believed was held. The differences still become
        facts; `PadScan.resynced` says they were not observed.
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
                self._axis(
                    code,
                    raw,
                    pressed,
                    released,
                    facts,
                    changed_only=changed_only,
                )
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
        the empty end state, so a recogniser sees a coherent sequence. The
        keyboard's focus-loss clear does exactly this.
        """
        for button in sorted(self._held, key=lambda b: b.name):
            self._edge(button, False, set(), released, facts)
