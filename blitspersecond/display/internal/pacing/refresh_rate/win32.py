"""Win32 backend: read an active display path's rational refresh rate."""

from __future__ import annotations

import ctypes
from collections.abc import Iterable


_ERROR_SUCCESS = 0
_ERROR_INSUFFICIENT_BUFFER = 122
_QUERY_RETRIES = 3


def select_refresh_rate(
    device_name: str,
    candidates: Iterable[tuple[str, int, int]],
) -> float | None:
    """Select and validate one device's rational rate from active paths."""
    wanted = device_name.casefold()
    for candidate_name, numerator, denominator in candidates:
        if candidate_name.casefold() != wanted:
            continue
        if numerator <= 0 or denominator <= 0:
            return None
        rate = numerator / denominator
        return rate if rate >= 1 else None
    return None


def _query_active_refresh_rates() -> list[tuple[str, int, int]]:
    """Return ``(GDI source name, numerator, denominator)`` for active paths."""
    from pyglet.libs.win32 import _user32
    from pyglet.libs.win32.constants import (
        DISPLAYCONFIG_DEVICE_INFO_GET_SOURCE_NAME,
        QDC_ONLY_ACTIVE_PATHS,
    )
    from pyglet.libs.win32.types import (
        DISPLAYCONFIG_PATH_INFO,
        DISPLAYCONFIG_SOURCE_DEVICE_NAME,
        UINT32,
    )

    for _ in range(_QUERY_RETRIES):
        path_count = UINT32()
        mode_count = UINT32()
        result = _user32.GetDisplayConfigBufferSizes(
            QDC_ONLY_ACTIVE_PATHS,
            ctypes.byref(path_count),
            ctypes.byref(mode_count),
        )
        if result != _ERROR_SUCCESS:
            return []

        paths = (DISPLAYCONFIG_PATH_INFO * path_count.value)()
        # Pyglet uses the same opaque 64-byte allocation because only path
        # information is consumed here; QueryDisplayConfig still requires the
        # corresponding mode buffer.
        modes = ctypes.create_string_buffer(64 * mode_count.value)
        result = _user32.QueryDisplayConfig(
            QDC_ONLY_ACTIVE_PATHS,
            ctypes.byref(path_count),
            paths,
            ctypes.byref(mode_count),
            modes,
            None,
        )
        if result == _ERROR_INSUFFICIENT_BUFFER:
            continue
        if result != _ERROR_SUCCESS:
            return []

        candidates = []
        for path in paths[: path_count.value]:
            if not path.targetInfo.targetAvailable:
                continue
            source = DISPLAYCONFIG_SOURCE_DEVICE_NAME()
            source.header.adapterId = path.sourceInfo.adapterId
            source.header.id = path.sourceInfo.id
            source.header.type = DISPLAYCONFIG_DEVICE_INFO_GET_SOURCE_NAME
            source.header.size = ctypes.sizeof(source)
            result = _user32.DisplayConfigGetDeviceInfo(
                ctypes.byref(source.header),
            )
            if result != _ERROR_SUCCESS:
                continue
            rational = path.targetInfo.refreshRate
            candidates.append(
                (
                    source.viewGdiDeviceName,
                    int(rational.Numerator),
                    int(rational.Denominator),
                )
            )
        return candidates
    return []


def query_refresh_rate(device_name: str) -> float | None:
    """Return one active GDI display's rational rate, or ``None`` on failure."""
    try:
        candidates = _query_active_refresh_rates()
    except (AttributeError, ImportError, OSError):
        return None
    return select_refresh_rate(device_name, candidates)
