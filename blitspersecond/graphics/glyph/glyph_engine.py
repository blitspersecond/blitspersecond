"""The auto-attaching GlyphLayer factory."""

from blitspersecond.display.layer import attach_to_display

from .glyph_layer import CharSet, GlyphLayer


class GlyphEngine:
    """The static factory: make a GlyphLayer and put it on screen.

        console = Console(GlyphEngine(CharSet.ASCII8X12))

    Calling it returns a GlyphLayer (__new__ hands back the layer; no
    GlyphEngine instance ever exists), appended at the front of the running
    engine's draw order. If no BlitsPerSecond engine exists yet (tests,
    offline tools), the layer is returned unattached, exactly as
    GlyphLayer(charset) would build it.
    """

    def __new__(cls, charset: CharSet | tuple[int, int]) -> GlyphLayer:
        return attach_to_display(GlyphLayer(charset))
