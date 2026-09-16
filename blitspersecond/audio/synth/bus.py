"""What a Stage's output travels on: one current block, mono or stereo.

A Bus is the wire between stages. It is deliberately not a queue and not a
buffer -- it holds the ONE block currently being settled, and the graph is
pulled rather than pushed, so there is never a second block in flight waiting
for somebody to take it.

Mono and stereo buses share no base class. They are used at different points
in the graph and never interchangeably: everything upstream of a mixing desk
strip is mono, everything from the desk out is stereo. Where one function
genuinely takes either -- compositing is the same arithmetic at both shapes
-- the signature says `Bus | StereoBus` and means it, rather than inventing a
parent for two classes with one method in common.
"""

from numbers import Integral

from numpy import copyto

from .block import Block, BlockPool, StereoBlock
from ..constants import BLOCK_SIZE


class _History:
    """A ring of past blocks on one lane, for one operator that asked.

    A delay line reaches backwards, but nothing upstream keeps a recording --
    the graph settles one block and moves on. So an operator that needs the
    signal as it was N samples ago keeps the blocks itself, and this is the
    ring it keeps them in.

    **The ring stores row IDs, not arrays.** A block rotates into history by
    handing over its pool row, and the row it gets back is the one that fell
    off the far end. No samples are copied for a delay that lands on a block
    boundary; the ring is a permutation of who owns which row.

    **Depth is whatever was last asked for.** `delay()` takes the delay every
    block rather than at construction, because the delay time is a register a
    game can write. Growing pads the far end with silence, which is the right
    answer -- the signal genuinely was silent that long ago, as far as this
    lane knows. Shrinking releases the rows that fell out of range.

    One ring per operator per lane. Two delays in one chain see different
    signals -- the second sees what the first did -- so they cannot share.
    """

    def __init__(self, pool: BlockPool):
        self.pool = pool
        #: Oldest at `cursor`, newest behind it. `None` is a slot no block has
        #: reached yet, which reads as silence.
        self.ids: list[int | None] = []
        self.quiescent: list[bool] = []
        self.cursor = 0
        #: Only built for a delay that does NOT land on a block boundary,
        #: where the output straddles two stored blocks and has to be
        #: assembled somewhere. Released the moment the delay becomes
        #: block-aligned again.
        self.scratch: Block | None = None

    def delay(self, current: Block, ticks: int):
        """Replace `current`'s contents with the signal `ticks` samples ago."""
        blocks, offset = divmod(ticks, BLOCK_SIZE)
        # A delay landing mid-block needs one block MORE than the whole ones,
        # because the output straddles two of them.
        depth = blocks + bool(offset)
        self._resize(depth)
        if not self.ids:
            # Zero delay. The lane is its own past; nothing to store.
            self._release_scratch()
            return

        # Rotate: the oldest slot gives up its row and takes the incoming one.
        delayed = self.ids[self.cursor]
        delayed_quiescent = self.quiescent[self.cursor]
        self.ids[self.cursor] = current.id
        self.quiescent[self.cursor] = current.quiescent
        if delayed is None:
            # Nothing has reached this far back yet. Claim a row and call it
            # silence, growing the slab to the next power of two past the
            # depth so a long line costs one reallocation rather than depth.
            current.id = self.pool.claim(1 << depth.bit_length())
            current.data.fill(0.0)
            current.quiescent = True
        else:
            current.id = delayed
            current.quiescent = delayed_quiescent
        self.cursor = (self.cursor + 1) % len(self.ids)

        if not offset:
            self._release_scratch()
            # The output is silent only if the whole ring is: those blocks
            # have not come out yet.
            current.quiescent = current.quiescent and all(self.quiescent)
            return

        # Straddling. `current` is now the OLDER of the two blocks the output
        # spans and `newer` is the one after it, so the answer is the tail of
        # the older followed by the head of the newer. For ticks=600 on a
        # 400-sample grid: the last 200 samples of two-blocks-ago, then the
        # first 200 of one-block-ago.
        if self.scratch is None:
            self.scratch = Block(self.pool)
        newer = self.ids[self.cursor]
        newer_quiescent = self.quiescent[self.cursor]
        self.scratch.data[:offset] = current.data[-offset:]
        if newer is None:
            self.scratch.data[offset:].fill(0.0)
        else:
            self.scratch.data[offset:] = self.pool.slab[newer][:-offset]
        self.scratch.quiescent = current.quiescent and newer_quiescent
        # Swap rather than copy back: the scratch becomes the lane's block and
        # the lane's old row becomes next block's scratch.
        current.swap(self.scratch)
        current.quiescent = current.quiescent and all(self.quiescent)

    def _resize(self, depth: int):
        """Re-length the ring, keeping the newest blocks and dropping the rest."""
        if depth == len(self.ids):
            return

        # Flatten to oldest-first so the ends are unambiguous. The modulo is
        # never reached on an empty ring: `range(0)` does not iterate.
        order = [
            (self.cursor + position) % len(self.ids)
            for position in range(len(self.ids))
        ]
        ordered = [self.ids[index] for index in order]
        ordered_quiescent = [self.quiescent[index] for index in order]
        if depth > len(ordered):
            ordered = [None] * (depth - len(ordered)) + ordered
            ordered_quiescent = (
                [True] * (depth - len(ordered_quiescent)) + ordered_quiescent
            )
        else:
            for block_id in ordered[: len(ordered) - depth]:
                if block_id is not None:
                    self.pool.release(block_id)
            # `ordered[-0:]` is the whole list, not an empty one -- so a ring
            # shrinking to nothing is spelled out rather than sliced.
            ordered = ordered[-depth:] if depth else []
            ordered_quiescent = ordered_quiescent[-depth:] if depth else []
        self.ids = ordered
        self.quiescent = ordered_quiescent
        self.cursor = 0

    def _release_scratch(self):
        if self.scratch is not None:
            self.scratch.release()
            self.scratch = None

    def release(self):
        for block_id in self.ids:
            if block_id is not None:
                self.pool.release(block_id)
        self.ids.clear()
        self.quiescent.clear()
        self.cursor = 0
        self._release_scratch()


