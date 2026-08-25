"""Health classification for presentation's blocking swap signal."""

from enum import Enum, auto


class FlipHealth(Enum):
    PROBING = auto()  # fast flips seen, but not enough to prove unthrottled
    OK = auto()  # flip blocked for a plausible presentation interval
    UNTHROTTLED = auto()  # flip is non-blocking (expected behind Win32/DWM)
    STALLED = auto()  # presentations are being withheld (occluded/minimised)


class PresentationMonitor:
    """Classify each flip() duration against the expected presentation pacing.

    The presentation-locked design assumes one loop iteration per display
    refresh. That assumption can break in two directions, and a single
    anomalous flip is evidence of neither:

    * UNTHROTTLED -- flip returns near-instantly, *sustained*. The swap is not
      pacing the loop: this is expected behind Win32/DWM and can also mean a
      driver override or headless mode elsewhere. Without deadline pacing the
      loop would spin at kHz and fast-forward the simulation. A lone fast flip
      is normal -- a cheap frame with swap-queue slack, most visibly the first
      frame of a run -- so this requires ``fast_streak`` consecutive flips.

    * STALLED -- flip blocks for far longer than any refresh interval,
      *sustained*. The compositor is withholding presentations (window
      occluded or minimised; observed as ~2s grants under Mutter), so
      cadence-driven simulation would slow to a crawl. A lone slow flip is a
      genuine brief stall and by design produces brief slowdown rather than
      a background transition, so this requires ``stall_streak`` consecutive
      slow flips.

    A short run of fast flips is PROBING rather than OK. The engine holds the
    current simulation state during that ambiguity: queue slack does not prove
    that a physical presentation completed, and a genuinely unthrottled backend
    must not fast-forward the game while the monitor gathers evidence.

    Once UNTHROTTLED is proven it is sticky until reset(). Deadline pacing moves
    the wait outside flip(), so swap duration can no longer prove that blocking
    vsync returned. Foreground restoration resets the monitor and deliberately
    re-detects the backend.
    """

    def __init__(
        self,
        fast_threshold: float = 0.001,
        fast_streak: int = 5,
        stall_threshold: float = 0.25,
        stall_streak: int = 2,
        initial_health: FlipHealth = FlipHealth.PROBING,
    ) -> None:
        self._fast_threshold = fast_threshold
        self._fast_streak = fast_streak
        self._stall_threshold = stall_threshold
        self._stall_streak = stall_streak
        self._initial_health = initial_health
        self._health = initial_health
        self._fast_run = 0
        self._stall_run = 0

    def reset(self) -> None:
        """Forget prior flip streaks after presentation is restarted."""
        self._health = self._initial_health
        self._fast_run = 0
        self._stall_run = 0

    def observe(self, flip_elapsed: float) -> FlipHealth:
        """Feed one flip() duration; returns the current health verdict."""
        if flip_elapsed < self._fast_threshold:
            self._fast_run += 1
        else:
            self._fast_run = 0

        if flip_elapsed > self._stall_threshold:
            self._stall_run += 1
        else:
            self._stall_run = 0

        if self._stall_run >= self._stall_streak:
            self._health = FlipHealth.STALLED
        elif self._health is FlipHealth.UNTHROTTLED:
            # Deadline pacing deliberately makes flip() fast. Only an explicit
            # presentation restart can make its duration diagnostic again.
            pass
        elif self._fast_run >= self._fast_streak:
            self._health = FlipHealth.UNTHROTTLED
        elif flip_elapsed < self._fast_threshold:
            self._health = FlipHealth.PROBING
        elif self._health is not FlipHealth.STALLED:
            self._health = FlipHealth.OK
        return self._health
