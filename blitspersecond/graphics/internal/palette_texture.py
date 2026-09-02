# Palette RAM made literal: palette tables reach the INDEXED shader as a
# 256xN RGBA texture it samples (unit 1), not as a 256-vec4 uniform. The
# uniform cost the engines ~0.15ms PER INDEXED DRAW, every compose, because
# the shared program has one uniform state and alternating layers stomp each
# other's table; a texture bind is nanoseconds and the upload happens only
# when a table actually mutated.
#
# N is the point of the second dimension. A tileset carries a BANK -- four
# tables for fumiko, twenty-eight for Chun-Li -- and one texture per member
# would make picking one a bind, which cuts a run. One table with a row per
# member makes picking one a NUMBER: `indexed.frag` takes the row from a
# varying, so it can be fed per draw today and per quad later, which is the
# sprite engine's palette-bank future ("palette as a vertex attribute")
# arriving by the door this module has named since it was written.

from __future__ import annotations

from typing import Sequence, Tuple, Union
from weakref import WeakKeyDictionary

from pyglet.gl import GL_NEAREST
from pyglet.image import ImageData, Texture

from ..palette import Palette, PaletteBank

#: What owns rows: a bank of them, or a lone Palette that is a bank of one.
Table = Union[Palette, PaletteBank]


class _Entry:
    __slots__ = ("texture", "versions")

    def __init__(self, texture: Texture) -> None:
        self.texture = texture
        self.versions: Tuple[int, ...] = ()  # forces the first upload


class PaletteTexture:
    """Memo: the identity of a table's owner -> its 256xN texture, upload
    gated by the mutation counters of the rows in it. Weak keys so an owner
    that dies releases its entry; identity (not equality) is the key on
    purpose -- sharing a Palette or a PaletteBank IS sharing its GPU table,
    same law as PixelBuffer sharing.

    A lone Palette is keyed as itself and gets a 256x1 texture. That is the
    bare indexed `PixelBuffer` and the console's default -- surfaces that
    carry one table and have no bank to belong to. A palette that is BOTH a
    bank member and some buffer's own table is projected twice, which costs a
    kilobyte and keeps the two owners from deciding each other's row."""

    _cache: "WeakKeyDictionary[Table, _Entry]" = WeakKeyDictionary()

    @classmethod
    def get(cls, table: Table) -> Texture:
        """`table`'s texture, current as of its last mutation: one row per
        palette, row `i` being `bank[i]`. Needs a GL context (draw-time
        machinery, like PixelBuffer.texture)."""
        rows: Sequence[Palette] = (table,) if isinstance(table, Palette) else table
        entry = cls._cache.get(table)
        if entry is None:
            entry = _Entry(
                Texture.create(
                    256,
                    len(rows),
                    min_filter=GL_NEAREST,
                    mag_filter=GL_NEAREST,
                )
            )
            cls._cache[table] = entry
        # One counter per row, because any member moving invalidates the
        # table and the shader has no way to sample a stale one. Comparing a
        # tuple of ints beats diffing 256 entries N times over, and a bank is
        # tens of rows, not thousands.
        versions = tuple(palette.version for palette in rows)
        if entry.versions != versions:
            entry.texture.blit_into(
                # Row-major from row 0 up, which is where `texelFetch` with
                # y=0 reads: pyglet's positive pitch uploads the first row of
                # bytes as the texture's first row, so `bank[i]` lands on row
                # i and the shader's varying is the register unchanged.
                ImageData(
                    width=256,
                    height=len(rows),
                    fmt="RGBA",
                    data=b"".join(palette.tobytes() for palette in rows),
                    pitch=None,
                ),
                0,
                0,
                0,
            )
            entry.versions = versions
        return entry.texture
