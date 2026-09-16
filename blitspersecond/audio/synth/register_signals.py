"""Letting another Stage's audio drive a register, instead of a stored value.

A register normally holds one value until something writes a new one. A
register *signal* replaces that value with a whole block of audio pulled from
another Stage, so it can change every sample. A tremolo is an oscillator
connected to a `level`; a vibrato is one connected to a `frequency`. There is
no depth, bias or amount: the control Stage produces exactly the values the
target register is to observe, which keeps modulation an ordinary graph
instead of a set of hidden modes.

The connection is a **separate kind of edge** from an audio lane. A Stage
driving a register is not mixed into anything -- it is pulled when the
register is read, settles once per block like any other Stage, and can fan
out to several sockets while processing once.
"""

from collections import ChainMap
from types import MappingProxyType
from typing import Protocol, cast

from .bus import Bus, StereoBus
from .registers import RegisterSpec


class BlockClock(Protocol):
    """The only engine service resolving a signal needs: which block we are on."""

    @property
    def block(self) -> int: ...


class SignalSource(Protocol):
    """A Stage able to drive a register: mono, pullable, on the same engine.

    Both attributes are declared read-only, because reading is all this
    module does with them. A mutable protocol attribute is invariant, which
    would demand every source spell its engine as `object` rather than as
    whatever engine it actually holds.
    """

    @property
    def channels(self) -> int: ...

    @property
    def _engine(self) -> object: ...

    def process(self) -> Bus | StereoBus: ...


class _RegisterSignals:
    """The resolved view of one register bank, with connected signals on top.

    Reading a register goes through a `ChainMap` whose first layer is the
    signals resolved this block, and whose later layers are the stored values
    it falls back to. That ordering IS the rule: while a signal is connected
    it completely overrides the stored value, and scheduled writes keep
    updating the value underneath so disconnecting restores it rather than
    freezing whatever the signal last said.

    A lane passes its Stage's view as `parent`, which puts a fourth layer
    underneath: lane signal, lane value, Stage signal, Stage value. That is
    how addressing one input shadows the Stage broadcast for that input only.
    """

    def __init__(
        self,
        engine: BlockClock,
        specs,
        *fallbacks,
        parent: "_RegisterSignals | None" = None,
    ):
        self._engine = engine
        self._specs = specs
        self._sources: dict[str, SignalSource] = {}
        self._values = {}
        self._parent = parent
        inherited = () if parent is None else (parent.view,)
        self._view = MappingProxyType(
            ChainMap(self._values, *fallbacks, *inherited)
        )
        self._resolved_at: int | None = None

    @property
    def view(self):
        return self._view

    @property
    def names(self):
        """Which registers are currently driven by a signal."""
        return self._sources.keys()

    def connect(self, name: str, source: SignalSource):
        if name not in self._specs:
            raise ValueError(f"Stage has no register {name!r}")
        spec = self._specs[name]
        if not isinstance(spec, RegisterSpec):
            raise TypeError(f"register {name!r} has no RegisterSpec")
        # Opting in is per register, because an array-valued register only
        # means something once its Operator implements that meaning. A Sine
        # driven at signal rate has to integrate phase rather than multiply,
        # so `accepts_signal` is a claim the Operator has done that work.
        if not spec.accepts_signal:
            raise ValueError(f"register {name!r} does not accept a signal")
        if source._engine is not self._engine:
            raise ValueError("cannot connect a signal from another AudioEngine")
        if source.channels != 1:
            raise ValueError("a register input requires a mono signal Stage")
        self._sources[name] = source
        self._invalidate()

    def disconnect(self, name: str, source: SignalSource):
        if self._sources.get(name) is not source:
            raise ValueError(
                f"register {name!r} is not connected to the given Stage"
            )
        del self._sources[name]
        self._invalidate()

    def resolve(self):
        """Pull every connected signal, once for this block.

        Cached on the block number, so a control Stage feeding four sockets
        advances its phase once rather than four times.

        **The array stored is a view, held for the rest of the block**, which
        is the one thing `Block` otherwise forbids. A claim that grows the
        slab -- a delay ring filling or lengthening does exactly that -- will
        replace the backing allocation and leave this view detached from the
        row it came from.

        That is currently harmless, but by an invariant held elsewhere rather
        than by construction: growth copies the samples forward, and a Stage
        that has settled is not written again until the next block, so a
        detached view still holds this block's audio. If a settled block ever
        becomes writable mid-block, this would silently read the previous one.
        Re-resolving per access instead would mean pulling the source again
        or storing a row id, and neither is free.
        """
        current = self._engine.block
        if self._resolved_at == current:
            return self._view
        if self._parent is not None:
            self._parent.resolve()
        self._values.clear()
        for name, source in self._sources.items():
            bus = cast(Bus, source.process())
            self._values[name] = bus.current.data
        self._resolved_at = current
        return self._view

    def _invalidate(self):
        self._values.clear()
        self._resolved_at = None
