"""WHERE the picture lands: the OS window, and the mapping onto it.

  surface.py   the one window, and the sole producer of raw OS events --
               teed onto the EventBus so nothing outside this package
               depends on the concrete Surface.
  viewport.py  the centred integer enlargement, mapped in both directions.
"""

from .surface import Surface
from .viewport import PresentationViewport, integer_viewport

__all__ = [
    "PresentationViewport",
    "Surface",
    "integer_viewport",
]
