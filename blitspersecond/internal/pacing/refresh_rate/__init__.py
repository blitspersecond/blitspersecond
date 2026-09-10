"""Select the most accurate refresh rate exposed by the window platform."""

from __future__ import annotations

from math import isfinite
import sys
from typing import Any, TypeGuard


def _usable(rate: object) -> TypeGuard[int | float]:
    return isinstance(rate, (int, float)) and isfinite(rate) and rate >= 1


def _win32_refresh_rate(screen: Any) -> float | None:
    """Query Win32's rational active-path rate without importing it elsewhere."""
    from .win32 import query_refresh_rate

    return query_refresh_rate(screen.get_display_id())


def _xrandr_refresh_rate(screen: Any) -> float | None:
    """Query the CRTC's unrounded rate without importing X11 elsewhere."""
    from .xrandr import query_refresh_rate

    return query_refresh_rate(screen)


def get_refresh_rate(screen: Any) -> float | None:
    """Return the active screen's rate, preferring an unrounded source.

    Pyglet reports an integer on both platforms that matter, by two different
    routes, and both lose the part that decides whether the tick and slot
    grids stay commensurate:

    * Win32 ``ScreenMode.rate`` comes from integer
      ``DEVMODE.dmDisplayFrequency``, which truncates 120000/1001 to 119.
    * X11 ``ScreenMode.rate`` is ``round(dotClock / (hTotal * vTotal))``,
      which reports a 59.91781Hz panel as 60.

    A backend failure on either falls back to pyglet's mode rate, which is the
    rounded value and no worse than having no backend at all.
    """
    if sys.platform == "win32":
        try:
            rate = _win32_refresh_rate(screen)
        except (AttributeError, ImportError, OSError):
            rate = None
        if _usable(rate):
            return float(rate)
    elif sys.platform.startswith("linux"):
        try:
            rate = _xrandr_refresh_rate(screen)
        except (AttributeError, ImportError, OSError):
            rate = None
        if _usable(rate):
            return float(rate)

    try:
        mode = screen.get_mode()
        rate = float(mode.rate) if mode is not None else None
    except (AttributeError, TypeError, ValueError, NotImplementedError):
        return None
    return rate if _usable(rate) else None
