"""A 256-glyph, byte-addressed character engine."""

from __future__ import annotations

from enum import Enum
from operator import index
from pathlib import Path
from typing import Optional

import numpy as np
from pyglet.image import Texture

from blitspersecond.colors import MagicPen, Palette, Pen, system_palette
from blitspersecond.common import Size
from blitspersecond.display.layer import Layer
from blitspersecond.graphics.common import (
    CWORD,
    Collidable,
    CollisionQuery,
    Orientation,
    PixelBuffer,
)
from blitspersecond.graphics.tile import TileLayer
from blitspersecond.resources import ImageSpec, load_image_spec
from blitspersecond.system.config import Config


ATLAS_COLS = 16
ATLAS_ROWS = 16
CHAR_COUNT = ATLAS_COLS * ATLAS_ROWS

_CHARSET_DIR = Path(__file__).resolve().parents[2] / "assets" / "charset"

#: The orientations a glyph cell can take: the four that preserve a
#: rectangle, which is exactly the ones with TRANSPOSE clear. The turning
#: three are refused rather than special-cased on square charsets -- a rule
#: that holds for every charset is worth more than a capability that holds
#: for some of them, and 8x8 is the only square set BPS ships.
#:
#: These four are the Klein four-group: closed under composition, every
#: element its own inverse, and composition is plain XOR of the bits. The
#: full D4 is not abelian, which is the other reason not to let the turning
#: three in through a side door.
LEGAL_ORIENTATIONS = (
    Orientation.NONE,
    Orientation.FLIP_H,
    Orientation.FLIP_V,
    Orientation.ROT_180,
)

#: With TRANSPOSE clear a flag value is `2*H + 4*V`, so `>> 1` is `H + 2*V`
#: -- a dense 0..3 index into the variant table, no lookup needed.
_VARIANTS = len(LEGAL_ORIENTATIONS)


class CharSet(Enum):
    """The character sets supplied with BPS."""

    ASCII8X8 = "ascii8x8.png"
    ASCII8X12 = "ascii8x12.png"


class _CellAccess:
    """Indexed reads and writes over a GlyphLayer's live cell buffers,
    addressed `cell[x, y]` -- screen order, like every other surface."""

    __slots__ = ("_engine",)

    def __init__(self, engine: GlyphLayer) -> None:
        self._engine = engine

    def __getitem__(self, cell: tuple[int, int]) -> int:
        x, y = cell
        return int(self._engine._chars[x, y])

    def __setitem__(self, cell: tuple[int, int], value: int) -> None:
        x, y = cell
        try:
            byte = index(value)
        except TypeError as error:
            raise TypeError(f"glyph index must be a byte, got {value!r}") from error
        if not 0 <= byte <= 0xFF:
            raise ValueError(f"glyph index must be a byte, got {byte}")

        self._engine._chars[x, y] = byte
        self._engine._orient[x, y] = self._engine._slot
        self._engine._paint(x, y, self._engine._pen.paint)
        self._engine._stamp(x, y)


class _GlyphAccess:
    """Indexed reads and writes over a GlyphLayer's 256 patterns."""

    __slots__ = ("_engine",)

    def __init__(self, engine: GlyphLayer) -> None:
        self._engine = engine

    @staticmethod
    def _index(value: object) -> int:
        try:
            # The TypeError catch below is the validation; there's no way to
            # narrow `object` to SupportsIndex before making the attempt.
            byte = index(value)  # pyright: ignore[reportArgumentType]
        except TypeError as error:
            raise TypeError(f"glyph id must be a byte, got {value!r}") from error
        if not 0 <= byte <= 0xFF:
            raise ValueError(f"glyph id must be a byte, got {byte}")
        return byte

    def __getitem__(self, glyph_id: int) -> np.ndarray:
        """Return a copy of one glyph's plane-index pattern."""
        return self._engine._glyphs[self._index(glyph_id)].copy()

    def __setitem__(self, glyph_id: int, pattern: np.ndarray) -> None:
        byte = self._index(glyph_id)
        if not isinstance(pattern, np.ndarray):
            raise TypeError(f"glyph pattern must be an ndarray, got {type(pattern)}")

        expected = self._engine._glyphs.shape[1:]
        if pattern.shape != expected:
            raise ValueError(
                f"glyph pattern must have shape {expected}, got {pattern.shape}"
            )
        if not (
            np.issubdtype(pattern.dtype, np.integer)
            or np.issubdtype(pattern.dtype, np.bool_)
        ):
            raise TypeError("glyph pattern must contain integer plane indices")
        if pattern.size and (np.any(pattern < 0) or np.any(pattern > 3)):
            raise ValueError("glyph pattern plane indices must be in the range 0..3")

        self._engine._set_pattern(byte, pattern)
        if np.any(self._engine._chars == byte):
            self._engine._refresh()


