"""Is the compositor still taking frames from us?"""

from enum import Enum, auto


class FlipHealth(Enum):
    OK = auto()  # presentations are being accepted
    STALLED = auto()  # presentations are being withheld (occluded/minimised)


class PresentationMonitor:
    """Classify each flip() duration to spot withheld presentations.

    This used to answer a much larger question -- whether the swap was pacing
    the loop at all -- because simulation timing was derived from
    presentation timing. It is not any more: the engine holds the authored
    rate on its own clock and every path presents on a deadline grid, so a
    swap that returns instantly is the normal case rather than evidence of
    anything. See `docs/bps/DECOUPLED_PRESENTATION.md`.

    What remains is the one direction that still matters. A compositor may
    withhold presentations entirely when a window is occluded or minimised --
    observed as roughly 2s grants under Mutter -- and feeding a stalled swap
    chain is wasted work. Detecting that got SIMPLER when blocking vsync went
    away: with nothing legitimately blocking in flip(), a long swap has only
    one explanation, so no streak reasoning is needed to separate it from an
    honest vsync wait.

    A streak is still required, but for a smaller reason: one slow flip is a
    hiccup, and treating it as a background transition would make brief
    stutter into a state change.
    """

    def __init__(
        self,
        stall_threshold: float = 0.25,
        stall_streak: int = 2,
    ) -> None:
        self._stall_threshold = stall_threshold
        self._stall_streak = stall_streak
        self._health = FlipHealth.OK
        self._stall_run = 0

    def reset(self) -> None:
        """Forget prior flip streaks after presentation is restarted."""
        self._health = FlipHealth.OK
        self._stall_run = 0

    def observe(self, flip_elapsed: float) -> FlipHealth:
        """Feed one flip() duration; returns the current health verdict."""
        if flip_elapsed > self._stall_threshold:
            self._stall_run += 1
        else:
            self._stall_run = 0
        self._health = (
            FlipHealth.STALLED
            if self._stall_run >= self._stall_streak
            else FlipHealth.OK
        )
        return self._health
