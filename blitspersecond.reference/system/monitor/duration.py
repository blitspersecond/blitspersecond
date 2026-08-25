from collections import deque

from blitspersecond.system.config import Config

from .abc import Metric


class DurationMetric(Metric):
    """A rolling window of duration samples in seconds (e.g. compose time).

    Deliberately lean next to PaceMetric: it owns no logging or period
    bookkeeping -- just a fixed-length window you read live (a HUD, a
    periodic logger elsewhere). average/max mirror PaceMetric so the two
    read the same way (seconds; *1000 for ms).
    """

    def __init__(self, window: int = 0):
        # Default window ~= one second of frames.
        self._queue: deque = deque(maxlen=window or Config().display.fps)

    def push(self, sample: float) -> None:
        self._queue.append(sample)

    @property
    def average(self) -> float:
        return sum(self._queue) / len(self._queue) if self._queue else 0.0

    @property
    def max(self) -> float:
        return max(self._queue) if self._queue else 0.0
