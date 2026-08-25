from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from heapq import heappop, heappush
from itertools import count
from numbers import Integral
from queue import Empty, SimpleQueue
from typing import cast

from ..common import Bus, FRAME_SIZE, RegisterWrite
from ..common.bus import FramePool, StereoBus
from .apply_stage import ApplyStage
from .composite_stage import CompositeStage
from .connection import _Connection
from .mixing_desk import MixingDesk
from .program import Program
from .source_stage import SourceStage
from .stage import Stage


@dataclass(frozen=True, slots=True)
class _RelativeWrite:
    dt: int
    stage: Stage
    values: tuple[tuple[str, object], ...]
    connection: _Connection | None = None


class AudioEngine:
    """Construct Stages, own the sample clock, and demand the output Stage."""

    def __init__(self):
        self.pool = FramePool()
        self._clock = 0
        self._running = False
        self._inbox = SimpleQueue()
        self._write_order = count()
        self._pending_writes = []
        self._writes_at: int | None = None
        self._current_writes: tuple[RegisterWrite, ...] = ()
        # The device always has one terminal stereo Stage to pull. Until the
        # game connects anything, the empty MixingDesk settles silence.
        self._output = MixingDesk(self)

    @property
    def clock(self) -> int:
        return self._clock

    @property
    def frame(self) -> int:
        return self._clock // FRAME_SIZE

    @property
    def writes(self) -> tuple[RegisterWrite, ...]:
        if self._writes_at != self.frame:
            self._prepare_writes()
        return self._current_writes

    @property
    def _prepared_writes(self) -> tuple[RegisterWrite, ...]:
        """Current writes without resolving an unstarted Frame."""
        if self._writes_at != self.frame:
            return ()
        return self._current_writes

    @property
    def output(self) -> Stage:
        return self._output

    @output.setter
    def output(self, stage: Stage):
        self._assert_topology_mutable()
        if stage._engine is not self:
            raise ValueError("output Stage belongs to another AudioEngine")
        self._output = stage

    def _assert_topology_mutable(self) -> None:
        if self._running:
            raise RuntimeError(
                "audio topology cannot change while AudioEngine is driven"
            )

    def source(self, program: Program | None = None) -> SourceStage:
        return SourceStage(self, program)

    def composite(self, program: Program | None = None) -> CompositeStage:
        return CompositeStage(self, program)

    def apply(self, program: Program) -> ApplyStage:
        return ApplyStage(self, program)

    def mixing_desk(self) -> MixingDesk:
        return MixingDesk(self)

    def schedule(
        self,
        events: Iterable[
            tuple[int, Stage, Mapping[str, object]]
            | tuple[int, Stage, Stage, Mapping[str, object]]
        ],
    ):
        """Schedule one relative timeline at the next unresolved Frame.

        Every ``dt`` is a non-negative sample offset from the same boundary.
        The audio thread chooses that boundary when it admits the complete
        batch, so callers never race the moving audio clock. A one-event batch
        and a complete score follow exactly the same path. Three-item events
        address Stage registers; four-item events add the connected source
        Stage and address that input lane.
        """
        batch = []
        for event in events:
            source: Stage | None = None
            if len(event) == 3:
                dt, stage, values = event
                connection = None
            elif len(event) == 4:
                dt, stage, source, values = event
                connection = None
            else:
                raise ValueError("scheduled event must contain 3 or 4 items")
            if not isinstance(dt, Integral) or isinstance(dt, bool):
                raise TypeError("scheduled dt must be an integer number of samples")
            if dt < 0:
                raise ValueError("scheduled dt cannot be negative")
            if not isinstance(stage, Stage) or stage._engine is not self:
                raise ValueError("register write references a foreign Stage")
            if len(event) == 4:
                if source is None or source._engine is not self:
                    raise ValueError("register write references a foreign input Stage")
                connection = stage._connection(source)
            if not isinstance(values, Mapping):
                raise TypeError("scheduled register values must be a mapping")
            if not values:
                raise ValueError("register write has no values")

            normalized = {}
            specs = (
                stage._register_specs
                if connection is None
                else connection.write_specs
            )
            for name, value in values.items():
                if name not in specs:
                    target = "Stage" if connection is None else "Stage input"
                    raise ValueError(f"{target} has no register {name!r}")
                normalized[name] = specs[name].normalize(name, value)
            write = _RelativeWrite(
                int(dt),
                stage,
                tuple(normalized.items()),
                connection,
            )
            batch.append((next(self._write_order), write))

        if not batch:
            raise ValueError("schedule has no events")
        self._inbox.put(tuple(batch))

    def _prepare_writes(self):
        if self._writes_at == self.frame:
            return

        # The current clock is the first unresolved sample. A whole submitted
        # timeline is translated across that boundary together, preserving all
        # of its relative timing. Anything submitted after this drain waits for
        # the following Frame rather than altering the one being resolved.
        boundary = self._clock
        while True:
            try:
                batch = self._inbox.get_nowait()
            except Empty:
                break
            for order, relative in batch:
                timestamp = boundary + relative.dt
                write = RegisterWrite(
                    timestamp,
                    relative.stage,
                    relative.values,
                    relative.connection,
                )
                heappush(self._pending_writes, (timestamp, order, write))

        start = self._clock
        stop = start + FRAME_SIZE
        current = None
        while self._pending_writes and self._pending_writes[0][0] < stop:
            timestamp, _order, write = heappop(self._pending_writes)
            if timestamp >= start:
                if current is None:
                    current = []
                current.append(write)

        self._current_writes = tuple(current) if current is not None else ()
        self._writes_at = self.frame

    def advance(self) -> Bus | StereoBus:
        self._prepare_writes()
        output = self._output.process()
        for write in self._current_writes:
            if write.connection is None:
                cast(Stage, write.stage)._registers.update(write.values)
            else:
                connection = cast(_Connection, write.connection)
                if connection.active:
                    connection._registers.update(write.values)
        self._clock += len(output.current.data)
        return output
