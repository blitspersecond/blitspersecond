from blitspersecond.common import SingletonMeta

from .duration import DurationMetric
from .pace import PaceMetric


class Metrics(metaclass=SingletonMeta):
    def __init__(self):
        self._pace = PaceMetric()
        self._render = DurationMetric()

    @property
    def pace(self) -> PaceMetric:
        """Simulation pace: per-tick window for HUDs (average/max), the
        per-game-second Pace log line, and the end-of-session report."""
        return self._pace

    @property
    def render(self) -> DurationMetric:
        """Compose-step duration (seconds) -- how long the render path takes to
        draw all layers into the framebuffer, excluding vsync/present wait."""
        return self._render