class Bus:
    """One current mono `Block`, plus whatever history operators asked for.

    **Released is a state, not a hint.** A bus outlives neither the stage nor
    the connection that owns it, and a disconnected lane's bus is released
    while references to it can still exist elsewhere in a half-updated graph.
    Using one after that raises, rather than reading a pool row that now
    belongs to another voice.
    """

    def __init__(self, pool: BlockPool):
        self.pool = pool
        self.current = Block(pool)
        #: One ring per operator that has asked for one, keyed by the
        #: operator itself. Built on first use: most lanes never delay.
        self._histories: dict[object, _History] = {}
        self._released = False

    def ingest(self, incoming: "Bus"):
        """Take a copy of another bus's current block.

        A copy rather than a swap or a shared reference: the source is some
        upstream stage's settled output, which may be feeding several lanes
        this block. Whoever ingests it is about to run operators that write
        in place, so each consumer needs its own copy or the second one reads
        what the first one did to it.
        """
        self._ensure_live()
        copyto(self.current.data, incoming.current.data)
        self.current.quiescent = incoming.current.quiescent

    def clear(self):
        self._ensure_live()
        self.current.data.fill(0.0)
        self.current.quiescent = True

    def delay(self, owner: object, ticks: int):
        """Replace this bus's current block with the signal `ticks` ago.

        `owner` is the operator asking, and it keys its own private ring --
        two delays in one chain see different signals, so they cannot share
        one. Passing `self` is the whole convention.
        """
        self._ensure_live()
        if not isinstance(ticks, Integral) or isinstance(ticks, bool):
            raise TypeError("delay must be an integer number of ticks")
        ticks = int(ticks)
        if ticks < 0:
            raise ValueError("delay cannot be negative")
        history = self._histories.get(owner)
        if history is None:
            history = self._histories[owner] = _History(self.pool)
        history.delay(self.current, ticks)

    def release(self):
        self._ensure_live()
        for history in self._histories.values():
            history.release()
        self._histories.clear()
        self.current.release()
        self._released = True

    def _ensure_live(self):
        if self._released:
            raise RuntimeError("cannot use a released Bus")


class StereoBus:
    """The mixing desk's current stereo block.

    **It has no history and cannot be delayed**, deliberately. Everything
    that wants to look backwards -- a delay line, an echo, a reverb's comb
    -- is an operator, and operators live upstream where the signal is still
    mono. By the time audio is stereo it is on its way to the device.
    """

    def __init__(self):
        self.current = StereoBlock()

    def clear(self):
        self.current.data.fill(0.0)
        self.current.quiescent = True
