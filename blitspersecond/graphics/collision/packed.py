"""The packed bit layout every collision mask in the engine speaks.

One bit per pixel along x: pixel `x` lives at bit `x & 31` of word `x >> 5`,
little bit-order, row 0 = top. That is exactly what
`np.packbits(bitorder="little")` produces once the result is viewed as
`uint32` on a little-endian machine -- which is every platform BPS targets.
So packing is a library call rather than a loop, and unpacking for a debug
viewer is its inverse rather than a second piece of arithmetic that has to
agree with the first.

Why the word is a DWORD, and why the screen being 640 wide is the reason:
`bps/reference/graphics/collision.md`, "One bit per pixel". The size is a constant
here rather than a literal spelled across three pillars, so this is the only
file that would decide a different one.

## Patterns stage in uint64 and store in uint32

Not an inconsistency, and not a type to unify -- the two are different
moments. A row pattern is shifted LEFT by up to 31 places to land at an
unaligned x, and the bits pushed past bit 31 are not overflow, they are the
carry into the next word. Staging in `uint64` is what makes that carry
recoverable; the low half and the high half are then extracted as `uint32`
and OR'd into two adjacent words. `pack` therefore hands back `uint64`
deliberately, and the masks it is stamped into are `uint32`.
"""

import numpy as np

#: The mask word. See the module docstring for why it is this size.
CWORD = np.uint32

#: Pixels per word, and its log2 -- the shift that turns an x into a word
#: index. Two spellings of one number, because the hot paths want both and
#: `int(log2(...))` in a stamp loop is not a thing worth computing.
CWORD_BITS = 32
CWORD_SHIFT = 5

#: The low `CWORD_BITS` of a `uint64` staging value: everything that belongs
#: to the word a shifted pattern started in. What is left above it is the
#: carry into the next word.
CLOW = np.uint64(0xFFFFFFFF)

def words_for(width: int) -> int:
    """How many words hold `width` pixels. 640 -> 20, exactly."""
    return -(-int(width) // CWORD_BITS)


def pack(bits: np.ndarray) -> np.ndarray:
    """A bool image as `(height, words)` uint64 row patterns.

    The shared stamping primitive: whatever a pillar decides is solid -- a
    tile's oriented pixels, a sprite part's silhouette, a filled hitbox
    rectangle -- becomes rows of packed bits here, once, and is cached by
    whoever asked. Rows are padded out to a whole word, so the caller never
    has to think about a source whose width is not a multiple of 32.

    `uint64` on the way out is the staging width, for the reason the module
    docstring gives: a pattern about to be shifted into place needs somewhere
    for its carry to go.
    """
    return pack_store(bits).astype(np.uint64)


def pack_store(bits: np.ndarray) -> np.ndarray:
    """A bool image as `(height, words)` **uint32** rows -- a whole plane.

    The other half of the split the module docstring draws. `pack` is for a
    PATTERN, which is about to be shifted somewhere and needs the staging
    room; this is for a STORE, which already sits where it belongs and never
    moves. A console's planes are screen space and a screen-space plane is
    never shifted, so paying the wide staging type for one would double the
    bytes to buy a carry that has nowhere to go.

    The packing is identical either way, which is why `pack` is written as
    this plus a widening cast: one implementation, two moments.
    """
    height, width = bits.shape
    nwords = words_for(width)
    if nwords == 0:
        # A zero-width source. Legal to ask about and impossible to hit, so
        # it packs to nothing rather than tripping the view below on a row
        # of no bytes.
        return np.zeros((height, 0), dtype=CWORD)
    block = np.zeros((height, nwords * CWORD_BITS), dtype=bool)
    block[:, :width] = bits
    return np.packbits(block, axis=1, bitorder="little").view(CWORD)


def unpack(packed: np.ndarray, width: int) -> np.ndarray:
    """A packed mask back to `(height, width)` bool.

    **Debug and viewers only, and it allocates.** Nothing in a frame path
    should call this: the packed form is the one the engine asks questions
    of, and a bool plane is eight times the bytes to stream for the same
    answer. It exists so a human can look at a mask, and so a test can state
    an expectation in pixels rather than in hex.
    """
    packed = np.ascontiguousarray(packed)
    return np.unpackbits(
        packed.view(np.uint8), axis=1, bitorder="little", count=int(width)
    ).view(np.bool_)
