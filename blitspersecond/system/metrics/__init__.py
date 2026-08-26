"""What the engine measures about itself, and how much of it it keeps.

    from blitspersecond.system import Metrics

    SPRITES = Metrics().counter("game.sprites", "Sprites drawn per frame")
    SPRITES.push(len(batch))

Two metric types (`Counter`, `Gauge`), one staging buffer per frame, and
three cascading rings per metric. Nothing here drives the engine: metrics
are written and never read back to decide anything, which is why they live
under `system` rather than inside the subsystems they observe.

Counters and gauges, named and declared the way Prometheus names and
declares things. That is where the vocabulary came from and it is the whole
of the debt -- this is not a Prometheus client and does not try to be. A
read here means "over the last N frames, seconds or minutes", never "since
the process started", and there is no exposition endpoint because the
consumer is a HUD or a session report in this same process.

**Everything is indexed by the FRAME.** One frame index, owned by the loop,
incremented once per game frame, and every metric shares it -- so slot N
means the same frame in every stream, a sparse metric stays aligned against
a dense one, and a report can say "frame 41203: gameloop 22ms, swap 3ms".
Wall time is something recorded per frame, not the axis recorded against.

**A frame nobody measured reads as NaN, never as zero.** Absence and zero
are different facts and a ring that conflates them is lying about data it
does not have. Writing NaN costs the same as writing zero, so there is
nothing to buy by accepting that.

The path a number takes:

    Counter.push / Gauge.push          buffer.py    ~140ns, an append
        |                                           frame coherence is free:
        |                                           the buffer IS the frame
        v
    Metrics.advance()                  metrics.py   once per game frame,
        |                                           called by Loop
        v
    CounterStream / GaugeStream        stream.py    1024 frames -> 512
        |                                           seconds -> 512 minutes,
        |                                           folded on eviction
        v
    Report                             report.py    percentiles, pace,
                                                    session worst-case

Costs, measured on a Ryzen 7 5700X3D under CPython 3.14, for a frame
carrying 40 observations across 24 metrics:

    push (staging append, x40)      5.60us   inside the work being measured
    cascade + clear (24 metrics)    4.79us   at the frame boundary
    applying datums to slots       16.55us   at the frame boundary
                                   -------
                                   26.94us   0.39% of a 144Hz frame

Two designs measured and rejected, recorded because both look reasonable
until you measure them:

* **The pyglet EventBus** costs 2106ns per dispatch against 53ns for a
  direct call. 676ns of that is an `AttributeError` raised and caught on
  EVERY dispatch (the dispatcher checks itself for an instance handler; a
  bus never has one), 329ns is a linear scan of a 29-entry list in an
  assert, and 96ns is a fresh list allocated per dispatch. It also
  short-circuits on `EVENT_HANDLED`, so one consumer returning truthy would
  silently swallow a sample from every consumer below it. At 2,000 blits in
  a frame, a blit counter through it would cost 61% of a 144Hz frame. (Its
  docstring's claim of "effectively zero overhead while nothing is
  subscribed" is also wrong: an unsubscribed dispatch still costs 1532ns.)
* **NumPy for the rings.** Scalar element access boxes a Python float on
  every touch: 722ns/push against 378ns for `array.array('d')`, and 842ns
  for a 2D row view. `system.frame_pacing` is fast with NumPy because it
  writes a whole structured row in ONE assignment and reads in bulk -- the
  opposite access pattern to this. `np.frombuffer` over `array.array` is
  genuinely zero-copy, so `report.py` still gets the bulk half for free.

And the boundary against the other instrumentation in this package:
`system.frame_pacing` is TRACING -- one bounded row per present, written to
CSV for an experiment, capturing things this cannot (`pacing_mode`,
`flip_health`, the phase breakdown within an iteration). It answers "what
exactly happened in this capture"; this answers "what is happening now, and
what has it been doing". Neither replaces the other and neither should grow
into the other.
"""

from .buffer import MetricBuffer
from .metric import Counter, Gauge, Metric
from .metrics import Metrics
from .report import MetricSummary, Report, carry_forward
from .stream import FRAMES, MINUTES, SECONDS, CounterStream, GaugeStream

__all__ = [
    "Counter",
    "CounterStream",
    "FRAMES",
    "Gauge",
    "GaugeStream",
    "MINUTES",
    "Metric",
    "MetricBuffer",
    "MetricSummary",
    "Metrics",
    "Report",
    "SECONDS",
    "carry_forward",
]
