"""The input toolbox: what the player is doing, one import away.

    from blitspersecond.input import kbm, key, keyboard, mouse

kbm is the one routable local keyboard-and-mouse device. Pyglet's focused
window callbacks have already combined every physical keyboard into one
keyboard stream and every physical pointer into one mouse stream, so the two
facets move together at the Port layer. They keep their own honest APIs as
kbm.keyboard and kbm.mouse, and remain directly importable as keyboard and
mouse.

keyboard is the keyboard made trustworthy: the engine's private handler
snapshots raw key events at each tick boundary, so a
game reads one coherent keyboard per tick -- held(), pressed()/released()
edges and composed key alternatives/chords, plus the ordered text/editing
event stream consumed by the one active Input created through
keyboard.input(). It is the keyboard facet of the permanent ``kbm`` device.
The direct import is always available; bps.keyboard proxies the facet currently
mapped somewhere in bps.ports. Ports enforces that mapping invariant and raises
at the source if KBM was explicitly unbound.

mouse is the pointer under the same law: buffered raw
window events, snapshotted at the tick boundary, answered in INTERNAL
640x360 pixels (the inverse of the present transform lives in the handler;
game code never sees window coordinates). Position, per-tick delta and
scroll, button edges in a vocabulary of MEANING:
mouse.PRIMARY/mouse.SECONDARY/mouse.MIDDLE
(aliases of pyglet's left/right values -- the OS applies the left-handed
swap below us, so the primary button is whichever the user says it is). It is
the pointer facet of ``kbm``, so it routes with the keyboard without flattening
their controls. The direct import is always available; bps.mouse proxies the
mapped facet. Ports enforces the same mapping invariant.

Pads (input.pads) applies the same buffer + tick-snapshot rule to native
gamepads: raw evdev on Linux and GameInput on Windows. It follows the MIDI
model, not the keyboard's: each platform transport supplies complete state at
the tick boundary. It is the BUS, and it ENUMERATES Pad objects --
`for pad in bps.pads` -- one per connected gamepad. Each Pad owns its own
physical identity, name, mapper and per-tick state; Button is the vocabulary
they speak.
It is an unbounded connected-device list with its own stable, one-based
identity. First deliberate activity assigns `pad.id`; disconnect leaves an
inert permanent proxy, and a same-fingerprint reconnect revives that object.
`bps.pads.gamepad` returns the connected identified Pad with the lowest ID.
Reach the full collection as bps.pads.

Ports (input.ports) is the engine-owned set of four stable virtual input
stations, reached as bps.ports. Each Port owns explicit InputDeviceMappings;
Pad identity never determines P1-P4: games explicitly connect, disconnect, or
move devices. `bps.ports.gamepad` returns the first connected Pad mapped across
P1-P4. The local KeyboardMouse begins mapped to P1 when the lazy collection is
first accessed. `bps.gamepad` prefers that routed Pad, then falls back to
`bps.pads.gamepad`.

NAMED MAPPERS (input.pads.mappers) give each pad a name and a face. Selected
per device by vendor:product at discovery, they are a SELECTOR and never a
gate: an unrecognised pad falls through to XboxMapper and works fine, because
the 360 is where pads converge. They turn a transport's unhelpful string
("Generic X-Box pad") into a name a person uses, and answer what a pad PRINTS
on a button -- pad.label(Button.FACE_SOUTH) is 'A', 'cross' or 'B' depending on
whose plastic it is -- so a HUD prompts in the player's own vocabulary.
`Button.ACCEPT` is the mapper's conventional family default for title/join
screens; a person or game may still choose a different physical position.

Player identity is deliberately absent. Nicknames, scores, saves, preferences,
lobbies, and hot-seat turns may bind to Ports above this layer, but none of
them participate in physical discovery or reconnect.
"""

from .kbm import (
    COMPLETED,
    DISCARDED,
    INTERRUPTED,
    LISTENING,
    InputState,
    KeyboardEvent,
    KeyboardStream,
    Predicate,
    elapsed,
    kbm,
    key,
    keyboard,
    mouse,
)
from .pads import Button, Identification, Pad, Pads
from .ports import InputDeviceMapping, Port, Ports

__all__ = [
    "Button",
    "InputState",
    "InputDeviceMapping",
    "Identification",
    "INTERRUPTED",
    "LISTENING",
    "COMPLETED",
    "DISCARDED",
    "KeyboardEvent",
    "KeyboardStream",
    "Pad",
    "Pads",
    "Port",
    "Ports",
    "Predicate",
    "elapsed",
    "kbm",
    "key",
    "keyboard",
    "mouse",
]
