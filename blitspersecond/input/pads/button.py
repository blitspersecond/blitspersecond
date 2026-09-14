"""The button vocabulary: what a gamepad has, settled.

Positions and jobs, never letters. `FACE_SOUTH` is the bottom button on every
pad ever made -- Xbox prints an A on it, Sony a cross, Nintendo a B -- and a
game maps it to its own action, because a pad does not know 'punch'.

Design record: `bps/reference/input/pads.md`.
"""

from __future__ import annotations

from enum import Enum, auto
from typing import TypeAlias

from blitspersecond.input.condition import Composable, Condition, Unresolved


class Button(Composable, Enum):
    """Every button the engine reports, and there will never be another.

    **CLOSED, and closing it is what buys the rest**: no `UNKNOWN` member, no
    escape hatch, and a `match` over it that is total and stays total. A
    control outside these ten is dropped where it arrives rather than carried
    as a value nothing can name.

    **Do not add a member.** Two tests answer a new button without reopening
    anything, and they have already refused a touchpad and a Share button.
    What they are, and why ten is the converged form rather than a subset:
    `bps/reference/input/pads.md`, *The vocabulary is closed*.

    The d-pad is deliberately absent: it arrives as an axis pair and is read
    as a direction, which is what it physically is.
    """

    # Named for POSITION, because position is the only thing that held. The
    # letters move between vendors and the function moves between games --
    # FACE_SOUTH is jump in one game and confirm in the next.
    FACE_SOUTH = auto()
    FACE_EAST = auto()
    FACE_WEST = auto()
    FACE_NORTH = auto()

    LB = auto()
    RB = auto()
    #: Triggers, thresholded into button edges. Many fightpads wire a face
    #: button to a trigger axis and report it digitally, so one threshold
    #: gives clean edges; a genuinely analogue trigger becomes a slightly
    #: light button.
    LT = auto()
    RT = auto()

    # Named for FUNCTION, the one place the vocabulary does that, because for
    # these two BOTH position and function held. The pair arrives with the
    # Famicom -- older than the face diamond -- and has been renamed every
    # generation since: Start/Select, Start/Back, Menu/View, Options/Share
    # then Create, plus/minus. Through all of it the Select-equivalent has
    # been left of centre and the Start-equivalent right of centre, and
    # neither has ever meant anything but start-or-pause and the other thing.
    # When two invariants are available the vocabulary takes the legible one:
    # `CENTRE_RIGHT` would be equally true and tell a reader nothing.
    START = auto()
    SELECT = auto()

    # NOT HERE, each refused for a stated reason rather than overlooked, and
    # each argued in full in `bps/reference/input/pads.md`, under
    # *What is not there*:
    #
    #   L3/R3    accidental when unwanted, unreliable when wanted, and an
    #            accessibility position -- a game cannot bind what the engine
    #            cannot name, which is the point
    #   Guide    already the player's: it turns their pad off
    #   the rest touchpads, motion, mute, paddles -- after the convergence,
    #            one vendor at a time, adopted by nobody else


ButtonCondition: TypeAlias = "Button | Condition"

#: "Whatever this pad calls confirm." Not an eleventh button -- a term the
#: pad resolves when there is finally a pad to resolve it against, so
#: `Button.START | Button.ACCEPT` reads as one phrase and still means the
#: right thumb on every family.
#:
#: It is not in the enum because it is not a position. Every other name in
#: this vocabulary says where a thumb goes; this one says what a FAMILY means
#: by yes, and those two are different kinds of fact.
Button.ACCEPT = Unresolved("Button.ACCEPT")
