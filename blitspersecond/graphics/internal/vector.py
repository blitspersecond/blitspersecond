from numpy import integer


class Vector:
    """A 2D vector -- a displacement/extent (dx, dy), NOT a position (that's
    Point). Used for sizes now and movement/velocity later, so its components are
    x/y like any vector, not w/h.

    A distinct type from Point because the affine distinction matters: a Point
    names *where*, a Vector names *how much* (a size, an offset, a step).
    Mutable in place (x/y setters) so a pooled owner can re-stamp it without
    allocating a new one.

    Deliberately lean -- no arithmetic operators yet; add them when a caller
    needs them rather than speculatively."""

    __slots__ = ("_x", "_y")

    def __init__(self, x: int = 0, y: int = 0) -> None:
        if not (isinstance(x, (int, integer)) and isinstance(y, (int, integer))):
            raise ValueError(
                f"Vector requires integer values, got: ({type(x)}, {type(y)})"
            )
        self._x = int(x)
        self._y = int(y)

    @property
    def x(self) -> int:
        return self._x

    @x.setter
    def x(self, value: int) -> None:
        self._x = int(value)

    @property
    def y(self) -> int:
        return self._y

    @y.setter
    def y(self, value: int) -> None:
        self._y = int(value)

    def __repr__(self):
        return f"{self.__class__.__name__}(x={self.x}, y={self.y})"

    def __eq__(self, other):
        if isinstance(other, Vector):
            return self.x == other.x and self.y == other.y
        return NotImplemented
