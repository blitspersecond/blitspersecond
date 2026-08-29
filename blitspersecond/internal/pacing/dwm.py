"""Win32/DWM's live composition clock.

The configured display mode and the clock DWM actually composes against are
not necessarily identical.  ``QueryDisplayConfig`` reports the former; this
module reads the latter so BPS's private slot grid does not slowly walk across
DWM's vblank boundary.

This is presentation policy, not a windowing abstraction.  Pyglet remains the
owner of the window and reports its monitor; BPS alone decides to submit on a
clock derived from the compositor.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import sys


# Selected by a PresentMon phase sweep on the Windows reference machine,
# 2026-08-29. The absolute clock prevents period drift; this lead places the
# ordinary WGL submission ahead of DWM's vblank without consuming the game's
# uninterrupted frame budget by servicing intermediate compositor slots.
DWM_SUBMIT_LEAD = 0.00125


@dataclass(frozen=True, slots=True)
class DwmPresentationClock:
    """One absolute DWM submission grid in QueryPerformanceCounter time."""

    period: float
    phase: float
    refresh_rate: float
    submit_lead: float = DWM_SUBMIT_LEAD


class _UnsignedRatio(ctypes.Structure):
    _layout_ = "ms"
    _pack_ = 4
    _fields_ = [
        ("uiNumerator", wintypes.UINT),
        ("uiDenominator", wintypes.UINT),
    ]


class _DwmTimingInfo(ctypes.Structure):
    # dwmapi.h is packed on four-byte boundaries.  Natural 64-bit ctypes
    # alignment makes this 320 bytes, which DWM rejects with its size-mismatch
    # HRESULT; the Windows SDK layout is 292 bytes.
    _layout_ = "ms"
    _pack_ = 4
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("rateRefresh", _UnsignedRatio),
        ("qpcRefreshPeriod", ctypes.c_longlong),
        ("rateCompose", _UnsignedRatio),
        ("qpcVBlank", ctypes.c_longlong),
        ("cRefresh", ctypes.c_ulonglong),
        ("cDXRefresh", wintypes.UINT),
        ("qpcCompose", ctypes.c_longlong),
        ("cFrame", ctypes.c_ulonglong),
        ("cDXPresent", wintypes.UINT),
        ("cRefreshFrame", ctypes.c_ulonglong),
        ("cFrameSubmitted", ctypes.c_ulonglong),
        ("cDXPresentSubmitted", wintypes.UINT),
        ("cFrameConfirmed", ctypes.c_ulonglong),
        ("cDXPresentConfirmed", wintypes.UINT),
        ("cRefreshConfirmed", ctypes.c_ulonglong),
        ("cDXRefreshConfirmed", wintypes.UINT),
        ("cFramesLate", ctypes.c_ulonglong),
        ("cFramesOutstanding", wintypes.UINT),
        ("cFrameDisplayed", ctypes.c_ulonglong),
        ("qpcFrameDisplayed", ctypes.c_longlong),
        ("cRefreshFrameDisplayed", ctypes.c_ulonglong),
        ("cFrameComplete", ctypes.c_ulonglong),
        ("qpcFrameComplete", ctypes.c_longlong),
        ("cFramePending", ctypes.c_ulonglong),
        ("qpcFramePending", ctypes.c_longlong),
        ("cFramesDisplayed", ctypes.c_ulonglong),
        ("cFramesComplete", ctypes.c_ulonglong),
        ("cFramesPending", ctypes.c_ulonglong),
        ("cFramesAvailable", ctypes.c_ulonglong),
        ("cFramesDropped", ctypes.c_ulonglong),
        ("cFramesMissed", ctypes.c_ulonglong),
        ("cRefreshNextDisplayed", ctypes.c_ulonglong),
        ("cRefreshNextPresented", ctypes.c_ulonglong),
        ("cRefreshesDisplayed", ctypes.c_ulonglong),
        ("cRefreshesPresented", ctypes.c_ulonglong),
        ("cRefreshStarted", ctypes.c_ulonglong),
        ("cPixelsReceived", ctypes.c_ulonglong),
        ("cPixelsDrawn", ctypes.c_ulonglong),
        ("cBuffersEmpty", ctypes.c_ulonglong),
    ]


def _clock_from_counts(
    *,
    qpc_vblank: int,
    qpc_refresh_period: int,
    qpc_frequency: int,
) -> DwmPresentationClock | None:
    """Translate DWM's QPC integers into Python's ``perf_counter`` domain."""
    if qpc_vblank <= 0 or qpc_refresh_period <= 0 or qpc_frequency <= 0:
        return None
    period = qpc_refresh_period / qpc_frequency
    return DwmPresentationClock(
        period=period,
        phase=qpc_vblank / qpc_frequency - DWM_SUBMIT_LEAD,
        refresh_rate=qpc_frequency / qpc_refresh_period,
    )


def _windows_functions():
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    query_frequency = kernel32.QueryPerformanceFrequency
    query_frequency.argtypes = [ctypes.POINTER(ctypes.c_longlong)]
    query_frequency.restype = wintypes.BOOL

    dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
    get_timing = dwmapi.DwmGetCompositionTimingInfo
    get_timing.argtypes = [wintypes.HWND, ctypes.POINTER(_DwmTimingInfo)]
    get_timing.restype = ctypes.c_long
    return query_frequency, get_timing


def _query_frequency(function) -> int | None:
    frequency = ctypes.c_longlong()
    if not function(ctypes.byref(frequency)) or frequency.value <= 0:
        return None
    return frequency.value


def _query_timing(function) -> tuple[int, _DwmTimingInfo]:
    timing = _DwmTimingInfo()
    timing.cbSize = ctypes.sizeof(timing)
    result = function(None, ctypes.byref(timing))
    return result & 0xFFFFFFFF, timing


def query_dwm_presentation_clock() -> DwmPresentationClock | None:
    """Return DWM's live composition grid, or ``None`` when unavailable.

    Since Windows 8.1 the API requires a NULL HWND.  Selection of whether
    this desktop clock is suitable for the window's current monitor is left
    to the caller, which also knows that monitor's configured mode.
    """
    if sys.platform != "win32":
        return None
    try:
        query_frequency, get_timing = _windows_functions()
        frequency = _query_frequency(query_frequency)
        if frequency is None:
            return None

        # Microsoft documents several members as becoming valid only after a
        # prior call.  Take the second complete sample, as the test probe does.
        result, _ = _query_timing(get_timing)
        if result:
            return None
        result, timing = _query_timing(get_timing)
        if result:
            return None
    except (AttributeError, OSError):
        return None

    return _clock_from_counts(
        qpc_vblank=timing.qpcVBlank,
        qpc_refresh_period=timing.qpcRefreshPeriod,
        qpc_frequency=frequency,
    )
