"""Tile: one cell of a sheet, named by the map that minted it."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # a Tile names its owner; its owner builds Tiles
    from .tilemap import TileMap


class Tile:
    """One cell of a `TileMap`'s sheet -- an index, and the map it belongs to.

        door = island.tile[42]
        island.cell[x, y] = door

    **A tile is minted by a TileMap and only that TileMap will draw it.** You
    do not construct one; `tilemap.tile[n]` hands you the one that exists, and
    asking twice gives the same object. The guard is `tile.owner is self`, and
    it is exact: art is shared on purpose, so a handle naming the SHEET would
    be valid for every map over that art and could answer nothing about
    identity.

    Carries no rectangle, no size and no buffer -- all three are arithmetic
    from the owner and the index. Why that holds, and what it buys, is in
    `bps/reference/tiles.md`, "Stamping a cell".
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
