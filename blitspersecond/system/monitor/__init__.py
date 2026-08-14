"""The instrument panel: what happened (Logger) and how fast (Metrics).

    from blitspersecond.system import Logger, Metrics

Both singletons, both passive observers -- nothing here drives the engine.
`Logger` wraps Python logging with colored console output (`LogLevel` sets the
floor). `Metrics` holds the engine's gauges: `.pace` (simulation pace -- the
engine pushes one sample per fixed tick, every game-second of pushes emits the
Pace log line, and the engine asks for the session pace report at loop exit)
and `.render` (compose-step duration -- draw cost only, never vsync wait, so
composition can't masquerade as a presentation stall).

The gauge machinery (PaceMetric, DurationMetric, the Metric ABC in abc.py)
lives in this package's other modules; user code meets only the singletons
and the Metric read surface (average/max/push).
"""

from .abc import Metric
from .logger import Logger, LogLevel
from .metrics import Metrics

__all__ = [
    "Logger",
    "LogLevel",
    "Metric",
    "Metrics",
]
