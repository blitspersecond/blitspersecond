"""The face a tilemap presents onto its own sheet.

    tilemap.tile[n]     one cell of the sheet     -- TileAccess

Internal because **you never name this type**. You reach through a tilemap to
it, exactly as you reach through a console to `console.glyph[id]`, which is
the same face on the other pillar: the source cells a placement draws from.

There is no face for `tilemap.map[x, y]`. The console needs `CellAccess`
because its grid IS the screen and it wants to say so; a tilemap's grid is a
world larger than the screen and it is a plain, x-major NumPy array that
means what NumPy means. See `tilemap.py` for why the two pillars diverge
there rather than converge.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional

from .tile import Tile

if TYPE_CHECKING:
    from .tilemap import TileMap


class TileAccess:
    """Indexed reads over a tilemap's sheet: `tilemap.tile[n]` -> `Tile`.

    Cells are numbered left to right then down, matching `Atlas`. Negative
    indices count from the end. `len()` is how many the sheet holds.

    **Memoised, not interned.** Asking twice gives the same `Tile`, because a
    refill loop asking per cell must not allocate per call. The memo is a
    lazily filled list of `len(sheet)` slots that dies with its tilemap --
    deliberately not reference's global pool, whose weakrefs, reclaim pass and
    staleness flag all existed to manage a lifetime that an owner now answers
    for free.
    """

    __slots__ = ("_owner", "_memo")

    def __init__(self, owner: "TileMap", count: int) -> None:
        self._owner = owner
        self._memo: List[Optional[Tile]] = [None] * count

    def __len__(self) -> int:
        return len(self._memo)

    def __getitem__(self, index: int) -> Tile:
        count = len(self._memo)
        position = int(index)
        if position < 0:
            position += count
        if not 0 <= position < count:
            raise IndexError(
                f"tile {index} outside this map's sheet, which holds "
                f"{count} (0..{count - 1})"
            )
        tile = self._memo[position]
        if tile is None:
            tile = Tile(self._owner, position)
            self._memo[position] = tile
        return tile

    def __repr__(self) -> str:
        return f"<TileAccess over {len(self._memo)} cells>"
