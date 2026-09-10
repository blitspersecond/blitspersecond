"""The machine: everything the engine runs on, and nothing a game imports.

The test for living here is not importance, it is who builds the thing. A
game says `BlitsPerSecond()` and gets an engine that already holds all of
this; it never imports one of these names or constructs one itself.

When something out here needs to read in, `BlitsPerSecond` forwards it --
either the object (`bps.clock`) or just the value (`bps.background`),
whichever the game actually wants. The engine is the front door, and it
decides what shows.

Instruments are the deliberate exception: a probe measuring the engine
reaches through `bps._display` on purpose, and the private spelling is
exactly what marks it as measuring the engine rather than playing a game
on it.

  clock.py        the ledger: `frame`, and `time` as `frame * step` exactly.
                  Imports nothing from `time`, which is the test of whether
                  the engine really does measure in frames.
  loop.py         the machine loop: every subsystem serviced in one
                  controlled order, once per TICK -- never once per slot.
  lifecycle.py    the state machine -- CREATED through STOPPED, plus the
                  backgrounding axis that is deliberately not a state.
  events.py       the bus: window and engine facts cross subsystem seams here.
  display.py      Display: an engine's logical screen, realised on a
                  physical one.
  framebuffer.py  the seam. The engine composes into `pending` and flags it
                  complete; pacing promotes it when a slot is right for it.
                  Neither side owns the handoff, so it sits above both
                  rather than inside either.
  window/         WHERE the picture lands.
  pacing/         WHEN it lands. Read its docstring before changing it.

Living here is not the same as being unreachable. `Display` went private
outright -- a game has no call to name the screen at all. `Clock` and
`Lifecycle` did not: a game reads `bps.clock.frame` and calls
`bps.suspend()`. What none of them is, is something a game CONSTRUCTS, and
the forward is how the far side of that line gets served.

`EngineState` is the one name re-exported from the package root, because it
is a vocabulary rather than machinery: a game needs it to say what
`bps.state` came back as, and importing an enum builds nothing.

This was `display.internal`, one level deeper, back when `Display` was the
public face of drawing. Layers moving into `graphics` left `Display` with
nothing a game calls, and `display/` -- two hundred lines wrapped around
fifteen hundred -- had no reason left to sit between the engine and its own
machinery.
"""

from .clock import Clock
from .events import EventBus
from .lifecycle import EngineState, Lifecycle
from .loop import Loop
from .framebuffer import Framebuffer, FrameHandoff
from .window import Surface
from .pacing.health import FlipHealth, PresentationMonitor
from .pacing.path import PresentationPath, detect_presentation_path
from .pacing.session import PresentationSession
from .display import Display

__all__ = [
    "Clock",
    "Display",
    "EngineState",
    "EventBus",
    "FlipHealth",
    "Framebuffer",
    "FrameHandoff",
    "Lifecycle",
    "Loop",
    "PresentationMonitor",
    "PresentationPath",
    "PresentationSession",
    "Surface",
    "detect_presentation_path",
]
