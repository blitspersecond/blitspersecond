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

    The register is a number at the GPU too, and not only here: the tileset's
    bank is ONE 256xN texture (`PaletteBank`, and `PaletteTexture` for the
    projection), so writing this picks a row the shader reads rather than
    binding a second table.

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
        # THIS sprite's bank, not the sheet's -- a sprite that grew rows of
        # its own must be able to select one, and only it can see them.
        available = len(self._sprite._bank(tileset))
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

    def add(self, tileset: str, palettes) -> Tuple[int, ...]:
        """Grow `tileset`'s table for THIS SPRITE, and hand back the new rows.

            shades = chun.palettes.add(
                "character",
                (costume.shadowed(HAIR, mix=k / 6) for k in range(1, 7)),
            )
            for ghost, row in zip(chun.before.extend(6), shades):
                ghost.palettes["character"] = row

        **Copy on write, and the copy is the point.** A `SpriteSheet` is
        immutable and interned by path, so growing its bank in place reached
        every other holder of that sheet -- and grew again on every load, for
        as long as anything kept it alive. This gives the sprite a bank of its
        own instead, leaving the sheet's untouched: *"you just change a new
        copy and dont disturb others"* (Kris, 2026-09-05).

        The copy is a tuple of `Palette` REFERENCES -- no table is duplicated,
        and the sheet's rows are shared with the new bank. What is really made
        is one more GPU palette texture, which is the small thing.

        Rows are appended, so a register handed out before still means what it
        meant, and the numbers returned are the new rows in order.
        """
        current = self._sprite._bank(tileset)
        grown = current.extended(palettes)
        self._sprite._banks[tileset] = grown
        return tuple(range(len(current), len(grown)))

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
