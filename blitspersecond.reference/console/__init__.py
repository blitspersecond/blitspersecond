"""Teletype semantics over an existing GlyphEngine.

Console is a controller, not a layer. Its GlyphLayer is the composited thing
to the layer stack; use Console for cursor, write, wrap, scroll, and echo.
"""

from .console import Console
from .cursor import Cursor

# The colour names (RED, WHITE, ...) live in blitspersecond.colors, not here --
# they colour sprites and tiles too, so they aren't the console's to own.

__all__ = ["Console", "Cursor"]
