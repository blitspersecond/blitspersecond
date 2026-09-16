"""Registers: the named, writable values an Operator reads while it works.

An Operator has no setters and a game never calls one. It declares registers
-- `frequency`, `gate`, `level` -- and everything that changes a sound is a
timestamped write to one of them, applied at a sample offset the caller
chose. That is what makes a note land between two samples instead of on the
block boundary that happened to be nearest.

A `RegisterSpec` is the declaration: what type the register holds, what range
is meaningful, and whether another stage's audio may drive it. A
`RegisterWrite` is one instant of change. Neither knows what a Stage is --
see `RegisterWrite.stage`.
"""

from dataclasses import dataclass
from enum import Enum
from numbers import Integral, Real


class RegisterScope(Enum):
    """Whether a register belongs to the whole Stage or to one input lane.

    A Stage-scoped register is one value shared by everything the stage does:
    an oscillator's frequency is the stage's frequency. A Lane-scoped one
    exists once per connected input, which is what lets a mixing desk give
    every strip its own level without the stages knowing they are on a desk.
    """

    STAGE = "stage"
    LANE = "lane"


@dataclass(frozen=True, slots=True)
class RegisterSpec:
    """What one register holds, and what may be written to it.

    **The default carries the type.** There is no separate type field: what
    a register accepts is whatever its default is, so `RegisterSpec(False)`
    is a gate and `RegisterSpec(0.5)` is a level, and neither can be handed
    the other's values. That keeps a declaration to the one line an operator
    would have written anyway.

    Frozen and compared by value, because two Programs on one Stage may
    declare the same register and must agree -- a conflict is caught by the
    specs being unequal.
    """

    #: The value before anything writes, and the type every write must match.
    default: object
    #: Inclusive bounds, or None for unbounded. Checked against the default
    #: too, so a spec that contradicts itself raises where it is written.
    minimum: object | None = None
    maximum: object | None = None
    #: What the number means -- "Hz", "linear", "bits". Nothing reads this
    #: yet; it is here so the declaration says what it means at the point it
    #: is written, and for a graph inspector to have something to show.
    unit: str | None = None
    #: Whether another Stage's audio may be connected to drive this register
    #: at sample rate, instead of it holding one value per write. Only
    #: meaningful for real-valued registers: a gate cannot be a waveform.
    accepts_signal: bool = False

    def __post_init__(self):
        # `default`, `minimum` and `maximum` are `object` because a register
        # can carry a structured value -- PCM's `sound` holds a SoundSpec.
        # Where bounds are set at all, callers pass mutually comparable values
        # of one type, which is an invariant pyright cannot see through
        # `object`.
        if not isinstance(self.accepts_signal, bool):
            raise TypeError("accepts_signal must be a boolean")
        if self.accepts_signal and (
            not isinstance(self.default, Real) or isinstance(self.default, bool)
        ):
            raise TypeError("a signal register must have a real-valued default")
        if (
            self.minimum is not None
            and self.maximum is not None
            and self.minimum > self.maximum  # pyright: ignore[reportOperatorIssue]
        ):
            raise ValueError("register minimum cannot exceed maximum")
        self._validate("default", self.default)

    def normalize(self, name: str, value: object):
        """Check a write against this spec and return it as the stored type.

        **`bool` is tested before `int`.** In Python `bool` IS an `int`, so
        without that order `True` would be an acceptable write to a frequency
        and would set it to 1 Hz. A gate takes only gates, and a number takes
        only numbers.

        Numbers are converted to the default's exact type, so writing `1` to
        a float register stores `1.0` and the operator never sees a type it
        did not declare.
        """
        initial = self.default
        if isinstance(initial, bool):
            compatible = isinstance(value, bool)
            normalized = value
        elif isinstance(initial, Integral):
            compatible = isinstance(value, Integral) and not isinstance(value, bool)
            normalized = (
                type(initial)(value)  # pyright: ignore[reportCallIssue]
                if compatible
                else value
            )
        elif isinstance(initial, Real):
            compatible = isinstance(value, Real) and not isinstance(value, bool)
            normalized = (
                type(initial)(value)  # pyright: ignore[reportCallIssue]
                if compatible
                else value
            )
        else:
            compatible = isinstance(value, type(initial))
            # A structured value carries its own type and invariants. Keep the
            # object rather than assuming its class can rebuild itself from
            # one positional argument.
            normalized = value
        if not compatible:
            raise TypeError(
                f"register {name!r} expects {type(initial).__name__}, "
                f"got {type(value).__name__}"
            )

        self._validate(name, normalized)
        return normalized

    def _validate(self, name: str, value: object):
        # Spelled `not value >= minimum` rather than `value < minimum` on
        # purpose: every comparison against NaN is False, so this form
        # REJECTS a NaN write where the readable one would admit it. A NaN
        # reaching an operator that keeps state -- a filter's history, a
        # delay's ring -- is not a bad sound but a dead one: the state is NaN
        # from then on, with nothing raised and no way back.
        #
        # This only guards registers that HAVE a bound. An unbounded one
        # still admits NaN and infinity; see the board.
        if (
            self.minimum is not None
            and not value >= self.minimum  # pyright: ignore[reportOperatorIssue]
        ):
            raise ValueError(f"register {name!r} must be at least {self.minimum}")
        if (
            self.maximum is not None
            and not value <= self.maximum  # pyright: ignore[reportOperatorIssue]
        ):
            raise ValueError(f"register {name!r} must be at most {self.maximum}")


@dataclass(frozen=True, slots=True)
class RegisterWrite:
    """One instant of change: these values, on that stage, at this sample.

    **`stage` is typed `object` deliberately.** Registers sit underneath the
    graph, and naming `Stage` here would point the dependency back up at the
    layer that uses them. The engine knows what it put in; nothing at this
    level needs to.
    """

    #: Absolute sample on the engine clock, not an offset into a block.
    timestamp: int
    stage: object
    #: Name/value pairs rather than a dict, so a write is hashable, frozen
    #: with the rest of the record, and applied in the order it was written.
    values: tuple[tuple[str, object], ...]
    #: The input lane this write addresses, for a Lane-scoped register.
    #: `None` means it addresses the Stage itself.
    connection: object | None = None
