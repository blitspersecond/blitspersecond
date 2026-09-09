"""The pillar's own vocabulary: how a world is stored, and the faces onto it.

    tilemap.tile[n]     one cell of the SHEET     -- TileAccess
    tilemap.cell[x, y]  one cell of the WORLD     -- CellAccess

Internal because **you never name any of this**. You reach through a tilemap
to it, exactly as you reach through a console to `console.glyph[id]` and
`console.cell[x, y]`.

`PLANE_DTYPE` lives here for the same reason: a layout nobody outside can name
is a layout that can change. `EMPTY_TILE` is the one word that does go out,
because a caller filling a buffer taken off `cell[:, :]` needs to say "nothing
here" and `np.zeros` says tile 0, which is a real tile.

Why the world is reachable only through `cell`, and what each road checks, is
in `bps/reference/tiles.md`, "A world, and `cell` is the only road in".
"""

from __future__ import annotations

from math import ceil
from typing import TYPE_CHECKING, Any, List, Optional, Tuple

import numpy as np

from blitspersecond.common import SCREEN, Size

from ..orientation import Orientation, applies_to
from .tile import Tile

#: One cell of a world: which tile of the sheet, and how it is turned.
#:
#: `tile` is a LOCAL index into the map's one sheet, zero-based, with
#: `EMPTY_TILE` (-1) meaning nothing is there. `flags` is `Orientation`'s 0..7
#: bit layout.
PLANE_DTYPE = np.dtype([("tile", np.int32), ("flags", np.uint8)])

#: No tile in this cell. It exists because an array cannot hold `None` -- so
#: `cell` says `None` and the array says this, one spelling per road, and the
#: accessor deliberately does NOT also accept it.
EMPTY_TILE = -1


def bounded_plane(cell: Size) -> Tuple[int, int, int, int]:
    """Where a stamp may land, in local pixels: `(left, top, right, bottom)`.

    The viewport rounded OUTWARD to whole cells, plus one cell of border on
    every side.

    **It lives here because two things read it and they must not each work it
    out.** `TileMap` refuses a stamp outside it and `CollisionMask` sizes its
    apron to hold it, and those two agreeing is not optional: an apron short
    of the plane drops border content SILENTLY. Note the rounding is why -- at
    360 rows and a 16px cell the plane reaches 384, twenty-four rows past the
    screen rather than the sixteen a cell-sized apron would allow for.
    """
    return (
        -cell.width,
        -cell.height,
        ceil(SCREEN.width / cell.width) * cell.width + cell.width,
        ceil(SCREEN.height / cell.height) * cell.height + cell.height,
    )

if TYPE_CHECKING:
    from .tilemap import TileMap


