from dataclasses import dataclass
from enum import Enum
from numbers import Integral, Real


class RegisterScope(Enum):
    STAGE = "stage"
    LANE = "lane"


@dataclass(frozen=True, slots=True)
class RegisterSpec:
    default: object
    minimum: object | None = None
    maximum: object | None = None
    unit: str | None = None
    accepts_signal: bool = False

    def __post_init__(self):
        # minimum/maximum/default are `object` because a register can carry a
        # structured value (e.g. SoundSpec); when they're set at all, callers
        # only ever pass values of the same mutually-comparable/reconstructible
        # type, an invariant pyright can't see through the `object` typing.
        if not isinstance(self.accepts_signal, bool):
            raise TypeError("accepts_signal must be a boolean")
        if self.accepts_signal and (
            not isinstance(self.default, Real)
            or isinstance(self.default, bool)
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
            # Structured register values already carry their own type and
            # invariants. Preserve the object rather than assuming its class
            # can reconstruct itself from one positional value.
            normalized = value
        if not compatible:
            raise TypeError(
                f"register {name!r} expects {type(initial).__name__}, "
                f"got {type(value).__name__}"
            )

        self._validate(name, normalized)
        return normalized

    def _validate(self, name: str, value: object):
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
    timestamp: int
    stage: object
    values: tuple[tuple[str, object], ...]
    connection: object | None = None
