"""The rates a game may be authored at.

The tick rate is the AUTHOR's choice, not the engine's. A game declares one at
construction and the engine holds it there absolutely -- it never bends to
suit a display, because the tick rate is what every tuned constant in the game
means. Jump height, gravity, coyote time, animation timing and input windows
are all counted in frames, so varying the rate silently redefines all of them
at once. That is what PAL ports did, and it is why they are a separate
speedrun category.

A closed set rather than an int, for two reasons:

* **It is a real choice with real consequences**, not a dial. A lower rate
  means fewer simulation steps per second, and correspondingly coarser motion
  and input granularity. Those are consequences to author around, not defects,
  and the engine has no standing to have an opinion about which of them a game
  wants. But an author reaching for 37 has misunderstood what this is, and a
  closed set says so at the call site.
There USED to be a second reason, and it was the stronger one: a tick-to-
present ratio whose exact denominator is ``panel / gcd(rate, panel)`` grows
without bound as panel rates climb, so a closed set was what kept the matrix
enumerable against real hardware. That argument died with the presentation
cadence -- nothing divides ticks into presentation slots any more, and the
clock would hold 37fps as readily as 60. The set is now an authoring
judgement rather than a technical constraint, which is a weaker footing and
should be recognised as such rather than defended with the old reason. See
`docs/bps/DECOUPLED_PRESENTATION.md`, and the board card "Let the game author
declare its tick rate", whose denominator analysis this retires.

15, 20, 30 and 60 are the classic divisions of 60 -- one tick per 4, 3, 2 and
1 slots on a 60Hz panel -- and predate all of this by decades.

40 is the odd one out and the recent addition. 60/40 is 3/2, so it paces
unevenly on a 60Hz panel (gaps of 1,2,1,2) and only became viable when 120Hz
displays made it exact: 120/40 is a uniform three slots per tick. PS5
popularised 40 on that basis, which is where players will have met it. It is
not the source of the set.

``IntEnum`` deliberately: every consumer -- `Clock` deriving its timestep,
`Metrics` folding frames to seconds -- treats this as a number and needs no
conversion.
"""

from enum import IntEnum


class fps(IntEnum):
    """A rate a game may be authored at. See module docstring."""

    FIFTEEN = 15
    TWENTY = 20
    THIRTY = 30
    FORTY = 40
    SIXTY = 60
