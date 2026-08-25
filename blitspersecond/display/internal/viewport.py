"""WHERE the internal screen lands on a real window.

The spatial question, and the easy one; `internal.pacing` is the temporal
counterpart (when to tick, when to swap) and is where the difficulty lives.

Consumed in both directions -- `framebuffer` maps the internal screen OUT to
window pixels, `input.kbm.mouse_handler` maps a pointer position BACK IN. One
function owns the mapping so those two can never disagree about where the
picture is.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class PresentationViewport:
    """Largest centred whole-number enlargement that fits in a window."""

    x: int
    y: int
    width: int
    height: int
    scale: int


def integer_viewport(
    internal_width: int,
    internal_height: int,
    window_width: int,
    window_height: int,
) -> PresentationViewport:
    """Return a centred integer-scaled viewport inside the window.

    Presentation never shrinks the internal framebuffer: a surface smaller
    than the logical render target cannot provide an integer pixel mapping.
    """

    if min(internal_width, internal_height, window_width, window_height) <= 0:
        raise ValueError("presentation dimensions must be positive")

    scale = min(
        window_width // internal_width,
        window_height // internal_height,
    )
    if scale < 1:
        raise ValueError(
            f"{window_width}x{window_height} surface is smaller than "
            f"{internal_width}x{internal_height} internal resolution"
        )

    width = internal_width * scale
    height = internal_height * scale
    return PresentationViewport(
        x=(window_width - width) // 2,
        y=(window_height - height) // 2,
        width=width,
        height=height,
        scale=scale,
    )
