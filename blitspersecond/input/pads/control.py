"""What a pad reports besides buttons: sticks, triggers, and the d-pad.

Named for the control, never for how a transport happens to carry it. That is
the same rule `Button` follows one level down -- FACE_SOUTH rather than 'A' --
and it matters more here, because the encodings disagree more.

Design record: `bps/reference/input/pads.md`.
"""

from __future__ import annotations

from blitspersecond.input.condition import Composable
from enum import Enum, IntEnum, auto


class Axis(Enum):
    """The continuous controls, normalised and carried as they are.

    **The signal is carried, not reduced.** A trigger is reported as travel
    even though `Button.LT`/`RT` exist for it too: the same control has two
    honest readings and which one a game wants is the game's business.

    **A closed vocabulary is NOT a promise every pad has all of it, and the
    pad will not tell you which it has** -- a control that is not there reads
    as permanently neutral, indistinguishable from one at rest. So a game may
    READ an axis and must never DEPEND on one; there is no test it could
    perform first. Why the hardware lies about this, and why `Button` has no
    such problem: `bps/reference/input/pads.md`, *A pad will not admit
    which controls it lacks*.

    Sticks are two axes each rather than one vector, because that is what the
    hardware reports; a deadzoned pair is a DERIVED reading.
    """

    LEFT_X = auto()
    LEFT_Y = auto()
    RIGHT_X = auto()
    RIGHT_Y = auto()
    #: Travel, 0..1. The edge view of the same control is `Button.LT`/`RT`.
    LEFT_TRIGGER = auto()
    RIGHT_TRIGGER = auto()


class Direction(Composable, IntEnum):
    """The d-pad, as a numpad position. Neutral is 5.

    **A d-pad is a d-pad.** It reaches the engine three different ways and
    none of them is the control; "hat" is a wire format, eight directions and
    a centre is the thing in your hand.

    Numpad notation because it is the notation the subject already uses: a
    quarter-circle forward is 236 in every move list ever printed.
    See `bps/reference/input/pads.md`.

    An `IntEnum` where `Button` deliberately is not -- there the integer is a
    transport's private numbering that must never leak, here it IS the
    vocabulary: `Direction.DOWN_RIGHT == 3` is the notation a move list is
    printed in, and 236 should mean what it means on paper.

    `Composable` comes first so its operators SHADOW int's. Without it
    `DOWN | DOWN_RIGHT` is arithmetic and evaluates to `DOWN_RIGHT` -- a
    real member, so nothing downstream could tell. See `condition.py`.
    """

    DOWN_LEFT = 1
    DOWN = 2
    DOWN_RIGHT = 3
    LEFT = 4
    NEUTRAL = 5
    RIGHT = 6
    UP_LEFT = 7
    UP = 8
    UP_RIGHT = 9


#: (x, y) -> Direction, with y growing DOWN as everything in this engine does.
_DIRECTIONS = {
    (-1, -1): Direction.UP_LEFT,
    (0, -1): Direction.UP,
    (1, -1): Direction.UP_RIGHT,
    (-1, 0): Direction.LEFT,
    (0, 0): Direction.NEUTRAL,
    (1, 0): Direction.RIGHT,
    (-1, 1): Direction.DOWN_LEFT,
    (0, 1): Direction.DOWN,
    (1, 1): Direction.DOWN_RIGHT,
}


def direction(x: int, y: int) -> Direction:
    """A d-pad position from two signs, y growing down.

    Takes signs rather than raw values so every transport can reach it: evdev
    passes its -1/0/1 axes straight through, a GameInput fixed-format reading
    passes the difference of its two button bits, and a switch position is a
    table lookup away from the same pair.
    """
    return _DIRECTIONS[(max(-1, min(1, x)), max(-1, min(1, y)))]
