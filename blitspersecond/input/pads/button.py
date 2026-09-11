"""The button vocabulary: what a gamepad has, settled.

Positions and jobs, never letters. `FACE_SOUTH` is the bottom button on every
pad ever made -- Xbox prints an A on it, Sony a cross, Nintendo a B -- and a
game maps it to its own action, because a pad does not know 'punch'.

See `docs/bps/PADS_PORT.md` for why this is closed and how a new button is
answered without opening it.
"""

from __future__ import annotations

from enum import Enum, auto


class Button(Enum):
    """Every button the engine reports, and there will never be another.

    **This is not a subset of what pads offer; it is the shape they converged
    on.** The 360 is where the form stopped moving -- d-pad, four face
    buttons, bumpers, triggers, two sticks, and the centre pair that has been
    there since the Famicom. The SNES contributed the face diamond and the
    shoulders, the Sega line the analogue stick and analogue triggers, Sony the
    second stick. Everything since has accepted that shape while varying the
    layout, so anything a later pad adds is one vendor experimenting after the
    convergence, not part of the form.

    So the enum is closed, and closing it is what buys the rest: no `UNKNOWN`
    member, no escape hatch, and a `match` over it that is total and stays
    total. A control outside these ten has no target here, which is why it is
    dropped where it arrives rather than carried as a value nothing can name.
    **A game cannot bind what it cannot name, and that is the intended end of
    the sentence.**

    Two tests answer a new button without reopening anything, and they have
    already been applied twice -- Sony's touchpad in 2013 and Microsoft's
    Share button in 2020 are both ignored. Is it on the Xbox One controller
    and the DualShock 4, the two pads that witness the settled form? And did
    any other vendor adopt it? If it sits where an existing control sits, it
    IS that control under a new name and it maps. Otherwise it is dropped.

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

    # NOT HERE, and each for a stated reason rather than an oversight:
    #
    # L3/R3. The stick clicks are the one input class only ever triggered at
    # inopportune moments and never by player agency -- you push the stick you
    # are already steering with, and it clicks. Refused at the vocabulary, the
    # same way the pointer refuses wheel tilt: an input bound onto a control
    # that already has a job.
    #
    # Touchpads, motion, mute, Share-below-the-guide, paddles. Added after the
    # convergence, one vendor at a time, adopted by nobody else.
    #
    # Guide / PS / Home. This one is not about the fixed point at all -- every
    # vendor has it, so it passes both tests above. It is refused because it
    # is already the player's: it turns their pad off and controls their
    # media, and a game binding it does not gain a control, it takes one away.
