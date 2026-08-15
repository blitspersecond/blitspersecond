from numbers import Integral

from numpy import bool_, copyto, float32, ndarray, zeros

from .constants import FRAME_SIZE


class FramePool:
    """One growing float32 slab addressed by stable claimed row IDs."""

    def __init__(self):
        self.slab = zeros((0, FRAME_SIZE), dtype=float32)
        self.claimed = zeros(0, dtype=bool_)
        self.count = 0
        self._released_hint = None
        self._released_count = 0

    def claim(self, block: int = 1) -> int:
        if block < 1:
            raise ValueError("claim block must be positive")
        if self._released_hint is not None:
            frame_id = self._released_hint
            self._released_hint = None
            self.claimed[frame_id] = True
            self._released_count -= 1
            return frame_id

        if self._released_count:
            for frame_id, claimed in enumerate(self.claimed[: self.count]):
                if not claimed:
                    self.claimed[frame_id] = True
                    self._released_count -= 1
                    return frame_id

        if self.count == len(self.slab):
            old = len(self.slab)
            grown = zeros((old + block, FRAME_SIZE), dtype=float32)
            grown[:old] = self.slab
            self.slab = grown
            claimed = zeros(old + block, dtype=bool_)
            claimed[:old] = self.claimed
            self.claimed = claimed

        frame_id = self.count
        self.count += 1
        self.claimed[frame_id] = True
        return frame_id

    def release(self, frame_id: int):
        if frame_id < 0 or frame_id >= self.count:
            raise IndexError(f"frame id {frame_id} does not exist")
        if not self.claimed[frame_id]:
            raise ValueError(f"frame id {frame_id} is already released")
        self.claimed[frame_id] = False
        self._released_hint = frame_id
        self._released_count += 1


class Frame:
    """One owned FramePool row, resolved by ID so slab growth stays safe."""

    def __init__(self, pool: FramePool):
        self.pool = pool
        self.id = pool.claim()
        self.quiescent = True
        self._released = False

    @property
    def data(self) -> ndarray:
        if self._released:
            raise RuntimeError("cannot use a released Frame")
        return self.pool.slab[self.id]

    @data.setter
    def data(self, value: ndarray):
        copyto(self.data, value)

    def swap(self, other: "Frame"):
        if self.pool is not other.pool:
            raise ValueError("cannot swap Frames from different pools")
        self.id, other.id = other.id, self.id
        self.quiescent, other.quiescent = other.quiescent, self.quiescent

    def release(self):
        if self._released:
            raise RuntimeError("Frame is already released")
        self.pool.release(self.id)
        self._released = True


class StereoFrame:
    """One persistent MixingDesk stereo Frame outside the mono FramePool."""

    def __init__(self):
        self.data = zeros((FRAME_SIZE, 2), dtype=float32)
        self.quiescent = True

    def swap(self, other: "StereoFrame"):
        if not isinstance(other, StereoFrame):
            raise TypeError("can only swap stereo Frames")
        self.data, other.data = other.data, self.data
        self.quiescent, other.quiescent = other.quiescent, self.quiescent


class StereoBus:
    """The MixingDesk's current stereo Frame; it deliberately has no history."""

    def __init__(self):
        self.current = StereoFrame()

    def clear(self):
        self.current.data.fill(0.0)
        self.current.quiescent = True


class _History:
    """A lazily populated circular history of owned FramePool row IDs."""

    def __init__(self, pool: FramePool):
        self.pool = pool
        self.ids: list[int | None] = []
        self.quiescent: list[bool] = []
        self.cursor = 0
        self.scratch: Frame | None = None

    def delay(self, current: Frame, ticks: int):
        frames, offset = divmod(ticks, FRAME_SIZE)
        depth = frames + bool(offset)
        self._resize(depth)
        if not self.ids:
            self._release_scratch()
            return

        delayed = self.ids[self.cursor]
        delayed_quiescent = self.quiescent[self.cursor]
        self.ids[self.cursor] = current.id
        self.quiescent[self.cursor] = current.quiescent
        if delayed is None:
            current.id = self.pool.claim(1 << depth.bit_length())
            current.data.fill(0.0)
            current.quiescent = True
        else:
            current.id = delayed
            current.quiescent = delayed_quiescent
        self.cursor = (self.cursor + 1) % len(self.ids)

        if not offset:
            self._release_scratch()
            current.quiescent = (
                current.quiescent and all(self.quiescent)
            )
            return

        if self.scratch is None:
            self.scratch = Frame(self.pool)
        newer = self.ids[self.cursor]
        newer_quiescent = self.quiescent[self.cursor]
        self.scratch.data[:offset] = current.data[-offset:]
        if newer is None:
            self.scratch.data[offset:].fill(0.0)
        else:
            self.scratch.data[offset:] = self.pool.slab[newer][:-offset]
        self.scratch.quiescent = current.quiescent and newer_quiescent
        current.swap(self.scratch)
        current.quiescent = current.quiescent and all(self.quiescent)

    def _resize(self, depth: int):
        if depth == len(self.ids):
            return

        order = [
            (self.cursor + offset) % len(self.ids)
            for offset in range(len(self.ids))
        ]
        ordered = [self.ids[index] for index in order]
        ordered_quiescent = [self.quiescent[index] for index in order]
        if depth > len(ordered):
            ordered = [None] * (depth - len(ordered)) + ordered
            ordered_quiescent = (
                [True] * (depth - len(ordered_quiescent))
                + ordered_quiescent
            )
        else:
            retired = ordered[: len(ordered) - depth]
            for frame_id in retired:
                if frame_id is not None:
                    self.pool.release(frame_id)
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
        for frame_id in self.ids:
            if frame_id is not None:
                self.pool.release(frame_id)
        self.ids.clear()
        self.quiescent.clear()
        self.cursor = 0
        self._release_scratch()


class Bus:
    """One current Frame plus lane-local history requested by Operators."""

    def __init__(self, pool: FramePool):
        self.pool = pool
        self.current = Frame(pool)
        self._histories = {}
        self._released = False

    def ingest(self, incoming: "Bus"):
        self._ensure_live()
        copyto(self.current.data, incoming.current.data)
        self.current.quiescent = incoming.current.quiescent

    def clear(self):
        self._ensure_live()
        self.current.data.fill(0.0)
        self.current.quiescent = True

    def delay(self, owner: object, ticks: int):
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
