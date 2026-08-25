"""The legacy SpriteEngine name, now an alias for SpriteLayer."""

from .sprite_layer import SpriteLayer

#: Legacy name, kept so existing code keeps working. See TileEngine for the
#: full note: the engines used to attach through a global lookup, layers are
#: now minted by ``bps.layers.sprite(sheet)``, and what remained of the
#: factory was the layer itself.
#:
#: Calling it directly builds an UNATTACHED layer -- nothing draws it until
#: ``bps.layers.add(layer)`` places it in a draw order.
SpriteEngine = SpriteLayer
