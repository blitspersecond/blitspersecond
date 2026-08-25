from __future__ import annotations

import weakref
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    # Type positions only -- Tile and TileCache are mutually referential
    # (the cache news up and re-stamps Tiles; Tile() interns through the
    # cache), so this side binds its half at first call, not import time.
    from blitspersecond.graphics.common import PixelBuffer
    from blitspersecond.graphics.internal import Point, Vector

    from .tile_cache import TileCache

# The TileCache singleton, bound on first Tile() (see __new__).
_cache: "Optional[TileCache]" = None


class Tile:
    """A cheap pointer into an atlas's texture: a pixel offset, a size, and a
    reference to the PixelBuffer (atlas) it samples -- no pixels of its own.

    *Interned*, like a singleton keyed on (atlas, source, size): constructing a
    Tile does NOT necessarily allocate one. `Tile(...)` routes through the global
    TileCache, which returns the existing instance for that key if there is one,
    else recycles a reclaimed slot, else builds one. So re-reading the same tile
    -- even rebuilding a whole TileEngine's placements every frame -- mints zero new objects,
    which is the churn that pressures the GIL and the GC beneath it.

    A mutable slot, NOT a frozen value: that's what lets the cache re-stamp a
    reclaimed slot's fields instead of allocating. Never mutate a Tile yourself
    (the cache owns its lifecycle) and never store a target on it -- target is a
    blit() argument; a source-ref must stay placement-free to be poolable.

    The atlas is held by *weakref*, and this is load-bearing: a Tile lives in the
    pool effectively forever, so a strong ref would pin every PixelBuffer alive
    and the weakref-finalize reclaim would never fire. Because it is weak, .buffer
    goes None exactly when the atlas is collected -- which is the true, cheap
    staleness signal (.stale), no per-access numpy read needed.

    The type is public (atlases hand Tiles to callers); its pool (TileCache,
    which owns every Tile's lifecycle) lives beside it in graphics.tile."""

    __slots__ = ("_source", "_size", "_buffer")

    # Private, read-only through properties: only the cache (which owns the Tile's
    # lifecycle) writes these, on the interned/recycled slot. There is no __init__
    # -- a cache hit must not re-run construction -- so the cache sets the slots
    # directly. _source = offset (Point) ; _size = extent (Vector, x/y = w/h) ;
    # _buffer = weakref to the atlas PixelBuffer.
    _source: Point
    _size: Vector
    _buffer: "weakref.ref[PixelBuffer]"

    def __new__(cls, source: Point, size: Vector, buffer: "PixelBuffer") -> "Tile":
        # Late-bound: the cache imports Tile (it news up and re-stamps slots),
        # so this side of the seam resolves its half at first call, not import
        # time. The singleton is then memoised on the module -- rebuilding a
        # layer's tiles every frame (the tile_stress footgun) pays no import
        # machinery per mint.
        global _cache
        if _cache is None:
            from .tile_cache import TileCache

            _cache = TileCache()
        return _cache.intern(source, size, buffer)

    @property
    def source(self) -> Point:
        return self._source

    @property
    def size(self) -> Vector:
        return self._size

    @property
    def buffer(self) -> "Optional[PixelBuffer]":
        """The atlas this tile samples, or None if it has been collected (stale)."""
        return self._buffer()

    @property
    def stale(self) -> bool:
        """True once the atlas is gone -- the true staleness signal, one weakref
        deref, no numpy. A correct program never reads a stale tile (reclaim only
        fires when nothing references it), so this is for debug/assertions."""
        return self._buffer() is None

    @property
    def atlas(self) -> int:
        """The atlas id, or -1 if the atlas has been collected."""
        buffer = self._buffer()
        return buffer.id if buffer is not None else -1

    def __repr__(self) -> str:
        return f"Tile(source={self._source}, size={self._size}, atlas={self.atlas})"
