"""Shared collision vocabulary: packed screen-space masks and queries.

Every collision identity owns persistent bit-packed masks. Tile/Glyph
engines expose one default mask; SpriteEngine additionally exposes named
planes (attack, hurt and so on). Composition pays once when drawable state
changes. CollisionQuery only selects masks and ANDs their words, so repeated
questions are independent of the number of tiles or sprite instances that
were stamped into them.
"""

from typing import Optional, Protocol, Tuple, runtime_checkable

import numpy as np

# Collision mask word size. Masks pack 1 bit per pixel along x: pixel x lives
# at bit (x & 31) of word (x >> 5), little bit-order -- the same layout
# np.packbits/np.unpackbits(bitorder="little") produce on a little-endian
# machine, which every target platform is. DWORD is the sweet spot (a 640px
# row = 20 words; tiles up to 32px straddle at most 2 words, and pattern<<31
# still fits uint64 staging math); an arch-dependent uint64 is a later knob if
# a platform ever wants it.
CWORD = np.uint32
CWORD_BITS = 32
CWORD_SHIFT = 5  # log2(CWORD_BITS)
CLOW = np.uint64(0xFFFFFFFF)

@runtime_checkable
class Collidable(Protocol):
    """A mask participant: the intent flag and the packed screen-space mask
    (row 0 = top, CWORD layout above)."""

    @property
    def collidable(self) -> bool: ...

    @property
    def collision_mask(self) -> np.ndarray: ...


@runtime_checkable
class PlaneCollidable(Protocol):
    """A named packed-mask participant. ``planes=None`` selects the default
    occupied-pixel silhouette; multiple names select their union."""

    @property
    def collidable(self) -> bool: ...

    @property
    def planes(self) -> frozenset: ...

    def collision_mask_for(
        self, planes: Optional[Tuple[str, ...]] = None
    ) -> np.ndarray: ...


def _edge_words(x0: int, x1: int) -> Tuple[int, int, CWORD, CWORD]:
    """Word span [j0, j1) covering pixel columns [x0, x1), plus the bit
    trims for the partial first and last words."""
    j0, j1 = x0 >> CWORD_SHIFT, ((x1 - 1) >> CWORD_SHIFT) + 1
    first = CWORD((0xFFFFFFFF << (x0 & (CWORD_BITS - 1))) & 0xFFFFFFFF)
    last = CWORD((1 << (((x1 - 1) & (CWORD_BITS - 1)) + 1)) - 1)
    return j0, j1, first, last

class CollisionQuery:
    """One prepared pairwise collision question between two collidable
    drawables (from their collides_with()) -- ask it with .at().

    Holds the two participants and optional sprite-plane selections, not mask
    arrays: a dirty participant may rebuild between calls. Every pairing is
    the same packed-word AND once the selected masks have been resolved.

    Semantics: everything is screen space, so "collides" means *visually
    coincident on screen* -- two layers with different parallax scrolls
    collide where they appear to overlap. Coordinates given to .at() are
    screen pixels, (0, 0) top-left."""

    __slots__ = ("_a", "_b", "_mine", "_theirs")

    def __init__(
        self,
        a,
        b,
        mine: Optional[Tuple[str, ...]] = None,
        theirs: Optional[Tuple[str, ...]] = None,
    ) -> None:
        self._a = a
        self._b = b
        self._mine = mine
        self._theirs = theirs

    def at(
        self,
        x: Optional[int] = None,
        y: int = 0,
        w: int = 0,
        h: int = 0,
    ) -> bool:
        """True if the two layers have any solid geometry in common inside
        the screen-space box (x, y, w, h); .at() with no arguments asks the
        whole screen. Multiple selected planes mean any overlap between the
        two selected sets; the test is exact to the pixel column."""
        ma = self._mask(self._a, self._mine)
        mb = self._mask(self._b, self._theirs)
        return self._masks_at(ma, mb, x, y, w, h)

    @staticmethod
    def _mask(participant, planes) -> np.ndarray:
        selected = getattr(participant, "collision_mask_for", None)
        if selected is not None:
            return selected(planes)
        return participant.collision_mask

    @staticmethod
    def _masks_at(ma, mb, x, y, w, h) -> bool:
        """The original mask x mask path: a windowed AND over the packed
        words -- a few words by a few rows for a typical hitbox -- with the
        partial first and last words trimmed by edge bit-masks."""
        rows = min(ma.shape[0], mb.shape[0])
        words = min(ma.shape[1], mb.shape[1])
        if x is None:
            return bool(
                np.bitwise_and(
                    ma[:rows, :words], mb[:rows, :words]
                ).any()
            )
        width = words * CWORD_BITS
        x0, y0 = max(int(x), 0), max(int(y), 0)
        x1, y1 = min(int(x) + int(w), width), min(int(y) + int(h), rows)
        if x0 >= x1 or y0 >= y1:
            return False
        j0, j1, first, last = _edge_words(x0, x1)
        c = ma[y0:y1, j0:j1] & mb[y0:y1, j0:j1]
        if j1 - j0 == 1:
            return bool((c[:, 0] & (first & last)).any())
        c[:, 0] &= first
        c[:, -1] &= last
        return bool(c.any())
