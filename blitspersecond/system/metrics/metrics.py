"""The registry: declare metrics, drive the frame boundary, hand out reports.

One `MetricBuffer` for the open frame, one stream per declared metric, and
one frame index. The frame index is THE source of truth for the whole
subsystem: every stream indexes off it rather than counting its own pushes,
so slot N means the same frame in every metric. That is what makes a report
able to say "frame 41203: gameloop 22ms, swap 3ms" at all -- metrics
counting their own pushes would drift apart within seconds, and a sparse
metric would silently misalign against a dense one.

The loop owns when it turns. `Loop` calls `advance()` once per game frame,
which is also what makes the index start at RUNNING rather than during
construction: warm-up opens no window and no display, so slow hardware
startup cannot appear as a stalled first frame. Same rule the presentation
baselines already follow.
"""

from typing import Callable, Dict, List, TypeVar, Union

from blitspersecond.common import SingletonMeta

from .buffer import MetricBuffer
from .metric import Counter, Gauge, Metric
from .report import Report
from .stream import CounterStream, GaugeStream, frame_slot

Stream = Union[CounterStream, GaugeStream]

#: Bound to `Metric` so `_declare` returns whatever type it was asked to
#: build. A `cast` at each call site would have said the same thing, but a
#: cast is a promise the checker takes on trust; this is one it verifies.
MetricType = TypeVar("MetricType", bound=Metric)


