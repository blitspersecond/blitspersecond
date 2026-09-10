"""The tilemap's screen-space collision mask.

One persistent bit-packed plane per collidable map, stamped as quads are
placed and translated destructively as the camera moves. The layout and the
question are `graphics.collision`'s; what is here is the tile pillar's own way
of filling one, argued in `bps/reference/graphics/tiles.md`, "The mask fills as the
quads do".

**Push, not pull.** The mask is written when content changes and never rebuilt
to answer a question -- content changes when the camera crosses a cell
boundary, and questions are asked several times a frame.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Tuple

import numpy as np

from blitspersecond.common import SCREEN

from ..collision import (
    CWORD,
    CWORD_BITS,
    CWORD_SHIFT,
    stamp_many,
    unpack,
    words_for,
)
from ..orientation import Orientation
from .internal import bounded_plane

if TYPE_CHECKING:
    from ..atlas import Atlas


class CollisionMask:
    """One map's packed screen-space mask, with a working border.

    Exported as exactly the 640x360 interior; stored larger. The border is
    real map content waiting to arrive: a stamp just off-screen lands in the
    apron, and destructive scrolling walks it into the queryable area without
    anything being restamped.

    ## The apron, and what decides its size

    Exactly `bounded_plane` -- **the same statement of the retained plane the
    map's own stamp guard reads, never the cell** -- plus two things the plane
    does not know about. `_drift`, up to 31 pixels of camera the store has not
    moved for, pushes a trailing stamp that much further out; and a pattern
    landing unaligned at the outermost position carries into one more word.
    So: the plane, the drift, and a word.

    Sizing it from the cell instead is a real bug and a silent one -- stamps
    that land past a cell-sized apron are simply declined, the border arrives
    empty, and every query about it answers False.

    ## Nothing here is allocated per frame, and nothing here is collected

    `stamp` writes into the store; `translate` copies into a permanent scratch
    and swaps the two. That is the whole inventory -- two buffers, because a
    move is whole words and whole rows and has no carry to stage.
    """

    __slots__ = (
        "_atlas",
        "_mask",
        "_scratch",
        "_apron",
        "_dims",
        "_drift",
        "_one_x",
        "_one_y",
    )

    def __init__(self, atlas: "Atlas") -> None:
        cell = atlas.cell
        height, width = SCREEN.height, SCREEN.width
        interior = words_for(width)
        # Sized from the SAME statement of the bounded plane the stamp guard
        # uses, never from the cell alone. The plane is the viewport rounded
        # OUTWARD to whole cells plus a border, so at 360 rows and a 16px cell
        # it reaches 384 -- twenty-four rows past the screen, not the sixteen
        # a cell-sized apron allows for. Sizing from the cell dropped trailing
        # stamps, and dropped them silently: the guard below simply declined
        # and the border arrived empty.
        #
        # A turned tile is square or shape-preserving (`applies_to` refuses a
        # diagonal turn on a rectangular sheet), so a stamp never occupies
        # more than the plane already accounts for.
        left, top, right, bottom = bounded_plane(cell)
        # Symmetric, because the store is. The trailing edge is the demanding
        # one -- the leading edge only ever holds a border cell.
        rows = max(-top, bottom - height)
        # Plus the drift: the store has not moved for up to 31 pixels of
        # camera, so a trailing stamp sits that much further out than the
        # plane says. The final word absorbs an unaligned pattern's carry.
        words = (
            max(words_for(-left), words_for(right - width + CWORD_BITS - 1))
            + 1
        )
        shape = (height + 2 * rows, interior + 2 * words)
        self._atlas = atlas
        self._mask = np.zeros(shape, dtype=CWORD)
        # A permanent one-element pair for the scalar stamp. `stamp_many`
        # takes arrays, and a refill makes one call per placement, so building
        # two throwaway arrays per stamp put the allocation back that going to
        # the kernel was meant to remove -- it was a third of the call.
        self._one_x = np.zeros(1, dtype=np.int64)
        self._one_y = np.zeros(1, dtype=np.int64)
        self._scratch = np.zeros(shape, dtype=CWORD)
        self._apron: Tuple[int, int] = (rows, words)
        self._dims: Tuple[int, int, int] = (height, width, interior)
        #: The sub-word part of the camera that the store has not moved for.
        #: Always in [0, 32); `offset` hands it to the query.
        self._drift: int = 0

    # -- writing ----------------------------------------------------------

    def stamp(
        self,
        key,
        x: int,
        y: int,
        orientation: Orientation = Orientation.NONE,
    ) -> None:
        """OR one tile's solid bits in at local pixel `(x, y)`.

        One placement, one call -- through the SAME compiled scatter the
        sprite pillar uses, at N=1.

        **This used to be two rectangular word-slice writes**, which is the
        shape reference had for a handful of parts and which the port took.
        The sprite pillar then measured the pair and deleted the split:
        *"8.52us for the slice strokes against 0.42us for `stamp_many` at N=1,
        so the scatter is cheaper before there is anything to batch. The split
        was an artefact of the tool."* It was an artefact here too; the tile
        road simply had not been looked at.

        **One stamp MECHANISM, both pillars -- not one coordinate space.** The
        apron stays a tile affordance, because tiles scroll and sprites do
        not: a sprite is at a coordinate and its plane IS the screen, so it
        passes `(0, 0)` and has no border to keep. A tilemap moves a
        contiguous grid in lockstep under a camera, so it holds the viewport
        plus a cell of skirt and carries a sub-word drift. That is why
        `stamp_many` takes STORE coordinates and says so: the caller adds its
        own apron, because the two pillars size one differently and the kernel
        is shared.

        Out-of-range stamps are dropped rather than refused. `TileMap._stamp`
        already refuses a placement outside the bounded plane with a message
        about the camera; a second refusal here would fire on the same mistake
        with less to say about it.

        **The whole-placement guard is kept, though the kernel drops per word
        on its own.** They are not the same policy: a sprite half off the top
        writes the rows that land, because a sprite genuinely hangs off the
        edge; a tile that cannot fit is a placement the map should not have
        made, and half a tile's bits is a hitbox missing an edge you can still
        see. `test_a_stamp_whose_carry_would_fall_outside_is_dropped_whole`
        pins it. The guard's original reason -- that the NumPy slice road
        would clamp the carry write to nothing and truncate silently -- is
        gone with that road; this keeps the BEHAVIOUR while changing only the
        mechanism.
        """
        pattern = self._atlas.solidity(key, orientation)
        height, nwords = pattern.shape
        if not height or not nwords:
            return
        rows, words = self._apron
        # Through the drift: the store has not moved for the sub-word part of
        # the camera, so a placement's bit position carries it. The skirt goes
        # in here too, in PIXELS -- `stamp_many` takes store coordinates and
        # derives its own word and shift, and `words * CWORD_BITS` is a whole
        # number of words, so the shift is the one this always used.
        px = int(x) + self._drift
        row = int(y) + rows
        word = (px >> CWORD_SHIFT) + words
        if (
            row < 0
            or row + height > self._mask.shape[0]
            or word < 0
            or word + nwords + 1 > self._mask.shape[1]
        ):
            return
        self._one_x[0] = px + words * CWORD_BITS
        self._one_y[0] = row
        stamp_many(self._mask, pattern, self._one_x, self._one_y)

    def clear_rect(self, x: int, y: int, width: int, height: int) -> None:
        """Zero the bits under the local pixel rect `(x, y, width, height)`.

        **The only operation in the engine that takes solidity AWAY**, and it
        exists because a replaced cell's old bits have nowhere else to go: a
        mask accumulates with OR, so stamping a new tile over an old one lights
        the union of the two rather than replacing it. Everything else that
        removes bits removes all of them (`clear`) or slides them off the edge
        (`translate`).

        It does not know what it is un-stamping. The caller clears a rect and
        then restamps whatever else still intersects it -- a hand `blit`
        straddling four grid cells is put back by that pass, not defended
        against here. That keeps this one arithmetic and makes overlap the
        caller's question, which is where the answer lives.

        Clipped, not dropped, where `stamp` drops. A stamp that falls outside
        the plane simply does not appear and the picture agrees; a CLEAR that
        silently did nothing would leave bits lit under a cell that no longer
        holds them, and a stale hitbox is the failure this whole card is
        about. So a rect half off the edge clears the half that is there.
        """
        if width <= 0 or height <= 0:
            return
        rows, words = self._apron
        # Through the drift, exactly as `stamp` does: the store has not moved
        # for the sub-word part of the camera, so a rect's bit position
        # carries it too. The two must agree or a clear lands beside its stamp.
        px0 = int(x) + self._drift
        px1 = px0 + int(width)
        row0 = int(y) + rows
        row1 = row0 + int(height)
        store_rows, store_words = self._mask.shape
        row0, row1 = max(row0, 0), min(row1, store_rows)
        if row0 >= row1:
            return
        # Word span, inclusive of the word the last pixel lives in.
        word0 = (px0 >> CWORD_SHIFT) + words
        word1 = ((px1 - 1) >> CWORD_SHIFT) + words
        first, last = max(word0, 0), min(word1, store_words - 1)
        if first > last:
            return

        # A mask of the bits to KEEP, so the clear is one AND per word. Zero
        # is the default -- an interior word of the rect keeps nothing -- and
        # the two partial edges add back what lies outside the rect. When the
        # rect fits in a single word both edges land on it and the survivors
        # are the intersection, which is what OR-ing the two gives.
        keep = np.zeros(last - first + 1, dtype=CWORD)
        low = px0 & (CWORD_BITS - 1)
        if word0 >= 0:
            keep[0] |= CWORD((1 << low) - 1)
        high = px1 & (CWORD_BITS - 1)
        if high and word1 <= store_words - 1:
            keep[-1] |= CWORD(((1 << CWORD_BITS) - 1) ^ ((1 << high) - 1))
        self._mask[row0:row1, first : last + 1] &= keep

    def translate(self, dx: int, dy: int) -> None:
        """The camera moved by `(dx, dy)`; make the stored plane agree.

        **This exists because stamping is linear in stamps.** A screen of 8px
        tiles is 3854 of them at 8.5us each, so restamping the window as the
        camera moves would cost 31.6ms -- two frames' budget -- against 4.7us
        to slide the plane it is already holding. Measured; see *Batch the
        collision stamp* on the board for why the 8.5us is dispatch and not
        work.

        **Most of the time it moves nothing at all.** Content appearing to
        move is the same thing as the screen looking somewhere else, so a step
        is absorbed by advancing `offset` and leaving every bit where it is.
        Only when the drift would carry content out of the apron does anything
        get copied -- and then it goes in whole words and whole rows, never
        part of a word.

        That is the CPU half of the split the GPU already makes. There, a
        coarse step rewrites buffers and a fine one is a single vertex
        attribute; here, a whole-word step moves memory and the sub-word
        remainder rides in the offset the query reads. Same idea, one level
        down, and the reason fine scrolling costs a collidable layer nothing.

        The remainder never exceeds 31 pixels, so the apron -- one cell of
        retained border plus a word -- covers it with room over.

        Rows need no remainder. They are the outer axis, so moving them is a
        block copy at memcpy speed rather than a shift, and there is nothing
        to be gained by deferring one. `dy` is applied exactly.
        """
        dx, dy = int(dx), int(dy)
        self._drift += dx
        # Floor division, so a leftward drift normalises the same way a
        # rightward one does and the remainder is always in [0, 32).
        words = self._drift >> CWORD_SHIFT
        self._drift -= words << CWORD_SHIFT
        if words:
            self._move(0, words)
        if dy:
            self._move(dy, 0)

    def _move(self, rows: int, words: int) -> None:
        """Slide the stored plane by whole rows and whole words.

        Content goes to LOWER indices as the camera advances, which is what
        makes the offset shrink back by the amount moved. Whatever is
        uncovered is zeroed rather than left behind -- a stale stamp reappearing
        at the far edge would be content the camera had already left.

        One `np.copyto` over overlapping regions of the same buffer would be
        undefined, so this copies into the permanent scratch and swaps. No
        allocation, and no third buffer: the carry staging the old
        bit-shifting path needed is gone with the shifting.
        """
        height, width = self._mask.shape
        source, target = self._mask, self._scratch
        target.fill(0)
        y0, y1 = max(rows, 0), height + min(rows, 0)
        x0, x1 = max(words, 0), width + min(words, 0)
        if y0 < y1 and x0 < x1:
            target[
                max(-rows, 0) : max(-rows, 0) + (y1 - y0),
                max(-words, 0) : max(-words, 0) + (x1 - x0),
            ] = source[y0:y1, x0:x1]
        self._mask, self._scratch = target, source

    def clear(self) -> None:
        """Zero the whole stored plane, apron included."""
        self._mask.fill(0)

    # -- reading ----------------------------------------------------------

    @property
    def mask(self) -> np.ndarray:
        """The whole stored plane -- apron included -- as `(rows, words)`
        CWORD, one bit per pixel.

        **Not clipped to the screen**, because a query has to be able to read
        the border. Screen `(0, 0)` sits at `offset` inside this, and the
        compiled kernel reads through that offset for nothing. Clipping here
        would hide exactly the content the apron exists to hold.

        A view of a buffer that `translate` SWAPS, so it must be re-read
        rather than retained. `Collider` resolves through here on every
        question for that reason.
        """
        return self._mask

    @property
    def offset(self) -> Tuple[int, int]:
        """Where this plane's coordinate origin sits in the store.

        The apron, plus the sub-word camera remainder the store has not moved
        for. A layer adds its own fine scroll to this before handing it to a
        query -- the mask knows how far IT has drifted, and the camera knows
        where it is looking.
        """
        rows, words = self._apron
        return (words * CWORD_BITS + self._drift, rows)

    @property
    def unpacked(self) -> np.ndarray:
        """The screen as `(360, 640)` bool. Allocates; viewers and tests.

        Applies `offset`, so this is what a query sees rather than what the
        store holds -- border content is outside it, which is the point.
        """
        height, width, _interior = self._dims
        ox, oy = self.offset
        bits = unpack(self._mask, self._mask.shape[1] * CWORD_BITS)
        out = np.zeros((height, width), dtype=bool)
        rows = min(height, bits.shape[0] - oy)
        cols = min(width, bits.shape[1] - ox)
        if rows > 0 and cols > 0:
            out[:rows, :cols] = bits[oy : oy + rows, ox : ox + cols]
        return out

    def __repr__(self) -> str:
        rows, words = self._apron
        return (
            f"<CollisionMask {self._mask.shape[1]}x{self._mask.shape[0]} words "
            f"apron=({rows}, {words})>"
        )
