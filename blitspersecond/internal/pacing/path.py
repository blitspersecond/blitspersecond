"""Describe the presentation path visible to the BPS process.

This is deliberately a classification of facts the child can actually know.
In particular, a client inside Gamescope cannot discover Gamescope's outer
backend; the Linux launcher records that choice in ``BPS_GAMESCOPE_BACKEND``.

It no longer STEERS anything. Every path now presents on a deadline grid with
client vsync off, so there is nothing left to classify a path in order to
decide -- `starts_on_deadline_grid`, `requests_vsync` and the
`BPS_FRAMEPACE_START_DEADLINE` override all went with that decision. What
survives is description: the path is worth logging, and worth recording in a
frame-pacing capture, because a capture is only comparable against another if
it says what produced it.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
import sys
from typing import Mapping

from blitspersecond.common import environment_flag


ENV_GAMESCOPE_BACKEND = "BPS_GAMESCOPE_BACKEND"

@dataclass(frozen=True)
class PresentationPath:
    """Stable launch-time presentation facts used by pacing and diagnostics."""

    name: str
    client: str
    session: str
    compositor: str
    outer_backend: str | None = None

    @property
    def description(self) -> str:
        path = f"{self.client} -> {self.compositor}"
        if self.outer_backend:
            path += f" (outer backend: {self.outer_backend})"
        return path


def detect_presentation_path(
    environment: Mapping[str, str] | None = None,
    *,
    platform: str | None = None,
) -> PresentationPath:
    """Classify the presentation path without opening another display.

    Pyglet 2.1 uses Xlib for every non-headless Linux window, so the session
    environment distinguishes direct XWayland from X11. Gamescope supplies
    stronger explicit markers to every child and takes precedence.
    """
    environment = os.environ if environment is None else environment
    platform = sys.platform if platform is None else platform

    if platform == "win32":
        return PresentationPath(
            name="win32",
            client="Win32/WGL",
            session="win32",
            compositor="DWM",
        )

    if not platform.startswith("linux"):
        return PresentationPath(
            name=platform,
            client=platform,
            session=environment.get("XDG_SESSION_TYPE", "unknown"),
            compositor="platform window system",
        )

    session = environment.get("XDG_SESSION_TYPE", "unknown").casefold()
    desktop = environment.get("XDG_CURRENT_DESKTOP", "")
    if environment.get("GAMESCOPE_WAYLAND_DISPLAY"):
        return PresentationPath(
            name="gamescope",
            client="Xlib/GLX",
            session=session,
            compositor="Gamescope",
            outer_backend=environment.get(ENV_GAMESCOPE_BACKEND) or None,
        )

    if session == "wayland":
        return PresentationPath(
            name="xwayland",
            client="Xlib/GLX via XWayland",
            session=session,
            compositor=desktop or "Wayland compositor",
        )

    return PresentationPath(
        name="x11",
        client="Xlib/GLX",
        session=session,
        compositor=desktop or "X11 window manager",
    )
