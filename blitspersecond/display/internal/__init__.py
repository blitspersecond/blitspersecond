"""Display appendix: the engine-only machinery the compositor sits on.

Users never name these -- they meet `Display`, `Layer` and `Layers` from the
package front door.

  surface.py    the pyglet window, and the sole producer of raw OS events
                (teed onto the EventBus so nothing outside display/ depends
                on the concrete Surface).
  framebuffer.py  the offscreen FBO the layers compose into, plus the CRT
                post-pass that presents it.
  viewport.py   WHERE the internal screen lands on the window -- the
                centred integer enlargement, mapped in both directions.
  pacing/       WHEN to tick and WHEN to swap. The hard half, and a package
                of its own: each fact the platform will not reliably report
                gets a module, evidence, and a guard. Read its docstring
                before changing anything in it.

All are kept out of the public surface exactly like `graphics.internal`; the
general machine ordering belongs to `blitspersecond.loop`.
"""

from .surface import Surface
from .framebuffer import Framebuffer
from .pacing.cadence import PresentationCadence
from .pacing.health import FlipHealth, PresentationMonitor
from .pacing.path import PresentationPath, detect_presentation_path
from .pacing.session import PresentationSession

__all__ = [
    "Surface",
    "Framebuffer",
    "PresentationCadence",
    "FlipHealth",
    "PresentationMonitor",
    "PresentationPath",
    "PresentationSession",
    "detect_presentation_path",
]
