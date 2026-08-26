"""The frame's staging buffer: every observation, in call order, unsorted.

Metric writes do not go to their streams. They land here, and the frame
boundary fans them out (`Metrics.advance`). Three reasons, in order of how
much they mattered:

* **The hot path gets cheaper.** An append is ~140ns against ~360ns for a
  ring update done in place, and ~2100ns through the pyglet EventBus (which
  was measured and rejected -- see the package docstring).
* **Frame coherence is free.** Everything in the buffer belongs to the same
  frame BY CONSTRUCTION, so no caller ever passes, holds, or looks up a
  frame number. Only the flush knows one, once.
* **The cost moves to a boundary you choose.** Fan-out happens at `advance`,
  not scattered through the work being measured.

Fixed capacity, deliberately. A game looping on `observe()` hits a bound and
increments `dropped` rather than growing this buffer without limit inside a
frame; a metrics system that can exhaust memory when misused is worse than
one that tells you it is being misused.
"""

from array import array


class MetricBuffer:
    """One frame's observations as parallel (id, value) arrays."""

    __slots__ = ("_ids", "_values", "_capacity", "_count", "_dropped")

    def __init__(self, capacity: int = 256) -> None:
        if capacity < 1:
            raise ValueError("metric buffer capacity must be positive")
        self._capacity = capacity
        # Parallel rather than interleaved so ids stay integers instead of
        # riding in a float array. Both are packed C storage -- no per-entry
        # Python objects, which is the whole point of not using a list.
        self._ids = array("L", [0]) * capacity
        self._values = array("d", [0.0]) * capacity
        self._count = 0
        self._dropped = 0

    def log(self, metric_id: int, value: float) -> None:
        """Record one observation. The hot path; keep it boring."""
        count = self._count
        if count < self._capacity:
            self._ids[count] = metric_id
            self._values[count] = value
            self._count = count + 1
        else:
            self._dropped += 1

    @property
    def count(self) -> int:
        """Observations staged for the open frame."""
        return self._count

    @property
    def dropped(self) -> int:
        """Observations lost to capacity since the engine started."""
        return self._dropped

    def drain(self):
        """Yield `(metric_id, value)` for the open frame, then reset.

        Returns the raw arrays plus a length rather than a generator: the
        caller is the flush loop, and a generator would allocate and box on
        a path that runs every frame.
        """
        count = self._count
        self._count = 0
        return self._ids, self._values, count

    def clear(self) -> None:
        """Discard the open frame's observations without recording them."""
        self._count = 0
