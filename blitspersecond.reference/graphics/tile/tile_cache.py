import weakref
from typing import TYPE_CHECKING, Dict, List, Tuple

import numpy as np

from blitspersecond.common import SingletonMeta

from blitspersecond.graphics.internal import Point, Vector
from .tile import Tile

if TYPE_CHECKING:
    from blitspersecond.graphics.common import PixelBuffer

# key into an atlas's pool: ((x, y), (w, h)) source rect, raw ints -- Point and
# Vector define __eq__ without __hash__ (unhashable), so the key stays a tuple.
_Key = Tuple[Tuple[int, int], Tuple[int, int]]

# Parallel SoA row per pooled tile (row i <-> _tiles[i]). Only the fields we
# operate on in *bulk* live here -- the owning atlas (so reclaim can broadcast a
# stale-mark on one column) and the stale flag itself (a reclaimed slot free to
# be reused). The source rect stays on the Tile's Point/Vector; it isn't queried
# in bulk yet. This is the seam onto the DOD SoA: widen the dtype, don't reshape.
_POOL_DTYPE = np.dtype([("atlas", np.int32), ("stale", np.bool_)])


class TileCache(metaclass=SingletonMeta):
    """The one global pool of source-ref Tiles, shared by every PixelBuffer.

    A Tile's (source, size, atlas) is stable, so it never needs rebuilding: ask
    for the same source rect on the same atlas and you get back the SAME Tile
    instance. Rebuilding a TileEngine from an atlas -- even every frame -- therefore
    allocates zero new Tile objects, which is the churn that pressures the GIL
    and the GC beneath it.

    Storage is a stable, append-only list of Tile objects indexed by id, mirrored
    by a parallel structured ndarray (_pool, row i <-> _tiles[i]). The objects
    give callers a stable handle; the ndarray gives the cache vectorised bulk ops
    -- reclaim broadcasts a stale-mark on the atlas column, and reuse finds a free
    id with one flatnonzero rather than walking the list. The list only grows when
    no stale slot can be reused ("only append if we need").

    Reclaim is anchored on the PixelBuffer lifecycle -- intern() registers a
    weakref.finalize on a buffer the first time one of its tiles is interned
    (the buffer itself knows nothing about tiles). When a buffer goes out of scope its
    rows are marked stale (reclaimable) rather than dropped; the next atlas that
    needs a tile re-stamps a stale slot in place instead of allocating one. So
    even the footgun of tearing down and rebuilding layers each frame degrades
    gracefully -- Python objects get recycled, not minted and collected.
    """

    def __init__(self) -> None:
        # id -> Tile object. Append-only and stable: an id never moves, so it is a
        # durable handle (and the row index into _pool). A list, not a dict:
        # access is always by dense contiguous int id (reuse-in-place leaves no
        # holes), so list indexing -- a direct offset, no hashing -- beats a dict
        # lookup (~1.6x) at a fraction of the memory.
        self._tiles: List[Tile] = []
        # Parallel SoA, capacity-doubled; only rows [:_count] are in use.
        self._pool: np.ndarray = np.zeros(0, dtype=_POOL_DTYPE)
        self._count: int = 0
        # How many of those rows are stale (reusable). A guard so a fresh build
        # (no stale rows) never scans for a free id -- keeps append O(1).
        self._stale: int = 0
        # atlas id -> {source-rect: id}. The intern fast-path (O(1) key lookup)
        # and O(1) whole-atlas eviction; values are ids into _tiles/_pool.
        self._index: Dict[int, Dict[_Key, int]] = {}

    def intern(self, source: Point, size: Vector, buffer: "PixelBuffer") -> Tile:
        """The interning seam behind `Tile(...)`. Returns the shared Tile for
        (atlas, source, size): the existing instance on a hit, else a reused stale
        slot re-stamped with this key, else a freshly appended slot. Repeat asks
        for the same key return the same object, so nothing is minted twice.

        buffer is the atlas PixelBuffer; the Tile holds a weakref to it (so tiles
        never pin atlases alive). source (a Point) and size (a Vector) name the
        rect. On a fresh append the cache *adopts* them as the Tile's own storage,
        so a build allocates no extra coord objects -- meaning: once passed to
        Tile()/intern(), they belong to the cache; the caller must not mutate them
        afterward. On a hit or a stale-slot reuse they are only read (their values
        copied), then discarded."""
        sx, sy = source.x, source.y
        sw, sh = size.x, size.y
        atlas = int(buffer.id)
        key = ((sx, sy), (sw, sh))
        group = self._index.get(atlas)
        if group is None:
            group = self._index[atlas] = {}
            # The pool anchors reclaim on the buffer's own lifecycle: when the
            # buffer is collected, its rows go stale. Registered here (first
            # intern per atlas) rather than by PixelBuffer -- the buffer knows
            # nothing about tiles, and a buffer that never mints one never
            # gets a finalizer. finalize holds no strong ref, so it never
            # keeps the buffer alive.
            weakref.finalize(buffer, self.reclaim, atlas)
        tid = group.get(key)
        if tid is None:
            tid = group[key] = self._alloc(source, size, buffer, atlas)
        return self._tiles[tid]

    def _alloc(
        self, source: Point, size: Vector, buffer: "PixelBuffer", atlas: int
    ) -> int:
        """Get an id for a new (atlas, rect): reuse a stale slot if one exists,
        else append (adopting the caller's Point/Vector). Returns the row id."""
        # Get a free id quickly -- vectorised, no list walk. Skipped entirely when
        # nothing is stale (the fresh-build path), so appends stay O(1).
        if self._stale:
            tid = int(np.flatnonzero(self._pool["stale"][: self._count])[0])
            tile = self._tiles[tid]
            # Re-stamp the slot's existing Point/Vector in place, so reuse
            # allocates nothing but the new atlas weakref. The passed source/size
            # are read, not kept.
            tile._source.x, tile._source.y = source.x, source.y
            tile._size.x, tile._size.y = size.x, size.y
            tile._buffer = weakref.ref(buffer)
            self._pool["stale"][tid] = False
            self._pool["atlas"][tid] = atlas
            self._stale -= 1
            return tid
        # No stale slot: grow. Adopt the caller's Point/Vector as the Tile's own
        # storage -- a fresh build then allocates exactly what its Tiles need.
        tid = self._count
        tile = object.__new__(Tile)
        tile._source = source
        tile._size = size
        tile._buffer = weakref.ref(buffer)
        self._tiles.append(tile)
        self._ensure_capacity(tid + 1)
        self._pool["atlas"][tid] = atlas
        self._pool["stale"][tid] = False
        self._count = tid + 1
        return tid

    def _ensure_capacity(self, n: int) -> None:
        """Grow _pool to hold at least n rows, doubling so appends amortise O(1)."""
        cap = len(self._pool)
        if n <= cap:
            return
        grown = np.zeros(max(8, cap * 2, n), dtype=_POOL_DTYPE)
        grown[:cap] = self._pool
        self._pool = grown

    def reclaim(self, atlas: int) -> None:
        """Flag a dead atlas's tiles reclaimable in one broadcast: mark every row
        whose atlas column matches stale. Safe because a buffer only dies once
        nothing (no TileEngine, no Layer) references it, so no live placement can
        still point at these slots. Called from the finalizer intern() registers
        on the buffer -- never by hand."""
        group = self._index.pop(atlas, None)
        if group is None:
            return
        pool = self._pool[: self._count]
        pool["stale"][pool["atlas"] == atlas] = True
        # Every id in the popped group was live (atlas ids are unique and never
        # reissued), so exactly len(group) rows just became stale.
        self._stale += len(group)

    def __len__(self) -> int:
        """Number of live (non-stale) tiles."""
        return self._count - self._stale
