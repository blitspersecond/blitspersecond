"""Native gamepads, snapshotted onto the engine's tick grid.

`Pads` is the BUS -- it coordinates the internal platform transport and
ENUMERATES `Pad` objects, one per gamepad that is plugged in::

    for pad in bps.pads:
        if pad.pressed(Button.FACE_SOUTH):
            jump(pad)

A `Pad` owns everything about itself: its identity (`fingerprint`), its `name`,
its mapper, and its per-tick state. `Pads` is an unbounded discovery list of
whatever is plugged in. First deliberate activity assigns a stable, one-based
Pad id. On disconnect the Pad becomes an inert permanent proxy; the same
fingerprint revives that exact object on reconnect. Identity is independent of
any explicit `input.ports` mapping. `bps.pads.gamepad` is the lowest-id
connected Pad; the engine's `bps.gamepad` checks routed Ports first and falls
back to it.

`Button` is the normalised vocabulary: physical positions, so FACE_SOUTH is the
bottom button on every pad ever made. Conditions compose with ``|``;
``bps.pads.identify().until(Button.START | Button.ACCEPT)`` completes when the
first Pad makes a matching press. Bare ``identify()`` accepts any deliberate
activity. ACCEPT is the pad family's conventional confirm position.

NAMED MAPPERS put a name and a face to each pad -- `PadMapper` and its family
(XboxMapper, SonyMapper, DualSenseMapper, NintendoMapper) are selected per
device by vendor:product at discovery. A SELECTOR, never a gate:
an unrecognised pad falls through to XboxMapper and works, because the 360 is
where pads converge. They give a pad an editorial name (`pad.name`) and the
label printed on its plastic (`pad.label` -- 'A', 'cross', 'B' for the same
FACE_SOUTH), so a HUD can prompt in the player's own vocabulary. See
docs/CONTROLLERS.md.
"""

from .codes import Button
from .identification import Identification
from .mappers import (
    DualSenseMapper,
    NintendoMapper,
    PadMapper,
    SonyMapper,
    XboxMapper,
)
from .pad import Pad
from .pads import Pads

__all__ = [
    "Button",
    "DualSenseMapper",
    "Identification",
    "NintendoMapper",
    "Pad",
    "Pads",
    "PadMapper",
    "SonyMapper",
    "XboxMapper",
]
