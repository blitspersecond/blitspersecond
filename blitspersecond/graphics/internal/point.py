from numpy import (
    array,
    dtype,
    int32,
    float32,
    float64,
    ndarray,
    add,
    multiply,
    subtract,
    integer,
)


class Point:
    _dtype: dtype = dtype([("x", int32), ("y", int32)])
    __slots__ = ("_raw",)

    def __init__(self, x: int = 0, y: int = 0) -> None:
        if not (isinstance(x, (int, integer)) and isinstance(y, (int, integer))):
            raise ValueError(
                f"Point requires integer values, got: ({type(x)}, {type(y)})"
            )
        # Create a structured array with fields x and y
        self._raw: ndarray = array((x, y), dtype=Point._dtype)

    @property
    def __array_interface__(self):
        return self._raw.__array_interface__

    @property
    def x(self) -> int:
        return int(self._raw["x"])

    @x.setter
    def x(self, value: int) -> None:
        self._raw["x"] = int(value)

    @property
    def y(self) -> int:
        return int(self._raw["y"])

    @y.setter
    def y(self, value: int) -> None:
        self._raw["y"] = int(value)

    def __repr__(self):
        return f"{self.__class__.__name__}(x={self.x}, y={self.y})"

    def __add__(self, other):
        if isinstance(other, Point):
            # Original Point addition
            return type(self)(x=self.x + other.x, y=self.y + other.y)
        elif isinstance(other, (tuple, list)) and len(other) == 2:
            # New tuple/list support
            return type(self)(x=self.x + other[0], y=self.y + other[1])
        return NotImplemented

    def __mul__(self, other):
        if isinstance(other, (int, float)):
            # Scalar multiplication, always cast to int for pixel-perfect coordinates
            return type(self)(int(self.x * other), int(self.y * other))
        elif isinstance(other, Point):
            # Element-wise multiplication, always cast to int
            return type(self)(int(self.x * other.x), int(self.y * other.y))
        return NotImplemented

    def __iadd__(self, other):
        """In-place addition with support for tuples/lists"""
        if isinstance(other, Point):
            self.x += other.x
            self.y += other.y
            return self
        elif isinstance(other, (tuple, list)) and len(other) == 2:
            self.x += other[0]
            self.y += other[1]
            return self
        return NotImplemented

    def __isub__(self, other):
        """In-place subtraction with support for tuples/lists"""
        if isinstance(other, Point):
            self.x -= other.x
            self.y -= other.y
            return self
        elif isinstance(other, (tuple, list)) and len(other) == 2:
            self.x -= other[0]
            self.y -= other[1]
            return self
        return NotImplemented

    def __eq__(self, other):
        if isinstance(other, Point):
            return self.x == other.x and self.y == other.y
        return NotImplemented
