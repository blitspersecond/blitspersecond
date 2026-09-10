"""X11/XRandR backend: read a CRTC's unrounded refresh rate.

Pyglet computes exactly the right number and then throws the fraction away --
`XlibScreenModeXrandr._calculate_refresh_rate` is
`round(dotClock / (hTotal * vTotal))`, so a panel running 59.91781Hz is
reported as `60`. That is the same defect as Win32's integer
`DEVMODE.dmDisplayFrequency`, and it matters for the same reason: pacing
against a rate the panel does not run at is what makes the tick and slot grids
slide, which surfaces as a periodically repeated frame.

Nothing here opens a display or enumerates outputs. Pyglet has already found
the CRTC for this screen and holds the connection; this reads the mode it
already selected and does the division in floating point.
"""

from __future__ import annotations

from typing import Any


def refresh_from_mode(
    dot_clock: int, h_total: int, v_total: int
) -> float | None:
    """Vertical refresh implied by a mode line, unrounded.

    The pixel clock over the total pixels in a frame -- totals, not visible
    dimensions, because blanking is part of the period. This is what `xrandr`
    prints and what the panel actually runs at.
    """
    total = h_total * v_total
    if dot_clock <= 0 or h_total <= 0 or v_total <= 0:
        return None
    return dot_clock / total


def query_refresh_rate(screen: Any) -> float | None:
    """Return this screen's CRTC refresh rate, or ``None`` when unavailable.

    Returns ``None`` rather than raising for every way this can be absent --
    a screen pyglet built without XRandR, a CRTC that has been disabled since,
    a mode id no longer in the resource list. The caller falls back to the
    reported mode rate, which is the rounded value and no worse than before.
    """
    crtc_id = getattr(screen, "crtc_id", None)
    display = getattr(getattr(screen, "display", None), "_display", None)
    if not crtc_id or display is None:
        return None

    try:
        from pyglet.libs.x11 import xlib, xrandr
    except (ImportError, OSError):
        return None

    resources = None
    crtc = None
    try:
        # The resource list is per-DISPLAY and is fetched against the root
        # window; the CRTC is then looked up inside it. Passing the crtc here
        # is the easy mistake and fails silently as "no resources".
        #
        # ...Current rather than XRRGetScreenResources: the latter asks the
        # server to re-poll every output, which is a round trip per call and
        # this runs on a window-move event.
        root = xlib.XDefaultRootWindow(display)
        resources = xrandr.XRRGetScreenResourcesCurrent(display, root)
        if not resources:
            return None
        crtc = xrandr.XRRGetCrtcInfo(display, resources, crtc_id)
        if not crtc or not crtc.contents.mode:
            return None
        mode_id = crtc.contents.mode
        modes = resources.contents
        for index in range(modes.nmode):
            mode = modes.modes[index]
            if mode.id == mode_id:
                return refresh_from_mode(
                    int(mode.dotClock), int(mode.hTotal), int(mode.vTotal)
                )
        return None
    except (AttributeError, OSError, ValueError):
        return None
    finally:
        if crtc:
            xrandr.XRRFreeCrtcInfo(crtc)
        if resources:
            xrandr.XRRFreeScreenResources(resources)
