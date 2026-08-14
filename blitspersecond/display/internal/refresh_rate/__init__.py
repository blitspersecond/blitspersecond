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


def get_refresh_rate(screen: Any) -> float | None:
    """Return the active screen's rate, preferring Win32's rational value.

    Pyglet's Win32 ``ScreenMode.rate`` comes from integer
    ``DEVMODE.dmDisplayFrequency`` and truncates modes such as 120000/1001 to
    119.  Other platforms, and Win32 API failures, retain pyglet's existing
    mode-rate behaviour.
    """
    if sys.platform == "win32":
        try:
            rate = _win32_refresh_rate(screen)
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
