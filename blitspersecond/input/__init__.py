"""Input: what the player did, settled onto the engine's frame boundary.

    stream.py    retention -- a bounded ring of frozen, frame-numbered
                 windows. Device-agnostic and deliberately small.
    boundary.py  InputBoundary: every device, cut together, so frame N means
                 the same interval on all of them.
    kbm/         the local keyboard and mouse, delivered over the EventBus
                 because the window pump already runs.

Sources stage arrivals as they come. At the head of each frame the engine
cuts: staging freezes into one immutable window, the window joins the ring,
and the oldest cycles out. Game code reads finalised windows and cannot reach
the frame still accumulating, so it never sees input change under it mid-tick.

`key` is pyglet's key module, re-exported rather than re-numbered. There is
one keyboard transport and there always will be, so a parallel BPS numbering
would be a table mapping a hundred and fifty symbols to themselves. Game code
writes `key.RETURN`; what it must not write is `pyglet.window.key.RETURN`, and
this is the door that makes the short spelling the available one.

Chords and the wider query vocabulary are a separate piece and belong above
this one. See `docs/bps/INPUT_STREAM.md`.
"""

from pyglet.window import key

from .boundary import InputBoundary
from .kbm import InputState, TextInput
from .stream import WINDOW_SECONDS, InputStream, InputWindow

__all__ = [
    "WINDOW_SECONDS",
    "InputBoundary",
    "InputState",
    "InputStream",
    "InputWindow",
    "TextInput",
    "key",
]
