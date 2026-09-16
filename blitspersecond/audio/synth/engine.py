"""The AudioEngine: the sample clock, the write timeline, and one demand.

Everything in the graph is pulled from here. `advance()` settles the terminal
Stage for one block, commits that block's register writes, and moves the
clock forward by exactly one block. Nothing else advances audio time -- not a
wall clock, not the game, not a Stage.

**Control arrives as one relative timeline.** `schedule()` takes events whose
`dt` is a sample offset from a common origin, and the audio worker picks that
origin when it admits the whole batch. A caller never reads the moving clock,
so a score's internal timing cannot be smeared by a late first event, and a
one-event batch travels exactly the same road as a complete piece of music.

**A scheduled future can be withdrawn, by naming the node it addresses.**
`cancel()` drops the unresolved writes aimed at a Stage or at one of its
input lanes, and `schedule(..., replacing=...)` does both as one command.
Identity is the graph's own -- a write already carries the Stage it is for,
so there is no separate handle or group name to mint, keep, or get wrong.

The law is the same boundary `dt=0` means: **a target's unresolved writes end
at the first unresolved block.** Cancellation is never retroactive, because a
block that has been rendered is a fact. It is also purely a control
operation: withdrawing a source's writes stops it being told to do anything
new, and the echo and reverb downstream of it go on ringing until they drain,
which is what the quiescence machinery is for and what a listener expects.

**Topology is construction-time state.** Building Stages, connecting them and
replacing the output all raise once a Driver owns the engine. The runtime
surface is `schedule()` and `cancel()` and nothing else: rewiring a live graph
would mean mutating the worker's objects underneath it, which needs a command
contract that does not exist rather than an unsynchronised write.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from heapq import heapify, heappop, heappush
from itertools import count
from numbers import Integral
from queue import Empty, SimpleQueue
from typing import cast

from .block import BlockPool
from .bus import Bus, StereoBus
from .connection import _Connection
from ..constants import BLOCK_SIZE
from .mixing_desk import MixingDesk
from .program import Program
from .registers import RegisterWrite
from .stage import Stage
from .stages import ApplyStage, CompositeStage, SourceStage


@dataclass(frozen=True, slots=True)
class _RelativeWrite:
    """One scheduled event, before the worker has chosen its origin."""

    dt: int
    stage: Stage
    values: tuple[tuple[str, object], ...]
    connection: _Connection | None = None


@dataclass(frozen=True, slots=True)
class _Submission:
    """One command across the thread boundary: cancellations, then writes.

    The two travel together so a replacement cannot be torn in half. A caller
    that submits "drop this stem's future and here is its new one" must not
    have the drop land in one block and the new timeline in the next, with a
    hole in between -- and must not have them arrive out of order, which
    would cancel what it had just submitted.
    """

    cancels: tuple[tuple[Stage, _Connection | None], ...]
    writes: tuple[tuple[int, _RelativeWrite], ...]


class AudioEngine:
    """Construct Stages, own the sample clock, and demand the output Stage."""

    def __init__(self):
        self.pool = BlockPool()
        self._clock = 0
        self._running = False
        #: The only thread boundary in the engine. A game tick puts a whole
        #: batch here; the audio worker takes it at a block edge of its own
        #: choosing. Nothing else crosses.
        self._inbox = SimpleQueue()
        #: Breaks ties in the heap so two writes at one sample stay in the
        #: order they were written -- which is what makes `gate=False` then
        #: `gate=True` at the same instant a retrigger rather than a coin toss.
        self._write_order = count()
        self._pending_writes = []
        self._writes_at: int | None = None
        self._current_writes: tuple[RegisterWrite, ...] = ()
        # The device always has a terminal Stage to pull. An empty MixingDesk
        # settles stereo silence, so a Driver can open and hold the output
        # before a game has built anything at all.
        self._output: Stage = MixingDesk(self)

    # -- clocks ----------------------------------------------------------

    @property
    def clock(self) -> int:
        """The absolute sample the current block begins at."""
        return self._clock

    @property
    def block(self) -> int:
        """Derived, not counted separately, so the two cannot disagree."""
        return self._clock // BLOCK_SIZE

    # -- the output ------------------------------------------------------

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

    # -- building a graph -------------------------------------------------

    def source(self, program: Program | None = None) -> SourceStage:
        return SourceStage(self, program)

    def composite(self, program: Program | None = None) -> CompositeStage:
        return CompositeStage(self, program)

    def apply(self, program: Program) -> ApplyStage:
        return ApplyStage(self, program)

    def mixing_desk(self) -> MixingDesk:
        return MixingDesk(self)

    # -- control ----------------------------------------------------------

    def schedule(
        self,
        events: Iterable[
            tuple[int, Stage, Mapping[str, object]]
            | tuple[int, Stage, Stage, Mapping[str, object]]
        ],
        *,
        replacing: Iterable["Stage | tuple[Stage, Stage]"] = (),
    ):
        """Admit one relative timeline at the next unresolved block.

        Three-item events address a Stage; four-item events name the connected
        Stage as well and address that input's lane.

        `replacing` names targets whose unresolved writes this timeline
        supersedes, in the same shape `cancel()` takes. The two halves cross
        the thread boundary as ONE command, so an interactive transport can
        swap the future it has already queued without a gap where neither
        timeline is in force.

        Everything is validated HERE, on the calling thread: an out-of-range
        frequency or an unknown register name is a caller's error with a
        caller's traceback, not a failure inside the audio worker where the
        only symptom would be silence. Validation runs before anything is
        queued, so a replacement that is rejected leaves what it was going to
        replace exactly as it was.
        """
        cancels = self._resolve_targets(replacing)
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
                # Every real lane is a `_Connection`; `Stage._connection` is
                # declared against a Protocol only because `stage` cannot
                # import `connection` without a cycle.
                connection = cast(_Connection, stage._connection(source))
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
            batch.append(
                (
                    next(self._write_order),
                    _RelativeWrite(
                        int(dt),
                        stage,
                        tuple(normalized.items()),
                        connection,
                    ),
                )
            )

        if not batch:
            raise ValueError("schedule has no events")
        # One put, after everything is validated. A batch is admitted whole or
        # not at all, so a bad event late in a score cannot leave half of it
        # already playing.
        self._inbox.put(_Submission(cancels, tuple(batch)))

    def cancel(self, targets: Iterable["Stage | tuple[Stage, Stage]"]):
        """Drop the unresolved writes addressed to these nodes.

        A target is a Stage, or a `(stage, source)` pair naming one input
        lane -- the same two addresses `schedule()` accepts, so what can be
        written can be withdrawn in the words it was written in.

        **The graph is the identity.** A queued write already names the Stage
        it is for, so cancelling needs no handle to keep and no group name to
        invent, and a target cannot go stale while the node it names still
        exists. The corollary is that a node's future is ONE future: two
        timelines writing the same Stage are cancelled together. Where they
        must die separately they are two voices, which the graph makes cheap.

        Takes effect from the first unresolved block, so audio already
        rendered is untouched -- and so is retained state everywhere
        downstream. Withdrawing a source's writes stops it being told to do
        anything new; the reverb behind it keeps ringing until it drains.

        Naming a target with nothing queued for it is not an error. A
        transport that stops twice, or stops something already finished, is
        ordinary.
        """
        cancels = self._resolve_targets(targets)
        if not cancels:
            raise ValueError("cancel has no targets")
        self._inbox.put(_Submission(cancels, ()))

    def _resolve_targets(
        self, targets: Iterable["Stage | tuple[Stage, Stage]"]
    ) -> tuple[tuple[Stage, _Connection | None], ...]:
        """Validate cancellation targets and resolve lanes, on this thread."""
        resolved: list[tuple[Stage, _Connection | None]] = []
        for target in targets:
            if isinstance(target, Stage):
                stage, source = target, None
            else:
                try:
                    stage, source = target
                except (TypeError, ValueError):
                    raise TypeError(
                        "a cancellation target is a Stage, or a "
                        "(Stage, input Stage) pair"
                    ) from None
            if not isinstance(stage, Stage) or stage._engine is not self:
                raise ValueError("cancellation target references a foreign Stage")
            if source is None:
                resolved.append((stage, None))
                continue
            if not isinstance(source, Stage) or source._engine is not self:
                raise ValueError(
                    "cancellation target references a foreign input Stage"
                )
            resolved.append((stage, cast(_Connection, stage._connection(source))))
        return tuple(resolved)

    @property
    def writes(self) -> tuple[RegisterWrite, ...]:
        """This block's writes, resolving the block if that has not happened."""
        if self._writes_at != self.block:
            self._prepare_writes()
        return self._current_writes

    @property
    def _prepared_writes(self) -> tuple[RegisterWrite, ...]:
        """This block's writes, but never resolving an unstarted block.

        What quiescence checks read. Asking whether a Stage is asleep must not
        pull a pending batch into the current block -- only ordinary
        resolution admits writes, and inspection is not resolution.
        """
        if self._writes_at != self.block:
            return ()
        return self._current_writes

    def _prepare_writes(self):
        """Choose the origin for anything newly submitted, then cut the block."""
        if self._writes_at == self.block:
            return

        # The current clock is the first unresolved sample, so a whole
        # timeline crosses into absolute time together and keeps its internal
        # spacing exactly. Anything submitted after this drain waits for the
        # next block rather than editing one already being produced.
        boundary = self._clock
        while True:
            try:
                submission = self._inbox.get_nowait()
            except Empty:
                break
            # Cancellations first, and only against what was already pending:
            # a replacement must not withdraw the timeline it arrived with.
            # Submissions are drained in the order they were made, so a later
            # command always sees the effect of an earlier one.
            if submission.cancels:
                doomed = set(submission.cancels)
                survivors = [
                    entry
                    for entry in self._pending_writes
                    if (entry[2].stage, entry[2].connection) not in doomed
                ]
                if len(survivors) != len(self._pending_writes):
                    self._pending_writes = survivors
                    heapify(self._pending_writes)
            for order, relative in submission.writes:
                timestamp = boundary + relative.dt
                heappush(
                    self._pending_writes,
                    (
                        timestamp,
                        order,
                        RegisterWrite(
                            timestamp,
                            relative.stage,
                            relative.values,
                            relative.connection,
                        ),
                    ),
                )

        start = self._clock
        stop = start + BLOCK_SIZE
        current = None
        while self._pending_writes and self._pending_writes[0][0] < stop:
            timestamp, _order, write = heappop(self._pending_writes)
            # Cannot currently be in the past -- every dt is non-negative and
            # measured from a boundary at or after this one. Popped and
            # dropped rather than applied late, so a write can never land on
            # audio that has already gone out.
            if timestamp >= start:
                if current is None:
                    current = []
                current.append(write)

        self._current_writes = tuple(current) if current is not None else ()
        self._writes_at = self.block

    # -- one block --------------------------------------------------------

    def advance(self) -> Bus | StereoBus:
        """Settle one block, commit its writes, and move the clock."""
        self._prepare_writes()
        output = self._output.process()
        # After rendering, not before. During the block an Operator sees these
        # as timestamped events and applies them at exact samples; afterwards
        # the banks hold the settled value, so a Stage that was never pulled
        # still has an authoritative starting point when it next runs.
        for write in self._current_writes:
            if write.connection is None:
                cast(Stage, write.stage)._registers.update(write.values)
            else:
                connection = cast(_Connection, write.connection)
                # A lane that has been disconnected is dead: its automation
                # does not resurrect if something reconnects that source.
                if connection.active:
                    connection._registers.update(write.values)
        # The clock follows the audio that was actually produced. A stereo
        # block is `(BLOCK_SIZE, 2)`, whose length is still BLOCK_SIZE.
        self._clock += len(output.current.data)
        return output
