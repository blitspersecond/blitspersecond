"""The legacy TileEngine name, now an alias for TileLayer."""

from .tile_layer import TileLayer

#: Legacy name, kept so existing code keeps working.
#:
#: TileEngine used to build a TileLayer and attach it to the running display,
#: found through a global lookup. Layers are now minted by
#: ``bps.layers.tile(...)``, which attaches to the collection you named -- so
#: the factory had nothing left to do, and this is a plain alias rather than a
#: shim. The names agree as a result: a layer from ``bps.layers.tile()`` IS a
#: TileEngine, and ``isinstance`` answers accordingly.
#:
#: Calling it directly builds an UNATTACHED layer -- nothing draws it until
#: ``bps.layers.add(layer)`` places it in a draw order. That is the advanced
#: path (GL-free tests, offline tools); the mainline is ``bps.layers.tile()``.
TileEngine = TileLayer
