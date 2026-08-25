"""The compositor's front door.

`Display` owns the window (`Surface`), the offscreen framebuffer the game
composes into, and the CRT post-pass that presents it. Everything it sits on
is engine-only machinery in `display.internal`.
"""

from .display import Display

__all__ = [
    "Display",
]
