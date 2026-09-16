"""One slab of audio memory, and the handles that own a row of it.

Audio allocates nothing per block. At 120 advances a second across however
many voices are sounding, an array built per operator per block is the one
shape that turns synthesis into a garbage collection problem -- so every
mono signal in the engine is a row of ONE float32 slab, handed out by
`BlockPool` and owned by a `Block`.

A `Block` is a stable integer handle, not an array. It resolves its row on
every access, because claiming past the end of the slab reallocates it and
anything holding the old array would be writing into memory the pool no
longer reads.
"""

from numpy import bool_, copyto, float32, ndarray, zeros

from ..constants import BLOCK_SIZE


class BlockPool:
    """One growing float32 slab, addressed by stable claimed row IDs.

    Grows and never shrinks. A game's voice count settles in the first few
    seconds and then stays there, so returning rows to the operating system
    would mean giving back memory that is about to be asked for again.
    Released rows go on a free stack and are reissued.

    **Row IDs are stable across growth.** That is the whole reason the pool
    hands out an integer rather than an array view: growth replaces the slab,
    and a view into the old one would keep working, silently, against memory
    nothing plays.
    """

    def __init__(self):
        self.slab = zeros((0, BLOCK_SIZE), dtype=float32)
        #: Per row, whether it is currently owned. Not used to FIND a free
        #: row -- the free stack does that -- only to catch a row being
        #: released twice, which would hand the same memory to two owners and
        #: mix two voices into one buffer with nothing raised.
        self.claimed = zeros(0, dtype=bool_)
        self.count = 0
        #: Released rows, newest first. A stack rather than a queue so that a
        #: claim/release pair reissues the same row, and so the sequence of
        #: IDs a given graph produces is reproducible between runs.
        self._free: list[int] = []

    def claim(self, growth: int = 1) -> int:
        """Take a row, growing the slab by `growth` rows if none are free.

        `growth` is how far to grow WHEN the slab is full, not how many rows
        to return -- a claim is always exactly one row. Callers that know
        they are about to want a run of them (a delay line sizing its
        history) pass a larger number to buy that growth in one reallocation
        instead of one per row.
        """
        if growth < 1:
            raise ValueError("claim growth must be positive")
        if self._free:
            block_id = self._free.pop()
            self.claimed[block_id] = True
            return block_id

        if self.count == len(self.slab):
            old = len(self.slab)
            grown = zeros((old + growth, BLOCK_SIZE), dtype=float32)
            grown[:old] = self.slab
            self.slab = grown
            claimed = zeros(old + growth, dtype=bool_)
            claimed[:old] = self.claimed
            self.claimed = claimed

        block_id = self.count
        self.count += 1
        self.claimed[block_id] = True
        return block_id

    def release(self, block_id: int):
        """Return a row to the free stack.

        Takes a raw ID because delay histories own rows without a `Block`
        around each one. Everything else releases through `Block.release`.
        """
        if block_id < 0 or block_id >= self.count:
            raise IndexError(f"block id {block_id} does not exist")
        if not self.claimed[block_id]:
            raise ValueError(f"block id {block_id} is already released")
        self.claimed[block_id] = False
        self._free.append(block_id)


class Block:
    """One owned pool row: `BLOCK_SIZE` float32 samples of one mono signal.

    **`data` is resolved per access, never held.** Caching it is the bug this
    class exists to prevent: the array it returns is invalidated by any claim
    that grows the slab, which can happen on another voice, in another part
    of the graph, between two lines of the operator holding it.

    Reading or writing after `release()` raises rather than returning the row
    -- which by then may belong to somebody else, and would otherwise be
    corruption with no exception attached to it.
    """

    def __init__(self, pool: BlockPool):
        self.pool = pool
        self.id = pool.claim()
        #: Whether this block is silent AND settled -- nothing is sounding
        #: and no retained state is still decaying, so the graph above it can
        #: be skipped until something wakes it. Not the same as "all zeros":
        #: a block of silence from a filter whose state is still ringing is
        #: not quiescent. Operators that can prove their own answer set it;
        #: the default is to stay awake.
        self.quiescent = True
        self._released = False

    @property
    def data(self) -> ndarray:
        if self._released:
            raise RuntimeError("cannot use a released Block")
        return self.pool.slab[self.id]

    @data.setter
    def data(self, value: ndarray):
        copyto(self.data, value)

    def swap(self, other: "Block"):
        """Exchange rows with another Block, moving no samples.

        How a signal reaches a Stage output without a copy: the two handles
        trade IDs, so what was written into a lane's row IS the output's row
        afterwards. Both handles stay valid and both still own a row.
        """
        if self.pool is not other.pool:
            raise ValueError("cannot swap Blocks from different pools")
        self.id, other.id = other.id, self.id
        self.quiescent, other.quiescent = other.quiescent, self.quiescent

    def release(self):
        if self._released:
            raise RuntimeError("Block is already released")
        self.pool.release(self.id)
        self._released = True


class StereoBlock:
    """`BLOCK_SIZE` interleaved stereo sample pairs: `(BLOCK_SIZE, 2)` float32.

    Shares a name with `Block` and almost nothing else. It owns its array
    outright instead of renting a pool row, because there is exactly one of
    these per mixing desk strip plus one per desk -- a handful for the whole
    engine, against a row per lane per voice on the mono side. A pool earns
    its keep at the second scale and is pure indirection at the first.

    Stereo appears only at the very end of the graph. Everything upstream is
    mono, and a desk strip is where a mono signal is placed between the two
    speakers, so nothing before that point has a second channel to carry.
    """

    def __init__(self):
        self.data = zeros((BLOCK_SIZE, 2), dtype=float32)
        self.quiescent = True

    def swap(self, other: "StereoBlock"):
        """Exchange arrays with another stereo block, moving no samples.

        Swaps the array itself rather than a row ID -- with no slab to be
        reallocated there is nothing an array reference can go stale against,
        which is the one problem `Block`'s indirection exists to solve.
        """
        if not isinstance(other, StereoBlock):
            raise TypeError("can only swap stereo blocks")
        self.data, other.data = other.data, self.data
        self.quiescent, other.quiescent = other.quiescent, self.quiescent
