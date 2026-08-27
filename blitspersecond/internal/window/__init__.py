"""WHERE the picture lands: the OS window, and the mapping onto it.

  surface.py   the pyglet window, and the sole producer of raw OS events --
               teed onto the EventBus so nothing outside this package ever
               depends on the concrete Surface.
  viewport.py  the centred integer enlargement that carries the internal
               screen onto whatever size the window happens to be, mapped in
               both directions so input can come back the other way.
"""

from .surface import Surface
from .viewport import PresentationViewport, integer_viewport

__all__ = [
    "PresentationViewport",
    "Surface",
    "integer_viewport",
]
