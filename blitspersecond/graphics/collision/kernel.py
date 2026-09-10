"""The two places in the engine that shift collision bits.

`collide_any` answers the whole collision question: do two packed planes share
a set bit inside a screen-space box, with each plane read through its own
offset. `stamp_many` writes one cached pattern in at many positions at once,
which is how a sprite layer composes a silhouette out of a population.

Everything else -- translation, the tilemap's scalar stamp -- moves whole
words and whole rows, or shifts one pattern the way NumPy is good at. Keeping
the sub-word arithmetic to this file is the same rule `local_rect` follows on
the draw side: two statements of one transform drift, and they drift
invisibly.

**Compiling is the exception in this engine, not the habit**, and the case for
these two is written out in `bps/reference/graphics/collision.md` ("Why this is
compiled") rather than assumed here. The short of it: `collide_any` is
compiled so that a per-plane offset costs nothing, because in NumPy it does
not and the tool would then have chosen a design where the mask moves with the
camera. `stamp_many` is compiled because NumPy has no vectorised accumulating
scatter at all. Neither is here merely because it is faster.

## Bounds are the kernel's own business

Numba does not bounds-check by default, so an index it is handed wrong reads
whatever is next in memory and returns a plausible answer. The first draft of
`collide_any` did exactly that and passed 4000 random cases against a
reference before the error was found -- it had been reading zeros. Every row
and word access below is therefore guarded here, out-of-range reads as empty,
and the fuzz in `test_collision_kernel.py` runs a second time with
`boundscheck=True` so a future edit cannot quietly reintroduce one.
"""

import numpy as np
from numba import njit

__all__ = ["collide_any", "stamp_many"]


# `cache=True` only: compile once per machine, not once per process.
#
# NOT `nogil`, and the difference is which thread the work is on. Releasing the
# GIL lets ANOTHER thread run while this one computes, so it is worth having
# exactly where there is another thread to serve. Audio has one -- its
# operators synthesise off the frame thread, and holding the GIL there would
# take frame time to make sound. Collision does not: it runs inside the game's
# own tick, on the one thread the 16.67ms budget belongs to, and there is
# nothing waiting to be let in. The flag would be a claim the code could not
# keep.
@njit(cache=True)
def collide_any(a, a_x, a_y, b, b_x, b_y, x0, x1, y0, y1):
    """True if planes `a` and `b` share a set bit in screen box `[x0,x1) x
    [y0,y1)`.

    `(a_x, a_y)` is where screen `(0, 0)` sits inside `a` -- so a plane that
    IS the screen passes `(0, 0)`, and a bordered one passes its apron plus
    whatever sub-word remainder its camera has not moved. Reading is clipped:
    anything outside a plane is empty, never an error, because a hitbox that
    has walked off the edge has stopped being able to touch things rather
    than stopped existing.

    Both planes are `(rows, words)` uint32, one bit per pixel along x, pixel
    `p` at bit `p & 31` of word `p >> 5`.
    """
    a_rows, a_words = a.shape
    b_rows, b_words = b.shape
    screen_x = x0
    while screen_x < x1:
        span = 32 if screen_x + 32 <= x1 else x1 - screen_x
        # The last chunk is partial; trim it so bits past the box cannot
        # answer for it. A full chunk needs no trim, and saying so avoids
        # `1 << 32`.
        keep = np.uint32(0xFFFFFFFF) if span == 32 else np.uint32((1 << span) - 1)
        # Where this chunk starts in each plane, split into a word and a bit.
        # Negative positions floor, so word -1 with shift 31 correctly means
        # "one bit of this chunk lies before the plane starts".
        pa = screen_x + a_x
        pb = screen_x + b_x
        wa, sa = pa >> 5, pa & 31
        wb, sb = pb >> 5, pb & 31
        for y in range(y0, y1):
            ra = y + a_y
            rb = y + b_y
            if ra < 0 or ra >= a_rows or rb < 0 or rb >= b_rows:
                continue
            # The funnel shift: 32 bits straddling two words. Each half is
            # guarded independently, so a chunk hanging off either end of a
            # plane contributes only the part that is really there.
            va = np.uint32(0)
            if 0 <= wa < a_words:
                va = a[ra, wa] >> np.uint32(sa) if sa else a[ra, wa]
            if sa and 0 <= wa + 1 < a_words:
                va |= a[ra, wa + 1] << np.uint32(32 - sa)
            if va == 0:
                continue        # nothing here to intersect with; skip b
            vb = np.uint32(0)
            if 0 <= wb < b_words:
                vb = b[rb, wb] >> np.uint32(sb) if sb else b[rb, wb]
            if sb and 0 <= wb + 1 < b_words:
                vb |= b[rb, wb + 1] << np.uint32(32 - sb)
            if (va & vb & keep) != 0:
                return True
        screen_x += 32
    return False


# `flat[idx] |= vals` is not a faster spelling of what this does -- it is a
# WRONG one, and that is the reason this function exists rather than a NumPy
# one-liner. Fancy-index assignment is BUFFERED: read all, OR all, write all,
# so duplicate indices keep only the last write. Three sprites landing in one
# word give 0b100 where the answer is 0b111.
#
# Overlapping sprites are not an edge case; composing overlaps into one plane
# is what a silhouette IS. So the unbuffered path is compulsory, and NumPy's
# is `np.bitwise_or.at` -- a scalar read-modify-write loop through the ufunc
# protocol, which is where 95% of the pre-compile cost went. See
# `bps/reference/graphics/collision.md` for the measurements.
@njit(cache=True)
def stamp_many(store, pattern, xs, ys):
    """OR one packed pattern into `store` at every `(x, y)` in `xs`, `ys`.

    Coordinates are STORE coordinates -- the caller has already added its
    apron and dropped what cannot land. That keeps the apron out of here,
    which matters because the two pillars size one differently and this is
    shared.

    `pattern` is `(rows, words)` uint64 holding 32 live bits a word, as
    `Atlas.solidity` returns. Each row lands as `pattern << (x & 31)` across
    two adjacent words, so a placement costs `rows * words` iterations and no
    per-pixel work.

    Out-of-range writes are DROPPED, per placement word rather than per
    placement: a sprite half off the top writes the rows that land and skips
    the rest. Numba does not bounds-check, so every access below is guarded
    here -- see the module docstring for what that cost the first time it was
    not.
    """
    rows, words = store.shape
    height, nwords = pattern.shape
    for i in range(xs.shape[0]):
        x = xs[i]
        row = ys[i]
        shift = np.uint64(x & 31)
        word = x >> 5
        for r in range(height):
            target_row = row + r
            if target_row < 0 or target_row >= rows:
                continue
            for w in range(nwords):
                value = pattern[r, w] << shift
                if value == 0:
                    continue
                target = word + w
                if 0 <= target < words:
                    store[target_row, target] |= np.uint32(
                        value & np.uint64(0xFFFFFFFF)
                    )
                if shift and 0 <= target + 1 < words:
                    store[target_row, target + 1] |= np.uint32(
                        value >> np.uint64(32)
                    )
