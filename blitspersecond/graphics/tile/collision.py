"""Screen-space pixel collision for TileEngine layers.

A CollisionMask is a persistent bit-packed screen-space mask (1 bit per
pixel) with a one-tile retained border. A collidable TileEngine stamps into it
and scrolling translates it destructively: pixels leaving the bordered local
plane are gone, while newly stamped border content moves into the queryable
screen. The word layout and the pairwise CollisionQuery are shared
vocabulary (graphics.collision) -- any engine producing a packed mask can
answer `a.collides_with(b).at(x, y, w, h)`; CollisionQuery is re-exported
here for the tile engine's own callers.

Per-tile solidity patterns are derived from the atlas's buffer here (the atlas
itself knows nothing about collision words) and cached weakly per atlas, so
every collidable TileEngine over one atlas shares one pattern cache.
"""

import weakref
from typing import TYPE_CHECKING, Optional, Tuple

import numpy as np

from blitspersecond.graphics.common import (
    CLOW,
    CWORD,
    CWORD_BITS,
    CWORD_SHIFT,
    CollisionQuery,
)

from .batch import TileFlags

__all__ = ["CollisionMask", "CollisionQuery"]

if TYPE_CHECKING:
    from .tile_atlas import TileAtlas

# Per-atlas solidity pattern caches, keyed weakly on the TileAtlas object so
# a dead atlas takes its patterns with it. Each entry is (mask_src, cache):
# the buffer mints a NEW mask array when its pixels change, so `is not`
# on mask_src detects staleness for free.
_PATTERNS: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


