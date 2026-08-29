"""Tile: one cell of a sheet, named by the map that minted it."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # a Tile names its owner; its owner builds Tiles
    from .tilemap import TileMap


class Tile:
    """One cell of a `TileMap`'s sheet -- an index, and the map it belongs to.

        door = island.tile[42]
        island.blit(door, x, y)

    **A tile is minted by a TileMap and only that TileMap will draw it.** You
    do not construct one; `tilemap.tile[n]` hands you the one that exists, and
    asking twice gives the same object.

    ## Why the owner is a TileMap and not the sheet

    Reference bound this to the atlas, and could therefore only ask *is this
    from the same art?* -- its guard compared buffers. But art is shared on
    purpose: parallax layers, a mirror match, and `PixelBuffer` interning by
    path all mean one sheet backs many maps. A handle naming the sheet is
    valid for all of them at once, so it cannot answer a question about
    identity, and identity is what a layer IS in this engine.

    Naming the map instead makes the check exact and one word: `tile.owner is
    self`. Stamping a tile into the wrong layer -- reading cells from one map
    and drawing them into another -- stops being a silent wrong picture.

    ## What it deliberately does not carry

    No source rectangle, no size, no buffer. All three are derivable from the
    owner and the index: the map has exactly one sheet and cannot change it,
    every placement matches the atlas grid, and the rectangle is arithmetic.
    Reference stored all three per tile and pooled the objects globally to
    afford it; that pool is gone with the fields it existed to amortise.
    """

    __slots__ = ("_owner", "_index")

    def __init__(self, owner: "TileMap", index: int) -> None:
        self._owner = owner
        self._index = index

    @property
    def owner(self) -> "TileMap":
        """The TileMap that minted this tile, and the only one that draws it."""
        return self._owner

    @property
    def index(self) -> int:
        """Which cell of the sheet, numbered left to right then down."""
        return self._index

    def __repr__(self) -> str:
        tag = self._owner.tag
        return f"<Tile {self._index} of {tag or 'untagged'} tilemap>"
