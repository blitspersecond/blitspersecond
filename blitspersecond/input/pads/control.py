"""What a pad reports besides buttons: sticks, triggers, and the d-pad.

Named for the control, never for how a transport happens to carry it. That is
the same rule `Button` follows one level down -- FACE_SOUTH rather than 'A' --
and it matters more here, because the encodings disagree more.

See `docs/bps/PADS_PORT.md`.
"""

from __future__ import annotations

from enum import Enum, IntEnum, auto


class Axis(Enum):
    """The continuous controls, normalised and carried as they are.

    **The signal is carried, not reduced.** A trigger is reported as travel
    even though `Button.LT` and `Button.RT` also exist for it: the same
    control has two honest readings, and which one a game wants is the game's
    business. Kris, 2026-09-11: *"carry the signal. my hori has them and they
    are digital"* -- a fightpad that wires a face button to a trigger axis
    reports 0 or full, so the threshold gives it clean edges, while a
    genuinely analogue trigger gives travel as well.

    **A closed vocabulary is not a promise every pad has all of it, and the
    pad will not tell you which it has.** A Hori Fighting Commander has one
    stick, and it is digital -- and measured on 2026-09-11 it declares all six
    axes over exactly the ranges a real Xbox pad declares, sticks at
    -32768..32767 and triggers at 0..1023. `EVIOCGABS` does not report the
    absence, because the pad does not admit to it: presenting as an Xbox
    controller is what makes it work everywhere, and the claim covers
    capabilities as much as identity.

    So a control that is not there reads as permanently neutral, and nothing
    can distinguish that from one at rest. **This is the same law the pointer
    already states** -- *"window events cannot reveal a button's absence, a
    mouse only proves buttons by pressing them"* -- and a pad only proves an
    axis by moving it.

    Which makes REQUIRE-NEVER design law rather than a runtime check: a game
    may read an axis and must never depend on one, because there is no test
    it could perform first.

    `Button` does not have the problem and the asymmetry is real: the ten
    buttons ARE the converged form, and a pad missing one would not be a
    gamepad. The continuous controls are where hardware varies, and where it
    also lies.

    Sticks are two axes each rather than one vector, because that is what the
    hardware reports. A stick as a deadzoned (x, y) pair is a DERIVED reading:
    the deadzone is radial, which is a judgement about intent applied to the
    two together, and a scan that stored the result would have thrown away
    what the hardware actually said.
    """

    LEFT_X = auto()
    LEFT_Y = auto()
    RIGHT_X = auto()
    RIGHT_Y = auto()
    #: Travel, 0..1. The edge view of the same control is `Button.LT`/`RT`.
    LEFT_TRIGGER = auto()
    RIGHT_TRIGGER = auto()


class Direction(IntEnum):
    """The d-pad, as a numpad position. Neutral is 5.

    **A d-pad is a d-pad.** It reaches the engine three different ways -- two
    evdev axes reading -1/0/1, four GameInput button bits, or a single
    GameInput switch position on the hardware that needs that quirk -- and
    none of those is the control. "Hat" is a wire format. Eight directions and
    a centre is the thing in your hand.

    **Numpad notation is not an arbitrary encoding; it is the one the subject
    already uses.** A quarter-circle forward is 236 in every move list ever
    printed, so a roll in the fact stream reads as 2, 3, 6 and a recogniser
    looks like the move it implements. For a ring whose stated purpose is
    reconstructing exactly that, nothing else comes close.

    An `IntEnum` deliberately, where `Button` deliberately is not: there the
    integer is a transport's private numbering that must never leak, and here
    the integer IS the vocabulary.
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
