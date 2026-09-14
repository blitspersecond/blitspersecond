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

`key` is `Key`, and its values ARE pyglet's -- names that carry the condition
operators, over the numbering the transport already uses. So there is still no
translation at ingress: a scan holds whatever the window system sent, and an
`IntEnum` member hashes equal to its value, so `key.LEFT in scan.held` answers
without a table. A parallel BPS numbering would be a hundred and seventy
entries mapping values to themselves, and this is not one.

**The names exist because the operators could not.** A pyglet symbol is a
plain `int`, so `key.LCTRL & key.LALT` does not raise -- it evaluates to
`LSHIFT`, a real symbol, with nothing downstream able to tell. `condition.py`
holds that argument in full.

Game code writes `key.RETURN`; what it must not write is
`pyglet.window.key.RETURN`, and this is the door that makes the short spelling
the available one.
"""

from .boundary import InputBoundary
from .condition import Condition, matches, resolve
from .kbm import Key as key
from .kbm import Modifier, Motion, TextInput
from .request import InputState, Request
from .stream import WINDOW_SECONDS, InputStream, InputWindow

__all__ = [
    "WINDOW_SECONDS",
    "Condition",
    "InputBoundary",
    "InputState",
    "InputStream",
    "Request",
    "InputWindow",
    "Modifier",
    "Motion",
    "TextInput",
    "key",
    "matches",
    "resolve",
]
