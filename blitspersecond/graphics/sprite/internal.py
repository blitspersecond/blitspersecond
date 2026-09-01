"""The faces a sprite presents onto its own state.

    sprite.palettes["character"] = 3     -- PaletteRegisters

Internal because **you never name this type.** You reach through a sprite to
it, exactly as you reach through a console to `console.cell[x, y]`. See
`console/internal.py` for the same argument at length.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Iterator, Tuple

#: Slot 0. Duplicated from `presentation` rather than imported: that module
#: imports THIS one, and a two-line constant is cheaper than the cycle.
PRIMARY = 0

if TYPE_CHECKING:
    from .sprite import Sprite


class PaletteRegisters:
    """A sprite's palette selection: tileset name -> which table it draws
    through.

        chun.palettes["character"] = 3      # a costume swap
        chun.palettes["effects"]            # 25, and left alone

    One register per tileset the sheet draws, seeded from each tileset's
    `default_palette`. This is the hardware OBJ model made literal -- a
    palette-bank field per sprite entry, not per tile -- and it is what makes
    a mirror match cost one sheet: red and blue Chun-Li are two sprites, two
    layers, two registers, one set of shared tables and one texture upload.

    **Not a `dict` subclass, deliberately.** Reference subclassed `dict` and
    validated in `__setitem__`, which left `update()`, `setdefault()` and
    `|=` as three unguarded doors into the same state -- a register set
    through any of them would skip the range check AND skip marking the
    sprite dirty, so the write would be accepted and never drawn. Holding a
    plain dict and exposing only what is meant to be reachable closes all
    three by construction.

    Writes validate loudly: an unknown tileset name and an out-of-range table
    are both errors, because a palette register that silently did nothing
    would show up as art in the wrong colours with nothing reporting it.

    **One class, two backings, one meaning.** Slot 0 has a register per
    tileset the sheet draws, held in a dict, because slot 0 may present art
    from any of them. An instanced slot draws only the sheet's
    `primary_tileset`, so it has exactly one register and it lives in the
    per-slot array with everything else about that slot. Naming any other
    tileset on an instanced slot raises -- the write would otherwise be
    accepted and mean nothing, which is the same silent-acceptance failure the
    dict subclass had.
    """

    __slots__ = ("_sprite", "_index")

    def __init__(self, sprite: "Sprite", index: int = 0) -> None:
        self._sprite = sprite
        self._index = int(index)

    @property
    def _names(self) -> Tuple[str, ...]:
        """The tilesets this slot may name."""
        if self._index == PRIMARY:
            return tuple(self._sprite._sheet.tilesets)
        return (self._sprite._sheet.primary_tileset,)

    def _check(self, tileset: str) -> None:
        if tileset in self._names:
            return
        if self._index == PRIMARY:
            raise KeyError(
                f"no tileset named {tileset!r} on this sheet -- it draws "
                f"{sorted(self._sprite._sheet.tilesets)}"
            )
        raise KeyError(
            f"slot {self._index} is an instanced presentation and draws only "
            f"this sheet's primary tileset "
            f"{self._sprite._sheet.primary_tileset!r}, so it has no register "
            f"for {tileset!r}. Only slot 0 presents art from another tileset."
        )

    def __getitem__(self, tileset: str) -> int:
        self._check(tileset)
        if self._index == PRIMARY:
            return self._sprite._slot0_palettes[tileset]
        return int(self._sprite._slots.palette[self._index])

    def __setitem__(self, tileset: str, index: int) -> None:
        self._check(tileset)
        available = len(self._sprite.sheet.tilesets[tileset].palettes)
        index = int(index)
        if not 0 <= index < available:
            raise ValueError(
                f"palette {index} out of range -- tileset {tileset!r} carries "
                f"{available} ({0}..{available - 1})"
            )
        if self._index == PRIMARY:
            self._sprite._slot0_palettes[tileset] = index
        else:
            self._sprite._slots.palette[self._index] = index
        self._sprite._touch()

    def __contains__(self, tileset: object) -> bool:
        return tileset in self._names

    def __iter__(self) -> Iterator[str]:
        return iter(self._names)

    def __len__(self) -> int:
        return len(self._names)

    def items(self):
        """Every register as `(tileset, index)` pairs.

        Here because a presentation that snapshots a sprite needs all of them
        at once -- a shadow trail's ghost copies the pose AND the registers,
        since the principal's costume may have changed since that frame.
        """
        return tuple((name, self[name]) for name in self._names)

    def __repr__(self) -> str:
        inner = ", ".join(f"{k}={v}" for k, v in sorted(self.items()))
        return f"<PaletteRegisters {inner}>"
