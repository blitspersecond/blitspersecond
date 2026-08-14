"""The compositor: where drawn frames become presented frames.

    from blitspersecond.display import Display, Layer, Layers

`Display` is the whole front of house. `BlitsPerSecond` boots exactly one and
`blitspersecond.loop` drives it -- so if you arrived here reading the engine,
this is the object behind `self._display`. Constructing it boots the `Surface`
(window + raw-event source) and the offscreen `Framebuffer`; each `flip()` then
composes the enabled layers into the framebuffer at the internal resolution and
presents through the CRT post-pass, timing how long the swap blocked
(`swap_duration`, the display's presentation-health signal). Game code never
holds one -- its contact point is `bps.layers`.

`Layers` is the ordered stack, background to foreground; a `Layer` is one slot
in it -- a single drawable (any graphics engine, or a bare PixelBuffer that gets
auto-wrapped) plus its compositing position, reordered through a fluent
interface (`layer.after(hud)`, `layer.first()`, `a.swap_with(b)`).

The engine-only machinery users never name lives in the appendix,
`display.internal` (`Surface`, `Framebuffer`, `PresentationCadence`) --
mirroring `graphics.internal`.
"""

from .display import Display
from .layer import Layer
from .layers import Layers

__all__ = [
    "Display",
    "Layer",
    "Layers",
]