class Metrics(metaclass=SingletonMeta):
    """Engine-wide metric registry and frame-boundary flush."""

    def __init__(self, framerate: int, capacity: int = 256) -> None:
        # `framerate` is the frames-to-seconds fold ratio, and a singleton
        # takes its arguments from whoever constructs it FIRST. The engine
        # does, in its own constructor, before any subsystem can declare
        # anything.
        #
        # **It has no default, deliberately.** A module-level `Metrics()` in
        # something imported earlier would otherwise fix the ratio at a
        # guess, and every per-second figure would be wrong by the ratio it
        # guessed wrong by -- with nothing raised and nothing to notice.
        # Without a default that import fails loudly instead. The rate is an
        # authoring choice from `fps` (15, 20, 30, 40, 60), so there is no
        # value that could be right to guess; `bps.metrics` is how a
        # subsystem reaches the one the engine built.
        self._framerate = framerate
        self._buffer = MetricBuffer(capacity)
        self._streams: List[Stream] = []
        self._metrics: List[Metric] = []
        self._by_name: Dict[str, Metric] = {}
        self._frame = 0

        # Engine-level metrics are declared here rather than at their push
        # sites because they are the engine's own, and a reader looking for
        # "what does this engine measure" should find the list in one place.
        # Subsystems with their own vocabulary (audio, input) declare theirs
        # next to the code that pushes them, via `counter()`/`gauge()`.
        #
        # NOT YET WIRED, as of 2026-08-26 -- both read as all-NaN, which is
        # the honest answer for a metric nothing has measured, so declaring
        # ahead of the push site costs nothing and misleads no one:
        #
        #   display.compose.ms   -- push site is `Display._compose`, still
        #                           commented out by the graphics rebuild.
        #                           The call is written and parked there.
        #   display.deadline.ms  -- the value exists as `deadline_error` in
        #                           `display.internal.pacing.session.present`
        #                           but is only handed to the frame-pacing
        #                           tracer. Wiring it means deciding how
        #                           `pacing` reaches metrics: it takes its
        #                           tracer through the `FramePacingSink`
        #                           protocol precisely so `display/` imports
        #                           no instrumentation, and that inversion
        #                           should not be quietly abandoned for this.
        self._frame_ms = self.gauge(
            "engine.frame.ms",
            "Wall seconds between successive game ticks: the engine clock "
            "itself. Should sit on the PACED period with only scheduler "
            "resolution around it -- 16.667ms at 60fps, or a hair more where "
            "the pacer re-timed its second to suit the panel (16.683ms on "
            "59.94Hz; see internal/pacing/accepted_rate.py) -- because it "
            "anchors each tick to the frame's own evenly-spaced deadline. A "
            "spread of several ms means ticks have picked up the display "
            "grid's quantisation, which is a defect: see display.frame.ms "
            "for the quantity that is SUPPOSED to be uneven.",
        )
        self._display_frame_ms = self.gauge(
            "display.frame.ms",
            "Wall seconds between successive frames reaching the screen. "
            "Bimodal by design and not a fault: a frame can only be shown on "
            "a presentation slot, so at 60fps on a 144Hz panel this "
            "alternates two and three slots (13.9 and 20.8ms) around a "
            "16.667ms mean. That is the cadence, measured.",
        )
        self._gameloop_ms = self.gauge(
            "engine.gameloop.ms",
            "Wall time inside the game's own fixed step, measured directly "
            "around the call. Unlike frame.ms this is never batch-averaged, "
            "so a single slow tick shows up as one.",
        )
        self._swap_ms = self.gauge(
            "display.swap.ms",
            "Seconds the window-system swap blocked, in milliseconds. "
            "Excludes compose and the post-process draw.",
        )
        self._compose_ms = self.gauge(
            "display.compose.ms",
            "Time to draw every enabled layer into the framebuffer. "
            "Excludes vsync and present, so composition cost can never "
            "masquerade as a presentation stall.",
        )
        self._deadline_ms = self.gauge(
            "display.deadline.ms",
            "Signed error against the presentation deadline on the "
            "deadline-grid path. Negative is early, positive is late.",
        )
        self._presents = self.counter(
            "display.presents",
            "Presentations completed during one game frame. On a 144Hz "
            "display running a 60fps simulation this reads 2 or 3 in a "
            "repeating cycle -- 3,2,3,2,2, which is what 'the first slot at "
            "or after the deadline' produces rather than anything stored. "
            "Read the distribution and not the peak.",
        )
        # Named `display.frames.*` in full: a bare "dropped" on a metrics
        # object reads as dropped frames, and there is already a different
        # quantity here that the same word fits -- pushes lost to a full
        # staging buffer, now `observations_dropped`. Two unrelated things
        # sharing one short word is how a reader trusts the wrong number.
        self._frames_overran = self.counter(
            "display.frames.overran",
            "Frames the engine failed to present inside their budgeted "
            "window: the frame went up more than a panel period after its "
            "deadline because building it took too long, so the previous "
            "frame stayed on screen for an extra window. A fault.",
        )
        self._starts_late = self.counter(
            "display.starts.late",
            "Times the engine was called with no reserve left before its "
            "slot -- the pacer's estimate of how long a frame takes was "
            "short. The leading indicator of an overrun.",
        )
        # Input loss, and the two kinds are NOT one counter. The first two are
        # the engine's own staging limit overflowing; the third is the kernel
        # discarding events before we ever read them. Different cause,
        # different remedy, and only the English word is shared.
        self._keyboard_dropped = self.counter(
            "input.keyboard.dropped",
            "Keyboard arrivals discarded because more than the staging limit "
            "landed in one frame. Far above any real typing, so a nonzero "
            "reading means the pump ran without the engine cutting.",
        )
        self._pointer_dropped = self.counter(
            "input.pointer.dropped",
            "Pointer arrivals discarded because more than the staging limit "
            "landed in one frame. A 1000Hz pointer offers about seventeen a "
            "frame at 60Hz, so this is two orders of magnitude clear.",
        )
        self._pad_desyncs = self.counter(
            "input.pads.desyncs",
            "Times the kernel dropped events on a pad and its state had to "
            "be replaced by asking the device. Input was lost; a held button "
            "may read as released in the window that recovers.",
        )
        self._phase_shifts = self.counter(
            "display.phase.shifts",
            "Times the slot grid was nudged a quarter period off a suspected "
            "vblank boundary. A handful over a session is the mechanism "
            "working; frequent shifting means the refresh rate we were told "
            "is not the rate the panel runs at.",
        )

    # -- declaration ---------------------------------------------------------

    def counter(self, name: str, documentation: str) -> Counter:
        """Declare a monotonic tally. Idempotent by name."""
        return self._declare(name, documentation, Counter, CounterStream)

    def gauge(self, name: str, documentation: str) -> Gauge:
        """Declare a measured level. Idempotent by name."""
        return self._declare(name, documentation, Gauge, GaugeStream)

    def _declare(
        self,
        name: str,
        documentation: str,
        metric_type: type[MetricType],
        stream_type: Callable[[int], Stream],
    ) -> MetricType:
        existing = self._by_name.get(name)
        if existing is not None:
            # Re-declaration hands back the same handle rather than raising:
            # module-level declaration means import order decides who gets
            # there first, and that is not something a caller can control.
            if not isinstance(existing, metric_type):
                raise TypeError(
                    f"{name} is already declared as a "
                    f"{type(existing).__name__}, not a {metric_type.__name__}"
                )
            return existing
        metric_id = len(self._metrics)
        metric = metric_type(metric_id, name, documentation, self._buffer)
        self._metrics.append(metric)
        self._streams.append(stream_type(self._framerate))
        self._by_name[name] = metric
        return metric

    def __getitem__(self, name: str) -> Metric:
        return self._by_name[name]

    @property
    def names(self) -> List[str]:
        """Every declared metric, in declaration order."""
        return [metric.name for metric in self._metrics]

    # -- the frame boundary --------------------------------------------------

    @property
    def frame(self) -> int:
        """Game frames serviced since the loop began. The source of truth."""
        return self._frame

    def advance(self) -> None:
        """Close the open frame and open the next one.

        Called once per game frame by `Loop`. Applies everything staged
        since the last call into this frame's slots, steps the index, then
        evicts and clears the slot the new frame will occupy.

        Note what the buffer therefore spans: from one game frame to the
        next, which INCLUDES the presentation work that happened in between.
        That is deliberate -- it is what puts a frame's 2 or 3 swaps in the
        same slot as the tick they were presented for.
        """
        slot = frame_slot(self._frame)
        ids, values, count = self._buffer.drain()
        streams = self._streams
        for index in range(count):
            streams[ids[index]].apply(slot, values[index])

        self._frame += 1
        frame = self._frame
        for stream in streams:
            stream.advance(frame)

    def rebase(self) -> None:
        """Discard the open frame's observations without recording them.

        Called when wall time passed that no frame should be charged for --
        resuming from suspension, or returning from a backgrounded window.
        The frame then reads as NaN: not measured, rather than measured as
        one catastrophically slow frame. The old pace instrument needed
        explicit period bookkeeping to achieve this; here it is what the
        storage already means.
        """
        self._buffer.clear()

    @property
    def observations_dropped(self) -> int:
        """Metric pushes lost to a full staging buffer, not dropped FRAMES.

        Non-zero means some hot path is pushing far more per frame than the
        buffer was sized for, so the readings themselves are incomplete --
        a fault in the instrument, not in what it measures. Frames the engine
        failed to present in time are `display.frames.overran`, which is an
        entirely different fact about an entirely different thing.
        """
        return self._buffer.dropped

    def report(self) -> Report:
        """A read-only view over every stream, for logging or inspection."""
        return Report(self._metrics, self._streams, self._framerate, self._frame)

    # -- engine metrics ------------------------------------------------------

    @property
    def frame_ms(self) -> Gauge:
        return self._frame_ms

    @property
    def display_frame_ms(self) -> Gauge:
        return self._display_frame_ms

    @property
    def gameloop_ms(self) -> Gauge:
        return self._gameloop_ms

    @property
    def swap_ms(self) -> Gauge:
        return self._swap_ms

    @property
    def compose_ms(self) -> Gauge:
        return self._compose_ms

    @property
    def deadline_ms(self) -> Gauge:
        return self._deadline_ms

    @property
    def presents(self) -> Counter:
        return self._presents

    @property
    def frames_overran(self) -> Counter:
        return self._frames_overran

    @property
    def starts_late(self) -> Counter:
        return self._starts_late

    @property
    def phase_shifts(self) -> Counter:
        return self._phase_shifts

    @property
    def keyboard_dropped(self) -> Counter:
        return self._keyboard_dropped

    @property
    def pointer_dropped(self) -> Counter:
        return self._pointer_dropped

    @property
    def pad_desyncs(self) -> Counter:
        return self._pad_desyncs
