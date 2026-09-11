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

from blitspersecond.input.stream import InputWindow

from .button import Button
from .control import Axis, Direction

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
