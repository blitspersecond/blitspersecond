import numpy as np
from collections import deque
from .config import Config


class Metrics(object):
    _MAX_RECORDS = 100

    def __init__(self):
        self.max_records = self._MAX_RECORDS
        self.records = deque(maxlen=self.max_records)
        self._last_dt = 0.0
        self.target_fps = Config().window.framerate
        self.target_dt = 1.0 / self.target_fps

    def __call__(self, dt: float) -> None:
        """Add a new delta time when the object is called."""
        if dt > 0:  # Only record positive delta times
            self._last_dt = dt
            self.records.append(dt)

    @property
    def last_dt(self) -> float:
        """Return the last delta time."""
        return self._last_dt

    @property
    def last_fps(self) -> float:
        """Return the FPS calculated from the last delta time."""
        return 1.0 / self._last_dt if self._last_dt > 0 else 0.0

    @property
    def percentile_99(self) -> float:
        """Return the 99th percentile of the recorded delta times (in FPS)."""
        if len(self.records) > 0:
            fps_values = [1.0 / dt for dt in self.records if dt > 0]
            if fps_values:  # Ensure there are valid FPS values
                return np.percentile(fps_values, 99)
        return 0.0

    @property
    def target_fps_delta(self) -> float:
        """Return the target delta time for the desired FPS."""
        return self.target_dt

    def __len__(self) -> int:
        """Return the number of recorded delta times."""
        return len(self.records)
