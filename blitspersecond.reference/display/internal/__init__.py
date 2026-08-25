"""Display appendix: the engine-only machinery the compositor sits on.

Users never name these -- they meet `Display`, `Layer` and `Layers` from the
package front door. `Surface` is the pyglet window (and the sole producer of raw
OS events, teed onto the EventBus); `Framebuffer` is the offscreen FBO the
layers compose into plus the CRT post-pass that presents it -- both leaf GL
internals. `PresentationCadence` is the engine's private pacing helper that
distributes fixed simulation ticks over delivered presentations, while
`PresentationMonitor` classifies the swap stream's health.
`PresentationPath` describes whether that surface is travelling through
Gamescope, direct XWayland/X11, or Win32 without pretending it can observe the
final physical scanout. `PresentationSession` combines those pieces into the
private per-run state handed to `blitspersecond.loop`. All are kept out of the
public surface exactly like `graphics.internal`; the general machine ordering
belongs to `blitspersecond.loop`.
"""

from .surface import Surface
from .framebuffer import Framebuffer
from .presentation.cadence import PresentationCadence
from .presentation.health import FlipHealth, PresentationMonitor
from .presentation.path import PresentationPath, detect_presentation_path
from .presentation.session import PresentationSession

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
