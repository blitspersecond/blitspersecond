"""The legacy GlyphEngine name, now an alias for GlyphLayer."""

from .glyph_layer import GlyphLayer

#: Legacy name, kept so existing code keeps working. See TileEngine for the
#: full note: the engines used to attach through a global lookup, layers are
#: now minted by ``bps.layers.glyph(charset)``, and what remained of the
#: factory was the layer itself.
#:
#: Calling it directly builds an UNATTACHED layer -- nothing draws it until
#: ``bps.layers.add(layer)`` places it in a draw order.
GlyphEngine = GlyphLayer
