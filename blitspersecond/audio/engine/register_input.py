from __future__ import annotations

from collections import ChainMap
from types import MappingProxyType
from typing import TYPE_CHECKING, cast

from ..common import Bus, RegisterSpec

if TYPE_CHECKING:
    from .stage import Stage, StageEngine


class _RegisterInputs:
    """Resolve optional mono signal connections over stored register values."""

    def __init__(
        self,
        engine: "StageEngine",
        specs,
        *fallbacks,
        parent: "_RegisterInputs | None" = None,
    ):
        self._engine = engine
        self._specs = specs
        self._sources: dict[str, Stage] = {}
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
        return self._sources.keys()

    def connect(self, name: str, source: "Stage"):
        if name not in self._specs:
            raise ValueError(f"Stage has no register {name!r}")
        spec = self._specs[name]
        if not isinstance(spec, RegisterSpec):
            raise TypeError(f"register {name!r} has no RegisterSpec")
        if not spec.accepts_signal:
            raise ValueError(f"register {name!r} does not accept a signal")
        if source._engine is not self._engine:
            raise ValueError("cannot connect a signal from another AudioEngine")
        if source.channels != 1:
            raise ValueError("a register input requires a mono signal Stage")
        self._sources[name] = source
        self._invalidate()

    def disconnect(self, name: str, source: "Stage"):
        if self._sources.get(name) is not source:
            raise ValueError(
                f"register {name!r} is not connected to the given Stage"
            )
        del self._sources[name]
        self._invalidate()

    def resolve(self):
        current = self._engine.frame
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