def _patterns(
    atlas: "TileAtlas",
    sx: int,
    sy: int,
    tw: int,
    th: int,
    flags: int = 0,
) -> np.ndarray:
    """One oriented source tile as (display_h, nw) uint64 row bit patterns.

    Orientation is the renderer's exact Tiled order: transpose first, then
    horizontal flip, then vertical flip. All three NumPy operations are views;
    only packbits materialises the result. The packed pattern is cached by
    (source, size, flags), so that work happens once on the first orientation
    use rather than once per placement or restamp.

    Pixel p of a row = bit p, split across nw words. packbits(bitorder="little")
    plus a uint32 view lines up bit-for-bit on a little-endian machine (every
    BPS target); uint64 keeps pattern << 31 staging math from overflowing."""
    flags = int(flags) & 7
    src = atlas.buffer.mask
    entry = _PATTERNS.get(atlas)
    if entry is None or entry[0] is not src:
        entry = (src, {})
        _PATTERNS[atlas] = entry
    cache = entry[1]
    key = (sx, sy, tw, th, flags)
    pats = cache.get(key)
    if pats is None:
        oriented = src[sy : sy + th, sx : sx + tw]
        if flags & TileFlags.TRANSPOSE:
            oriented = oriented.T
        if flags & TileFlags.FLIP_H:
            oriented = oriented[:, ::-1]
        if flags & TileFlags.FLIP_V:
            oriented = oriented[::-1, :]
        oh, ow = oriented.shape
        nw = -(-ow // CWORD_BITS)
        block = np.zeros((oh, nw * CWORD_BITS), dtype=bool)
        block[:, :ow] = oriented
        packed = np.packbits(block, axis=1, bitorder="little")
        pats = packed.view(CWORD).astype(np.uint64)
        cache[key] = pats
    return pats


class CollisionMask:
    """One TileEngine's persistent screen-space collision mask, bit-packed
    (CWORD, 1 bit per pixel -- see the CWORD note above; ~29KB, L2-resident).

    Built lazily (needs Config) on first stamp/clear. Stored with a tile-sized
    retained border on every edge; `mask` exports only the 640x360 interior.
    Border pixels are real local tilemap content: a stamp just outside the
    screen waits there and destructive scrolling shifts it into the interior.
    Scratch + stage are the scroll shift's swap and carry-term buffers --
    allocated once, never in the frame path.
    """

    def __init__(self, atlas: "TileAtlas") -> None:
        self._atlas = atlas
        self._cmask: Optional[np.ndarray] = None
        self._cmask_scratch: Optional[np.ndarray] = None
        self._cmask_stage: Optional[np.ndarray] = None
        self._capron: Tuple[int, int] = (0, 0)  # (rows, words) each side
        self._cdims: Tuple[int, int, int] = (0, 0, 0)  # (h, w px, w words)
        # restamp()'s reusable work vectors (uint64 shift staging, uint32
        # lo/hi words, intp flat indices), grown with the placement count.
        self._cwork: Optional[Tuple[np.ndarray, ...]] = None

    def ensure(self, tw: int = 0, th: int = 0) -> np.ndarray:
        """The padded packed store, (re)built to fit tiles up to (tw, th)
        (default: the atlas's tile_size).

        The local tilemap has a one-tile border, but collision coordinates
        also include the sub-cell camera remainder. At the leading edge those
        add, so the packed working apron is two tile extents each side (rows
        vertically, whole words horizontally). Any valid local stamp therefore
        lands entirely inside the array -- no clipping in the stamp loop.
        Grows (preserving all bordered content at the same local coordinates)
        if a bigger oriented tile shows up;
        growth allocates, which is fine because tile sizes are static in
        practice -- it cannot recur in a steady-state frame."""
        if tw <= 0:
            tw = self._atlas.tile_size[0]
        if th <= 0:
            th = self._atlas.tile_size[1]
        need_ah = 2 * int(th)
        # One extra word absorbs the carry emitted by an unaligned pattern at
        # the outermost retained-border position.
        need_aw = -(-(2 * int(tw)) // CWORD_BITS) + 1
        if (
            self._cmask is not None
            and need_ah <= self._capron[0]
            and need_aw <= self._capron[1]
        ):
            return self._cmask
        from blitspersecond.system.config import Config

        disp = Config().display
        h, w = disp.height, disp.width
        words = -(-w // CWORD_BITS)
        ah = max(need_ah, self._capron[0])
        aw = max(need_aw, self._capron[1])
        shape = (h + 2 * ah, words + 2 * aw)
        grown = np.zeros(shape, dtype=CWORD)
        if self._cmask is not None:
            oah, oaw = self._capron
            old = self._cmask
            yoff, xoff = ah - oah, aw - oaw
            grown[yoff : yoff + old.shape[0], xoff : xoff + old.shape[1]] = old
        self._cmask = grown
        self._cmask_scratch = np.zeros(shape, dtype=CWORD)
        self._cmask_stage = np.zeros(shape, dtype=CWORD)
        self._capron = (ah, aw)
        self._cdims = (h, w, words)
        return self._cmask

    def stamp(
        self,
        x: int,
        y: int,
        source: Tuple[int, int],
        size: Tuple[int, int],
        flags: int = 0,
    ) -> None:
        """OR one tile's solid bits into the packed mask at its translated
        local position (x, y).

        This is the scalar blit-time path. A row's pattern lands as
        word-aligned pieces: pattern << (x & 31) split into nw+1 target
        words, no per-pixel work. The working apron absorbs the off-screen
        part of every valid local-border stamp."""
        stw, sth = int(size[0]), int(size[1])
        if int(flags) & int(TileFlags.TRANSPOSE):
            tw, th = sth, stw
        else:
            tw, th = stw, sth
        x, y = int(x), int(y)
        cm = self.ensure(tw, th)
        h, w, _ = self._cdims
        ah, aw = self._capron
        left, right = -(aw * CWORD_BITS), w + aw * CWORD_BITS
        top, bottom = -ah, h + ah
        if x >= right or y >= bottom or x + tw <= left or y + th <= top:
            return
        pats = _patterns(
            self._atlas, int(source[0]), int(source[1]), stw, sth, flags
        )
        nw = pats.shape[1]
        shifted = pats << np.uint64(x & (CWORD_BITS - 1))
        out = np.zeros((th, nw + 1), dtype=CWORD)
        out[:, :nw] = shifted & CLOW
        out[:, 1:] |= shifted >> np.uint64(CWORD_BITS)
        xw = (x >> CWORD_SHIFT) + aw
        cm[ah + y : ah + y + th, xw : xw + nw + 1] |= out

    def translate(self, ox: int, oy: int, nx: int, ny: int) -> None:
        """Destructively translate the whole bordered packed plane.

        This is the CPU mirror of the GPU camera translation. Content falling
        beyond the one-tile border is discarded; the newly exposed outer band
        is zero. Border stamps move naturally into the queryable interior, so
        no retained placement list is consulted and reversing direction cannot
        resurrect content that has left the local plane."""
        dx, dy = nx - ox, ny - oy
        if self._cmask is None or (dx == 0 and dy == 0):
            return
        cm = self.ensure()
        sc, st = self._cmask_scratch, self._cmask_stage
        assert sc is not None and st is not None
        rows, words = cm.shape
        # New pixel p = old pixel p + dx: a whole-word move (dx >> 5) plus a
        # cross-word carry shift (dx & 31) -- new[j] = old[j+wd] >> bo |
        # old[j+wd+1] << (32-bo) -- computed into the scratch buffer in two
        # strokes over ~29KB and swapped. Both buffers are permanent: shift,
        # don't copy-allocate. Exposed bands stay zero (scratch starts
        # blank; out-of-range source words contribute nothing). Assumes the
        # bordered store, including incoming stamps outside the screen.
        sc.fill(0)
        if abs(dx) < words * CWORD_BITS and abs(dy) < rows:
            src = cm
            dst = sc
            ys0, ys1 = max(dy, 0), rows + min(dy, 0)
            s = src[ys0:ys1]
            d = dst[max(-dy, 0) : max(-dy, 0) + (ys1 - ys0)]
            wd, bo = dx >> CWORD_SHIFT, dx & (CWORD_BITS - 1)
            j0, j1 = max(-wd, 0), min(words, words - wd)
            if bo == 0:
                if j0 < j1:
                    d[:, j0:j1] = s[:, j0 + wd : j1 + wd]
            else:
                if j0 < j1:
                    np.right_shift(s[:, j0 + wd : j1 + wd], CWORD(bo), out=d[:, j0:j1])
                k0, k1 = max(-wd - 1, 0), min(words, words - wd - 1)
                if k0 < k1:
                    stage = st[: ys1 - ys0, : k1 - k0]
                    np.left_shift(
                        s[:, k0 + wd + 1 : k1 + wd + 1],
                        CWORD(CWORD_BITS - bo),
                        out=stage,
                    )
                    d[:, k0:k1] |= stage
        self._cmask, self._cmask_scratch = sc, cm

    def restamp(self, tiles: np.ndarray, scroll: Tuple[float, float]) -> None:
        """Rebuild the packed mask wholesale from the live SoA slice: clear,
        then re-stamp every local placement intersecting the bordered store.

        Vectorised per (tile row x word): for each distinct (source, flags)
        the oriented row bit patterns come from the atlas's lazy pattern
        cache, and each pattern word becomes two np.bitwise_or.at scatters
        (low/high split of pattern << (x & 31)) across every placement wearing
        that tile orientation. ufunc.at, not fancy `|=`: stamps sharing a word
        are duplicate indices, which `|=` silently drops and .at accumulates
        correctly. Work vectors are persistent and grown with the placement
        count -- the per-frame path allocates nothing beyond per-kind
        selections."""
        cm = self.ensure()
        cm.fill(0)
        n = len(tiles)
        if n == 0:
            return
        h, w, _ = self._cdims
        sx, sy = int(scroll[0]), int(scroll[1])
        tx = tiles["target"][:, 0] - sx
        ty = tiles["target"][:, 1] - sy
        tw, th = tiles["size"][:, 0], tiles["size"][:, 1]
        cm = self.ensure(int(tw.max()), int(th.max()))
        ah, aw = self._capron
        vis = (
            (tx + tw > -(aw * CWORD_BITS))
            & (ty + th > -ah)
            & (tx < w + aw * CWORD_BITS)
            & (ty < h + ah)
        )
        if not vis.any():
            return
        ah, aw = self._capron
        stride = cm.shape[1]
        flat = cm.reshape(-1)
        # TileEngine guarantees one uniform atlas source size, so group by
        # source + orientation in 35 bits: sx:16 | sy:16 | flags:3. Each
        # distinct oriented tile's cached patterns are walked exactly once.
        src = tiles["source"].astype(np.uint64)
        flags = tiles["flags"].astype(np.uint64)
        key = (src[:, 0] << np.uint64(19)) | (src[:, 1] << np.uint64(3)) | flags
        if self._cwork is None or len(self._cwork[0]) < n:
            cap = max(8, n)
            if self._cwork is not None:
                cap = max(cap, 2 * len(self._cwork[0]))
            self._cwork = (
                np.empty(cap, dtype=np.uint64),  # shift staging
                np.empty(cap, dtype=np.uint64),  # lo/hi extraction
                np.empty(cap, dtype=CWORD),  # scatter values
                np.empty(cap, dtype=np.intp),  # flat indices
            )
        vbuf, ebuf, wbuf, ibuf = self._cwork
        ktw, kth = self._atlas.tile_size
        for k in np.unique(key[vis]):
            sel = np.nonzero(vis & (key == k))[0]
            m = len(sel)
            ksx = int(k >> np.uint64(19))
            ksy = int((k >> np.uint64(3)) & np.uint64(0xFFFF))
            kflags = int(k & np.uint64(7))
            pats = _patterns(self._atlas, ksx, ksy, ktw, kth, kflags)
            nw = pats.shape[1]
            xs, ys = tx[sel], ty[sel]
            off = (xs & (CWORD_BITS - 1)).astype(np.uint64)
            base = ((ys + ah).astype(np.intp) * stride) + (xs >> CWORD_SHIFT) + aw
            v, e, vals, idx = vbuf[:m], ebuf[:m], wbuf[:m], ibuf[:m]
            for dy in range(pats.shape[0]):
                for wc in range(nw):
                    pat = pats[dy, wc]
                    if pat == 0:
                        continue  # transparent stretch of the tile row
                    np.left_shift(pat, off, out=v)
                    np.bitwise_and(v, CLOW, out=e)
                    vals[:] = e
                    np.add(base, dy * stride + wc, out=idx)
                    np.bitwise_or.at(flat, idx, vals)
                    np.right_shift(v, np.uint64(CWORD_BITS), out=e)
                    vals[:] = e
                    np.add(idx, 1, out=idx)
                    np.bitwise_or.at(flat, idx, vals)

    def clear(self) -> None:
        """Zero the persistent packed mask (apron included)."""
        self.ensure().fill(0)

    @property
    def mask(self) -> np.ndarray:
        """The packed screen-space collision mask: a (360, 20) CWORD view,
        row 0 = top, pixel x = bit (x & 31) of word (x >> 5) (little
        bit-order). This is the canonical query surface -- window it and AND
        against another layer's mask; unpack only for debug display."""
        cm = self.ensure()
        h, _, words = self._cdims
        ah, aw = self._capron
        return cm[ah : ah + h, aw : aw + words]

    @property
    def unpacked(self) -> np.ndarray:
        """Debug/viewer convenience: the mask as (360, 640) bool, freshly
        unpacked (allocates -- keep it out of engine frame paths; a display
        layer copying it once per frame is what it's for)."""
        packed = np.ascontiguousarray(self.mask)
        h, w, _ = self._cdims
        bits = np.unpackbits(
            packed.view(np.uint8).reshape(h, -1), axis=1, bitorder="little", count=w
        )
        return bits.view(np.bool_)