class TileAccess:
    """Indexed reads over a tilemap's sheet: `tilemap.tile[n]` -> `Tile`.

    Cells are numbered left to right then down, matching `Atlas`. Negative
    indices count from the end. `len()` is how many the sheet holds.

    **Memoised, not interned.** Asking twice gives the same `Tile`, because a
    refill loop asking per cell must not allocate per call. The memo is a
    lazily filled list of `len(sheet)` slots that dies with its tilemap.
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


def _axis(value: Any, size: int, wraps: bool, name: str, writing: bool) -> int:
    """One scalar index, resolved against an axis of `size` cells.

    Negative counts from the end, as Python does everywhere -- `cell[-1, 0]`
    is the last column. Bounds apply after that resolution.

    **Reads wrap on a wrapped axis; writes never do.** Reading past the edge
    is what a camera does when a world loops, and `_block` already implements
    exactly this for the refill. Writing past the edge is a cell being named
    and arithmetic being got instead, so it raises on every axis.
    """
    position = int(value)
    if wraps and not writing:
        return position % size
    if position < 0:
        position += size
    if not 0 <= position < size:
        raise IndexError(
            f"{name}={value} is outside this world's {size} cells (0..{size - 1})"
            + ("" if writing or not wraps else "; wrap that axis to read past it")
        )
    return position


class CellAccess:
    """The world: `cell[x, y]` -> `None`, or `(Tile, Orientation)`.

        level.cell[3, 5]  = level.tile[7]                  # one cell
        level.cell[3, 5]  = (level.tile[7], Orientation.FLIP_H)
        level.cell[3, 5]  = None                           # empty it
        level.cell[3:9, 4] = wall                          # a run, one write
        level.cell[a:b, c] = level.cell[d:e, f]            # round-trips

    **X FIRST**, screen order, and here that is also the STORAGE order -- a
    world is x-major -- so nothing is transposed on any road.

    **A write reaches the world always, and the screen too when the cell is on
    it.** That is what the accessor exists for: a NumPy write into a raw array
    cannot be observed, so a cell edited in view would keep its old quad and
    its old mask bits. Off screen there is nothing to do -- the refill reads
    the world as the camera arrives.

    **Orientation round-trips**, so `cell[a, b] = cell[c, d]` preserves a flip.
    A console's `cell` drops the facing and both rules are right; see
    `bps/reference/tiles.md`.
    """

    __slots__ = ("_owner",)

    def __init__(self, owner: "TileMap") -> None:
        self._owner = owner

    @staticmethod
    def _scalar(value: Any) -> bool:
        return isinstance(value, (int, np.integer))

    def __getitem__(self, cell: tuple) -> Any:
        """One cell, or a COPY of a block of them.

            level.cell[3, 5]        -> None, or (Tile, Orientation)
            level.cell[3:9, 4]      -> a PLANE_DTYPE array
            level.cell[:, :]        -> the whole world, copied

        **A block read is a COPY, not a view**, and that is load-bearing: a
        view would put the world back in public hands through the side door,
        where `cell[0:10, 0:10]["tile"] = 5` would reach it with nothing
        noticing.

        The copy is also the buffer to fill and write back, which is why this
        pillar needs no `empty_map`. Costs are in `bps/reference/tiles.md` --
        the short of it is that a block beats any per-cell road, so slices are
        the answer to "I have a lot of cells".
        """
        x, y = cell
        owner = self._owner
        if self._scalar(x) and self._scalar(y):
            cx = _axis(x, owner.cols, owner.wrap[0], "x", writing=False)
            cy = _axis(y, owner.rows, owner.wrap[1], "y", writing=False)
            entry = owner._map[cx, cy]
            identity = int(entry["tile"])
            if identity == EMPTY_TILE:
                return None
            return (owner.tile[identity], Orientation(int(entry["flags"])))
        return owner._map[x, y].copy()

    def __setitem__(self, cell: tuple, value: Any) -> None:
        """Change one cell, or a block of them in one write.

        A scalar value fills, an array lands -- the same rule the console's
        `cell` follows. What can be written:

            None                        the cell holds no tile
            tile                        this map's `tile[n]`, unturned
            (tile, orientation)         and turned
            a PLANE_DTYPE array         what a block read hands back

        **The scalar road checks OWNERSHIP; the array road checks RANGE.** A
        `Tile` knows which map minted it, so one from another map is refused; a
        raw array carries no `Tile`, so all that can be checked is that its ids
        exist on this sheet. The asymmetry is deliberate and argued in
        `bps/reference/tiles.md` -- an out-of-range id would otherwise be a
        certain `IndexError` inside a later refill, with nothing in the
        traceback naming the write.

        **Writes raise out of range on every axis, wrapped or not.** Reading
        past the edge is a camera looking across a seam; writing past it is
        arithmetic that went wrong.
        """
        x, y = cell
        owner = self._owner
        if self._scalar(x) and self._scalar(y):
            cx = _axis(x, owner.cols, owner.wrap[0], "x", writing=True)
            cy = _axis(y, owner.rows, owner.wrap[1], "y", writing=True)
            owner._map[cx, cy] = self._entry(value)
            owner._restamp_region(cx, cy, cx + 1, cy + 1)
            return

        target = owner._map[x, y]
        if isinstance(value, np.ndarray) and value.dtype == PLANE_DTYPE:
            self._check_ids(value["tile"])
            target[...] = value
        else:
            target[...] = self._entry(value)
        owner._restamp_region(*self._span(x, y))

    def _entry(self, value: Any) -> tuple:
        """A written value as the `(tile, flags)` the array holds."""
        owner = self._owner
        if value is None:
            return (EMPTY_TILE, 0)
        if isinstance(value, tuple):
            if len(value) != 2:
                raise TypeError(
                    f"a cell takes a tile, or (tile, orientation); got a "
                    f"{len(value)}-tuple"
                )
            tile, orientation = value
        else:
            tile, orientation = value, Orientation.NONE
        if isinstance(tile, (int, np.integer)):
            raise TypeError(
                f"a cell takes a Tile from this map's tile[n], not the index "
                f"{int(tile)}"
                + (
                    ". Use None to empty a cell -- EMPTY_TILE is the array's "
                    "word, not the accessor's"
                    if int(tile) == EMPTY_TILE
                    else f". Did you mean tile[{int(tile)}]?"
                )
            )
        owner._require_own(tile)
        orientation = Orientation(int(orientation) & 7)
        cell = owner.tileset.cell
        if not applies_to(orientation, (cell.width, cell.height)):
            raise ValueError(
                f"{orientation!r} turns a tile diagonally, and this sheet's "
                f"tiles are {cell.width}x{cell.height}. A quarter turn would "
                f"make them {cell.height}x{cell.width}, which either overflows "
                f"the neighbouring cells or has to be squeezed -- and "
                f"squeezing stops the pixels being square. Rectangular sheets "
                f"take NONE, FLIP_H, FLIP_V and ROT_180; square sheets take "
                f"all eight."
            )
        return (tile.index, int(orientation))

    def _check_ids(self, ids: np.ndarray) -> None:
        """Every id in a landed array names a cell of this sheet, or is empty.

        Vectorised over the whole write, and the ONLY check the array road
        makes -- see `__setitem__` for why range is checked where ownership
        is not.
        """
        count = len(self._owner.tileset)
        bad = (ids != EMPTY_TILE) & ((ids < 0) | (ids >= count))
        if bad.any():
            first = int(ids[np.unravel_index(int(np.argmax(bad)), bad.shape)])
            raise IndexError(
                f"this array names tile {first}, and this map's sheet holds "
                f"{count} (0..{count - 1}). Use EMPTY_TILE for an empty cell."
            )

    def _span(self, x: Any, y: Any) -> tuple:
        """The bounding box of a block write, in world cells.

        A bounding box rather than the exact selection, because restamping a
        cell that did not change rewrites the same quad over the same bits.
        Being generous costs a stamp; being narrow loses one.
        """
        owner = self._owner
        cols, rows = owner.cols, owner.rows
        x0, x1 = (x.indices(cols)[:2] if isinstance(x, slice) else (int(x), int(x) + 1))
        y0, y1 = (y.indices(rows)[:2] if isinstance(y, slice) else (int(y), int(y) + 1))
        if x0 < 0:
            x0 += cols
        if x1 < 0:
            x1 += cols
        if y0 < 0:
            y0 += rows
        if y1 < 0:
            y1 += rows
        return (x0, y0, x1, y1)

    def __repr__(self) -> str:
        return f"<CellAccess over {self._owner.cols}x{self._owner.rows} cells>"
