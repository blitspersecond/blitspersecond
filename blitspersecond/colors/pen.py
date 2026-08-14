# A pen: the four palette indices a glyph cell is painted with, one per plane.
# NOT a palette -- it holds no colours of its own, just indices INTO one: the
# colours the pen is currently drawing with. Slots 0 and 1 are named background
# and foreground (every glyph has those); slots 2 and 3 are reached by number,
# because a font decides what -- if anything -- they draw, so a name would
# prejudge it (the way "highlight"/"shadow" did). A pen, not a console or text
# thing: the same four-index pen would paint a line or a curve if the engine
# ever draws one.

from __future__ import annotations

from typing import Iterator


class Pen:
    """A pen's four palette indices, one per glyph plane: `background` (0),
    `foreground` (1), and slots `[2]`/`[3]` by number. Mutable and indexable --
    `pen.foreground = RED`, `pen[2] = LIME`, `pen[0]` is `pen.background`."""

    __slots__ = ("_slots",)

    def __init__(
        self, background: int = 0, foreground: int = 1, *rest: int
    ) -> None:
        if len(rest) > 2:
            raise ValueError(f"a pen has 4 slots; got {2 + len(rest)} values")
        slots = [int(background), int(foreground), 2, 3]
        for i, value in enumerate(rest):
            slots[2 + i] = int(value)
        self._slots = slots

    @property
    def background(self) -> int:
        return self._slots[0]

    @background.setter
    def background(self, value: int) -> None:
        self._slots[0] = int(value)

    @property
    def foreground(self) -> int:
        return self._slots[1]

    @foreground.setter
    def foreground(self, value: int) -> None:
        self._slots[1] = int(value)

    def __getitem__(self, index: int) -> int:
        return self._slots[index]

    def __setitem__(self, index: int, value: int) -> None:
        self._slots[index] = int(value)

    @property
    def paint(self) -> tuple[int, int, int, int]:
        """What a glyph layer writes into a cell's colour slab: four constants,
        broadcast flat across every pixel of the cell. `MagicPen` answers the
        same question with an array that varies inside the cell -- that shared
        question is the whole of the protocol between them."""
        b, f, s2, s3 = self._slots
        return b, f, s2, s3

    def __len__(self) -> int:
        return 4

    def __iter__(self) -> Iterator[int]:
        return iter(self._slots)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Pen):
            return self._slots == other._slots
        return NotImplemented

    def __repr__(self) -> str:
        b, f, s2, s3 = self._slots
        return f"Pen(background={b}, foreground={f}, {s2}, {s3})"
