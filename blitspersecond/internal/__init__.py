"""The machine: everything the engine runs on, and nothing a game names.

The test for living here is not importance, it is naming. A game says
`BlitsPerSecond()`, `bps.loop`, `bps.metrics`, and reaches for drawing
through `graphics` -- it never says a word from this package. Instruments
are the deliberate exception: a probe measuring the engine reaches through
`bps._display` on purpose, and the private spelling is exactly what marks it
as measuring the engine rather than playing a game on it.

  loop.py         the machine loop: every subsystem serviced in one
                  controlled order, once per presentation slot.
  lifecycle.py    the state machine -- CREATED through STOPPED, plus the
                  backgrounding axis that is deliberately not a state.
  display.py      Display: an engine's logical screen, realised on a
                  physical one.
  framebuffer.py  the seam. The engine composes into `next` and flags it
                  complete; pacing promotes it when a slot is right for it.
                  Neither side owns the handoff, so it sits above both
                  rather than inside either.
  window/         WHERE the picture lands.
  pacing/         WHEN it lands. Read its docstring before changing it.

This was `display.internal`, one level deeper, back when `Display` was the
public face of drawing. Layers moving into `graphics` left `Display` with
nothing a game calls, and `display/` -- two hundred lines wrapped around
fifteen hundred -- had no reason left to sit between the engine and its own
machinery.
"""

from .lifecycle import EngineState, Lifecycle
from .loop import Loop
from .framebuffer import Framebuffer, FramebufferPair
from .window import Surface
from .pacing.health import FlipHealth, PresentationMonitor
from .pacing.path import PresentationPath, detect_presentation_path
from .pacing.session import PresentationSession
from .display import Display

__all__ = [
    "Display",
    "EngineState",
    "FlipHealth",
    "Framebuffer",
    "FramebufferPair",
    "Lifecycle",
    "Loop",
    "PresentationMonitor",
    "PresentationPath",
    "PresentationSession",
    "Surface",
    "detect_presentation_path",
]
