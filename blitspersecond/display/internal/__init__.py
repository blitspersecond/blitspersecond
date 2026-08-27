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
  pacing/       WHEN to swap. Display's own presentation deadline; the
                simulation clock it used to share a package with now lives
                on the engine. Read its docstring before changing it.

All are kept out of the public surface exactly like `graphics.internal`; the
general machine ordering belongs to `blitspersecond.loop`.
"""

from .surface import Surface
from .framebuffer import Framebuffer
from .pacing.health import FlipHealth, PresentationMonitor
from .pacing.path import PresentationPath, detect_presentation_path
from .pacing.session import PresentationSession

__all__ = [
    "Surface",
    "Framebuffer",
    "FlipHealth",
    "PresentationMonitor",
    "PresentationPath",
    "PresentationSession",
    "detect_presentation_path",
]