class GlyphLayer(Layer):
    """A byte-addressed set of 256 fixed-size glyphs -- and a Layer: the
    glyph grid is the thing in the draw order.

    Produced by the GlyphEngine factory (the mainline); constructing
    GlyphLayer directly builds the same object *unattached*.

    Pass a :class:`CharSet` to start from one of BPS's system character sets,
    or ``(width, height)`` to start with 256 blank glyphs of that size.

    Character patterns are readable and replaceable through ``glyph[id]``;
    pattern values select one of the four slots in each cell's pen.

    Writes take TWO brushes, not one: ``pen`` is what colour they are and
    ``orientation`` is which way they face. Both describe the next write and
    neither disturbs cells already on screen. Facing is limited to the four
    orientations that preserve a rectangle, and costs nothing at render time
    -- the mirrored patterns are derived once at construction and the facing
    folds into the glyph id, so a screen of mixed facings gathers exactly as
    an upright one does.
    """

    def __init__(self, charset: CharSet | tuple[int, int]) -> None:
        if isinstance(charset, CharSet):
            atlas = load_image_spec(str(_CHARSET_DIR / charset.value))
            atlas_width, atlas_height = atlas.size
            if atlas_width % ATLAS_COLS or atlas_height % ATLAS_ROWS:
                raise ValueError(
                    f"{charset.name} must be a {ATLAS_COLS}x{ATLAS_ROWS} "
                    f"glyph atlas, got {atlas_width}x{atlas_height} pixels"
                )
            char_width = atlas_width // ATLAS_COLS
            char_height = atlas_height // ATLAS_ROWS
        else:
            char_width, char_height = self._validate_char_size(charset)
            atlas = ImageSpec(
                size=(
                    ATLAS_COLS * char_width,
                    ATLAS_ROWS * char_height,
                ),
                mode="P",
            )

        self._cols, self._rows = self._screen_grid(char_width, char_height)

        if atlas.mode != "P":
            raise ValueError("a character set atlas must be an indexed image")

        self._atlas = atlas
        # Every glyph pre-flipped, once: (variant, glyph, gh, gw). Orientation
        # then costs a wider gather rather than a branch -- `_refresh` folds
        # the variant into the glyph id and indexes a flat view of this, so
        # the composed shape and the work per cell are exactly what they were
        # before orientation existed (measured at 1.00x). 96KB for the
        # shipped 8x12 set, small enough to stay resident.
        self._variants = np.empty(
            (_VARIANTS, CHAR_COUNT, char_height, char_width), dtype=np.uint8
        )
        # Variant 0 IS the upright table, as a view -- one store, so writing a
        # pattern through `glyph[id]` cannot leave the two disagreeing.
        self._glyphs = self._variants[0]
        self._glyphs[:] = (
            np.asarray(atlas.data, dtype=np.uint8)
            .reshape(
                ATLAS_ROWS,
                char_height,
                ATLAS_COLS,
                char_width,
            )
            .transpose(0, 2, 1, 3)
            .reshape(CHAR_COUNT, char_height, char_width)
        )
        self._reflect()
        # A flat (variant * 256 + glyph) view of the same buffer, so the
        # gather is one index array and stays a view of the writes above.
        self._flat = self._variants.reshape(
            _VARIANTS * CHAR_COUNT, char_height, char_width
        )
        self._char_width = char_width
        self._char_height = char_height
        self._palette = system_palette()
        self._pen = Pen(0, 0, 0, 0)
        # The second brush, beside the pen: which way subsequent writes face.
        # `_slot` is the same value as a dense variant index, kept alongside
        # so the write path never converts.
        self._orientation = Orientation.NONE
        self._slot = 0
        # Cell storage is x-major -- _chars[x, y] -- so every index in this
        # file reads the way the API does. Only _image.pixels stays row-major,
        # because that is the GPU's byte order rather than our choice.
        self._chars = np.empty((self._cols, self._rows), dtype=np.uint8)
        # uint16 so the folded `_orient * CHAR_COUNT + _chars` index needs no
        # widening cast on the render path.
        self._orient = np.zeros((self._cols, self._rows), dtype=np.uint16)
        # Colour is screen-shaped -- one pen per PIXEL, not per cell. A plain
        # Pen paints a cell's slab with four constants and behaves exactly as
        # it always has; the room this leaves is for a pen that varies within
        # the cell, which a cell-shaped buffer could only hold the average of.
        self._colours = np.empty(
            (self._rows * char_height, self._cols * char_width, 4), dtype=np.uint8
        )
        self._cell = _CellAccess(self)
        self._glyph = _GlyphAccess(self)
        self._image = PixelBuffer(
            ImageSpec(
                size=(self._cols * char_width, self._rows * char_height),
                mode="P",
                palette=self._palette,
                transparency_index=0,
            )
        )
        # The renderer: the whole composed screen image as ONE tile on an
        # unattached TileLayer -- the same draw path every other surface
        # takes. The stamp itself is deferred to first composite: blit()
        # needs a current GL context, construction must not.
        self._renderer = TileLayer(
            self._image,
            self._image.width,
            self._image.height,
        )
        self._stamped = False
        self._collision_mask = np.zeros(
            (self._image.height, self._image.width // 32), dtype=CWORD
        )
        self._collision_source: Optional[np.ndarray] = None
        self.clear()
        # IS-A Layer: content and place are one object. See TileLayer.
        super().__init__()

    @staticmethod
    def _validate_char_size(value: object) -> tuple[int, int]:
        if not isinstance(value, tuple) or len(value) != 2:
            raise TypeError("charset must be a CharSet or a (width, height) tuple")

        width, height = value
        if (
            not isinstance(width, int)
            or isinstance(width, bool)
            or not isinstance(height, int)
            or isinstance(height, bool)
        ):
            raise TypeError("character width and height must be integers")
        if width <= 0 or height <= 0:
            raise ValueError(
                f"character width and height must be positive, got {width}x{height}"
            )
        return width, height

    @staticmethod
    def _screen_grid(width: int, height: int) -> tuple[int, int]:
        screen = Config().display
        if screen.width % width or screen.height % height:
            raise ValueError(
                f"character size {width}x{height} does not tile the "
                f"{screen.width}x{screen.height} screen"
            )
        return screen.width // width, screen.height // height

    @property
    def cols(self) -> int:
        return self._cols

    @property
    def rows(self) -> int:
        return self._rows

    @property
    def palette(self) -> Palette:
        return self._palette

    @palette.setter
    def palette(self, value: Palette) -> None:
        if not isinstance(value, Palette):
            raise TypeError(f"expected Palette, got {type(value)}")
        self._palette = value
        self._image.palette = value

    @property
    def pen(self) -> Pen | MagicPen:
        return self._pen

    @pen.setter
    def pen(self, value: Pen | MagicPen) -> None:
        if isinstance(value, MagicPen):
            if value.size != (self._char_width, self._char_height):
                raise ValueError(
                    f"magic pen is {value.size[0]}x{value.size[1]}; this layer's "
                    f"cells are {self._char_width}x{self._char_height}"
                )
        elif not isinstance(value, Pen):
            raise TypeError(f"pen must be a Pen or MagicPen, got {type(value)}")
        self._pen = value

    @property
    def orientation(self) -> Orientation:
        """Which way subsequent cell writes face -- the second brush, beside
        `pen`. Set it, then write; the cells you already wrote keep what they
        were stamped with, exactly as the pen's slots do.

            layer.orientation = Orientation.FLIP_H
            layer.cell[x, y] = ord("R")          # draws a passable Я

        Only the four orientations that preserve a rectangle are accepted --
        NONE, FLIP_H, FLIP_V, ROT_180. A quarter turn would need a cell
        `height x width` instead of `width x height`, which is a different
        grid, so it is refused rather than silently squashed. The 8x8 charset
        is square and could take all eight; it does not get to, because a
        rule that holds for every charset beats a capability that holds for
        some of them.

        Deliberately NOT a property of `Pen`. A pen answers one question --
        what four indices go in a cell's colour slab -- and orientation
        answers a different one, about the glyph pattern. Putting it on the
        pen would also fuse the letter's facing to the ink's, and those are
        independent: a MagicPen's array is already mirrorable on its own
        (`pen.pixels[:] = pen.pixels[:, ::-1]`), so the two combine rather
        than gating each other."""
        return self._orientation

    @orientation.setter
    def orientation(self, value: Orientation) -> None:
        flags = Orientation(value)
        if flags not in LEGAL_ORIENTATIONS:
            raise ValueError(
                f"a {self._char_width}x{self._char_height} glyph cell cannot "
                f"take {flags!r}: a quarter turn would make it "
                f"{self._char_height}x{self._char_width}. Glyph cells take "
                f"NONE, FLIP_H, FLIP_V or ROT_180"
            )
        self._orientation = flags
        # TRANSPOSE is clear, so the value is 2*H + 4*V and >> 1 is the dense
        # variant index. See LEGAL_ORIENTATIONS.
        self._slot = flags >> 1

    @property
    def cell(self) -> _CellAccess:
        return self._cell

    @property
    def glyph(self) -> _GlyphAccess:
        return self._glyph

    def clear(self) -> None:
        """Set every cell to glyph 0, painted with the current pen and facing
        the current orientation -- both brushes, the same way round."""
        self._chars.fill(0)
        self._orient.fill(self._slot)
        self._flood()
        self._refresh()

    def scroll(self, amount: tuple[int, int]) -> None:
        """Move the cell grid by ``(x, y)`` cells without wrapping.

        Positive x moves contents right and positive y moves them down.
        Moved cells keep their colours and their facing; newly exposed cells
        are glyph 0 in the current pen and the current orientation.
        """
        if not isinstance(amount, tuple) or len(amount) != 2:
            raise TypeError("scroll amount must be an (x, y) tuple")
        x, y = amount
        if (
            not isinstance(x, int)
            or isinstance(x, bool)
            or not isinstance(y, int)
            or isinstance(y, bool)
        ):
            raise TypeError("scroll x and y must be integers")
        if x == 0 and y == 0:
            return

        src_x0 = max(-x, 0)
        src_x1 = self._cols - max(x, 0)
        src_y0 = max(-y, 0)
        src_y1 = self._rows - max(y, 0)

        gw = self._char_width
        gh = self._char_height

        if src_x0 < src_x1 and src_y0 < src_y1:
            chars = self._chars[src_x0:src_x1, src_y0:src_y1].copy()
            # Facing travels with the character it belongs to -- scrolling
            # moves cells, and a cell is its glyph, its ink and its facing.
            orient = self._orient[src_x0:src_x1, src_y0:src_y1].copy()
            # Colour moves in pixels, because that is how it is stored -- the
            # slab of a moved cell travels intact, whatever is inside it.
            colours = self._colours[
                src_y0 * gh : src_y1 * gh, src_x0 * gw : src_x1 * gw
            ].copy()
        else:
            chars = None
            orient = None
            colours = None

        self._chars.fill(0)
        self._orient.fill(self._slot)
        self._flood()

        if chars is not None and colours is not None and orient is not None:
            dst_x0 = max(x, 0)
            dst_y0 = max(y, 0)
            dst_x1 = dst_x0 + chars.shape[0]
            dst_y1 = dst_y0 + chars.shape[1]
            self._chars[dst_x0:dst_x1, dst_y0:dst_y1] = chars
            self._orient[dst_x0:dst_x1, dst_y0:dst_y1] = orient
            self._colours[
                dst_y0 * gh : dst_y1 * gh, dst_x0 * gw : dst_x1 * gw
            ] = colours

        self._refresh()

    def _reflect(self, byte: Optional[int] = None) -> None:
        """Derive the mirrored variants from the upright ones -- all 256
        patterns, or one when a pattern is replaced. Slot order is the dense
        `H + 2*V` from LEGAL_ORIENTATIONS: 1 mirrors x, 2 mirrors y, 3 both.
        A glyph is (gh, gw), so x is axis -1 and y is axis -2."""
        source = self._variants[0] if byte is None else self._variants[0, byte]
        target = self._variants if byte is None else self._variants[:, byte]
        target[1] = source[..., ::-1]
        target[2] = source[..., ::-1, :]
        target[3] = source[..., ::-1, ::-1]

    def _set_pattern(self, byte: int, pattern: np.ndarray) -> None:
        """Replace one glyph's upright pattern and re-derive its mirrors, so
        a font edited at runtime stays consistent in every orientation."""
        self._variants[0, byte] = pattern
        self._reflect(byte)

    def _flood(self) -> None:
        """Repaint every cell's colour with the current pen. A plain pen's four
        constants broadcast over the whole buffer; a magic pen tiles, one copy
        per cell -- which is what makes `clear()` under a magic pen come back as
        a screen of that pen rather than a screen of one stretched copy."""
        paint = self._pen.paint
        if isinstance(paint, np.ndarray):
            # Cell-sized by construction -- the pen setter checked -- so this
            # tiles exactly, no remainder.
            self._colours[:] = np.tile(paint, (self._rows, self._cols, 1))
        else:
            self._colours[:] = paint

    def _paint(self, x: int, y: int, colour) -> None:
        """Fill one cell's colour slab. A four-index pen broadcasts flat across
        it; a (gh, gw, 4) array lands verbatim. numpy sorts out which."""
        top = y * self._char_height
        left = x * self._char_width
        self._colours[
            top : top + self._char_height, left : left + self._char_width
        ] = colour

    def _refresh(self) -> None:
        # Cells are (x, y, gh, gw); the image is row-major, so the transpose
        # to (y, gh, x, gw) is where the two orders meet -- once, here. Colour
        # is already screen-shaped, so it takes no part in the reshuffle.
        # Orientation folds into the glyph id rather than branching: one
        # gather from the variant table, the same output shape as an
        # unoriented grid, so this costs what it always did.
        planes = (
            self._flat[self._orient * CHAR_COUNT + self._chars]
            .transpose(1, 2, 0, 3)
            .reshape(self._colours.shape[:2])
        )
        self._image.pixels[:] = np.take_along_axis(
            self._colours, planes[..., None], axis=2
        )[..., 0]
        self._image.dirty = True

    def _stamp(self, x: int, y: int) -> None:
        glyph = self._variants[self._orient[x, y], self._chars[x, y]]
        glyph_height, glyph_width = glyph.shape
        top = y * glyph_height
        left = x * glyph_width
        colours = self._colours[top : top + glyph_height, left : left + glyph_width]
        self._image.pixels[
            top : top + glyph_height, left : left + glyph_width
        ] = np.take_along_axis(colours, glyph[..., None], axis=2)[..., 0]
        self._image.dirty = True

    @property
    def collidable(self) -> bool:
        return True

    @property
    def collision_mask(self) -> np.ndarray:
        source = self._image.mask
        if source is not self._collision_source:
            packed = np.packbits(source, axis=1, bitorder="little")
            self._collision_mask[:] = np.ascontiguousarray(packed).view(CWORD).reshape(
                self._collision_mask.shape
            )
            self._collision_source = source
        return self._collision_mask

    def collides_with(self, other: Collidable) -> CollisionQuery:
        if not other.collidable:
            raise ValueError(f"collides_with() target {other!r} is not collidable")
        return CollisionQuery(self, other)

    @property
    def texture(self) -> Texture:
        return self._image.texture

    @property
    def size(self) -> Size:
        return self._image.size

    @property
    def indexed(self) -> bool:
        return self._image.indexed

    @property
    def transparency_index(self) -> Optional[int]:
        return self._image.transparency_index

    def prepare(self) -> None:
        self._renderer.prepare()

    def _composite(self) -> None:
        if not self._stamped:
            self._renderer.blit(0, 0, 0)
            self._stamped = True
        self._renderer._composite()

