"""An image cut into rects, addressed by key.

    grid  = Atlas(PixelBuffer("tiles/overworld.png"), 16, 16)
    named = Atlas(PixelBuffer("chun-li.png"), {"hit-01": Rect(1, 1, 83, 101)})

    len(atlas)          # how many rects
    atlas[key]          # one rect's PIXELS
    atlas.rect(key)     # the rect itself

**An atlas is a set of rects over one image.** Giving a cell size is an
AUTOGRID -- a way of generating those rects rather than a second kind of
atlas. See the class docstring.
"""

from __future__ import annotations

from typing import Iterator, Mapping, Optional, Union

import numpy as np

from blitspersecond.common import Rect, Size

from .palette import Palette
from .pixel_buffer import PixelBuffer

#: One row per rect: x, y, width, height. int32 rather than a list of `Rect`
#: because the rects are bulk data, not objects -- measured 2026-09-01, a
#: pathological 262,144-cut atlas is 4MB here against 21MB of NamedTuples, and
#: the autogrid fills it with four vectorised writes instead of a Python loop
#: (14us against 132ms). `Rect` is what comes back OUT, one at a time.
RECT_DTYPE = np.int32


class Atlas:
    """A `PixelBuffer` cut into rects, addressed by key.

        glyphs = Atlas(buffer, 8, 12)                        # autogrid
        cuts   = Atlas(buffer, {"blue": Rect(0, 0, 8, 8)})   # rects given

        len(atlas)          # how many rects
        atlas[key]          # one rect's pixels, an (height, width) view
        atlas.rect(key)     # where it sits

    ## An atlas is rects, and a grid is a way to make them

    Every pillar that draws does the same thing with an image: cut it into
    rects and send those rects to the GPU as quads. Kris, 2026-09-01:

        "we have 3 things now that are the same thing -- the console glyphs,
        a tileset and now the tiles a sprite uses. each is a large image cut
        into rects that gets funneled through a drawing pipeline and
        eventually ends up as quads on a gpu"

        "if we consider cells, rows etc on an Atlas as an autogrid -- then
        it's still rects"

    That second remark is the one this class is built on, and it is stronger
    than it looks. There are not two kinds of atlas with a shared interface:
    **there is one atlas, holding rects.** A charset and a tilemap hand over a
    cell size, and the grid those numbers describe is a *generator* -- it
    fills the same rect table an authored package fills by listing its
    rectangles. Chun-Li's sheet holds 1741 rects in 982 distinct sizes; a
    charset holds 256 identical ones. Same table.

    So there is one storage and one lookup, and `cell`, `cols` and `rows`
    are not a second mode -- they are **the autogrid's parameters, kept
    because the consumer that supplied them usually needs them back.** A
    `TileMap`'s world cell size IS its sheet's cell size; a `Console` measures
    its screen in glyphs. An atlas given its rects outright has no such
    parameters and says so when asked.

    ## What varies is the key, and only that

    An autogrid numbers its rects left to right then down, so index `n` is at
    column `n % cols`, row `n // cols`, and negative indices count from the
    end. Rects given outright are keyed by the names their author chose, in
    the order the author wrote them.

    Nothing here knows what the rects are FOR: each engine brings its own rule
    about how many it needs and what a key means.

    ## The autogrid's one demand

    **The image does not have to be square, or a power of two, or any
    particular grid.** It has to divide evenly by the cell size, and that is
    the only demand. The console previously assumed every charset atlas was
    16x16 glyphs, which was true of the two images that happened to exist and
    was never a rule -- so `len()` is the question an engine should ask ("does
    this hold the 256 I need?") rather than the shape.

    Rects given outright are validated instead: positive, non-negative origin,
    and wholly inside the image. That check is at construction because the
    alternative is a NumPy slice quietly returning fewer rows than the rect
    said -- a picture subtly wrong with nothing reporting it.

    ## Reads are views

    `atlas[key]` hands back a VIEW into the source buffer's pixels: editing it
    edits the image, which is what a tool wants and what an engine loading
    from a file should copy out of. Rects may overlap, abut or leave gaps --
    an autogrid's never do, an authored package's may, and the atlas takes
    them as given either way.

    **It carries the image's metadata too** -- `size`, `mode`, `indexed`,
    `palette` -- so an engine handed a sheet can ask the sheet rather than
    reaching through to `buffer`. A console guards `sheet.indexed`, which is a
    question about the sheet it was given and should read like one.

    Margins and inter-cell spacing (Tiled supports both) are NOT handled by
    the autogrid. Nothing in BPS ships an atlas that needs them yet, and
    guessing the parameters would be worse than the raise you get from an
    image that does not divide -- and a sheet that genuinely has them can be
    given its rects outright, which is the escape hatch the second road
    already is.
    """

    __slots__ = ("_buffer", "_rects", "_names", "_cell", "_cols", "_rows")

    def __init__(
        self,
        buffer: PixelBuffer,
        width: Union[int, Mapping[str, Rect]],
        height: Optional[int] = None,
    ) -> None:
        if not isinstance(buffer, PixelBuffer):
            raise TypeError(f"an atlas cuts up a PixelBuffer; got {type(buffer)}")
        self._buffer = buffer
        if isinstance(width, Mapping):
            if height is not None:
                raise TypeError(
                    "Atlas(buffer, rects) takes named rects OR a cell size, "
                    "not both -- rects given outright need no grid to generate "
                    "them"
                )
            self._given(width)
        else:
            if height is None:
                raise TypeError(
                    "Atlas(buffer, width, height) needs a cell height, or pass "
                    "a mapping of name -> Rect to give the rects outright"
                )
            self._autogrid(int(width), int(height))

    def _autogrid(self, width: int, height: int) -> None:
        """Generate the rect table from a cell size.

        Four vectorised writes, not a Python loop: the table is bulk data and
        an atlas may hold a lot of it.
        """
        if width <= 0 or height <= 0:
            raise ValueError(f"atlas cell size must be positive, got {width}x{height}")
        image_width, image_height = self._buffer.size
        if image_width % width or image_height % height:
            raise ValueError(
                f"a {image_width}x{image_height} image does not divide into "
                f"{width}x{height} cells"
            )
        self._cell = Size(width, height)
        self._cols = cols = image_width // width
        self._rows = rows = image_height // height
        self._names = None

        count = cols * rows
        index = np.arange(count, dtype=RECT_DTYPE)
        rects = np.empty((count, 4), dtype=RECT_DTYPE)
        rects[:, 0] = (index % cols) * width
        rects[:, 1] = (index // cols) * height
        rects[:, 2] = width
        rects[:, 3] = height
        self._rects = rects

    def _given(self, rects: Mapping[str, Rect]) -> None:
        """Take the rect table as authored, validated against the image."""
        if not rects:
            raise ValueError("an atlas needs at least one rect")
        width, height = self._buffer.size
        table = np.empty((len(rects), 4), dtype=RECT_DTYPE)
        names: dict = {}
        for row, (name, raw) in enumerate(rects.items()):
            x, y, w, h = (int(v) for v in raw)
            if w <= 0 or h <= 0:
                raise ValueError(f"rect {name!r}: non-positive size in {(x, y, w, h)}")
            if x < 0 or y < 0:
                raise ValueError(f"rect {name!r}: negative origin in {(x, y, w, h)}")
            if x + w > width or y + h > height:
                raise ValueError(
                    f"rect {name!r}: {(x, y, w, h)} runs outside the "
                    f"{width}x{height} image"
                )
            table[row] = (x, y, w, h)
            names[str(name)] = row
        self._rects = table
        self._names = names
        # No grid generated these, so there are no grid parameters to report.
        self._cell = None
        self._cols = 0
        self._rows = 0

    # -- the image ---------------------------------------------------------

    @property
    def buffer(self) -> PixelBuffer:
        """The image being cut up. Live -- an atlas is a way of reading it,
        not a copy of it.

        Reach for this to DO something to the image: `edit()`, `copy()`, the
        `texture`. What the image IS -- `size`, `mode`, `indexed`, `palette`
        -- is repeated below, so a caller asking a question about the sheet
        asks the sheet."""
        return self._buffer

    @property
    def size(self) -> Size:
        """The whole image's `(width, height)` in pixels."""
        return self._buffer.size

    @property
    def mode(self) -> str:
        """The image's mode -- `"P"`, `"RGB"`, `"RGBA"`."""
        return self._buffer.mode

    @property
    def indexed(self) -> bool:
        """Whether the pixels hold palette indices rather than colours.

        The question an engine asks before deciding it can use a sheet at
        all: a console's glyphs must be indexed (a glyph is a stencil of
        plane indices), and a tileset generally is not."""
        return self._buffer.indexed

    @property
    def palette(self) -> Optional[Palette]:
        """The table the pixels index into, or None if they hold colours.

        Here rather than only on the buffer because a sheet of indices
        without its palette is unreadable -- they are one fact about the
        image, and separating them invites resolving pixels against the wrong
        table."""
        return self._buffer.palette

    # -- the autogrid's parameters -----------------------------------------

    @property
    def gridded(self) -> bool:
        """Whether an autogrid generated these rects.

        The question a consumer that NEEDS the grid parameters asks once, at
        construction, so it can refuse with its own reason rather than
        tripping over `cell` somewhere later. A `TileMap` needs it because a
        world cell is a sheet cell; a `Console` needs it to measure a screen
        in glyphs.
        """
        return self._names is None

    def _require_grid(self, member: str) -> None:
        """Refuse an autogrid parameter on an atlas that was given its rects.

        Not a mode check -- the rects are the same rects either way. It is
        that `cell`, `cols`, `rows` and `cells` describe a generator this
        atlas did not use, so there is no honest number to return.
        """
        if self._names is not None:
            raise TypeError(
                f"{member} is an autogrid parameter, and this atlas was given "
                f"its {len(self._names)} rects outright. Use rect(name) for "
                f"where one sits; a uniform cell size is not something it has."
            )

    @property
    def cell(self) -> Size:
        """The autogrid's cell `(width, height)` in pixels."""
        self._require_grid("cell")
        assert self._cell is not None
        return self._cell

    @property
    def cols(self) -> int:
        """The autogrid's columns."""
        self._require_grid("cols")
        return self._cols

    @property
    def rows(self) -> int:
        """The autogrid's rows."""
        self._require_grid("rows")
        return self._rows

    @property
    def cells(self) -> np.ndarray:
        """Every rect's pixels stacked as `(len(self), height, width)`,
        contiguous -- plus a channel axis if the source image has one.

        A COPY, and unavoidably so: rects are strided in the source image, so
        stacking them in key order is a gather. Use it to fill a table once at
        load; use `atlas[key]` to look at one.

        An autogrid parameter in disguise, and so restricted to one: rects
        given outright have no common shape to stack into a densely indexed
        array. That is the same fact `applies_to` states from the other side
        about turning a rectangle.
        """
        self._require_grid("cells")
        assert self._cell is not None
        pixels = self._buffer.pixels
        channels = pixels.shape[2:]
        # reshape splits each axis, transpose reorders the four -- a view
        # until the final reshape gathers it, which is the copy above.
        return (
            pixels.reshape(
                self._rows, self._cell.height, self._cols, self._cell.width, *channels
            )
            .transpose(0, 2, 1, 3, *range(4, 4 + len(channels)))
            .reshape(len(self), self._cell.height, self._cell.width, *channels)
        )

    # -- the rects ---------------------------------------------------------

    def _row(self, key: Union[int, str]) -> int:
        """Which row of the rect table `key` names.

        The one place a key is resolved, so `rect` and `__getitem__` cannot
        come to disagree about which rect a key means -- they answer two
        questions about the same one, and are only useful while they agree.
        """
        names = self._names
        if names is not None:
            try:
                return names[key]  # pyright: ignore[reportArgumentType]
            except (KeyError, TypeError):
                known = sorted(names)
                shown = ", ".join(repr(k) for k in known[:8])
                if len(known) > 8:
                    shown += f", ... ({len(known)} total)"
                raise KeyError(
                    f"no rect named {key!r} in this atlas -- it holds {shown}"
                ) from None
        count = len(self._rects)
        row = int(key)  # pyright: ignore[reportArgumentType]
        if row < 0:
            row += count
        if not 0 <= row < count:
            raise IndexError(
                f"cell {key} outside atlas 0..{count - 1} "
                f"({self._cols}x{self._rows} cells)"
            )
        return row

    def rect(self, key: Union[int, str]) -> Rect:
        """Where rect `key` sits in the image -- `Rect(x, y, width, height)`,
        origin top-left.

            atlas[3]            the pixels
            atlas.rect(3)       the place those pixels came from
            named.rect("blue")  the same question, keyed by name

        Two questions about one rect, and which you want depends on whether
        you compose on the CPU or on the GPU. A console gathers pixels into a
        screen it paints itself, so it wants the first. A tilemap or a sprite
        never touches pixel data on the render path -- it hands a rectangle to
        the hardware and lets the sampler do the reading -- so it wants the
        second.

        It exists as a method rather than as a type on purpose. An earlier
        engine handed out a `Tile` object carrying its source rect and its
        size, pooled globally to afford the allocation; that whole apparatus
        was serving four integers. `TILEMAP_PORT.md` F1 left this parked until
        a second caller appeared -- *"a method returning four numbers, not a
        type ... do not pre-build it"* -- and the sprite pillar was that
        caller. One table answers for both, which is what lets one draw path
        serve an autogrid and an authored package alike.
        """
        x, y, w, h = self._rects[self._row(key)]
        return Rect(int(x), int(y), int(w), int(h))

    def __getitem__(self, key: Union[int, str]) -> np.ndarray:
        """One rect's pixels as an `(height, width)` view.

        Autogrid rects are numbered left to right then down and take negative
        indices; rects given outright take their author's string.

        A VIEW into the source image: editing it edits the image. `rect(key)`
        is the same rect's place rather than its pixels.
        """
        x, y, w, h = self._rects[self._row(key)]
        return self._buffer.pixels[y : y + h, x : x + w]

    def __contains__(self, key: object) -> bool:
        """Whether this atlas holds a rect under `key`.

        The membership test a loader wants: checking that every part of an
        assembly names a rect that exists, before anything tries to draw one.
        """
        try:
            self._row(key)  # pyright: ignore[reportArgumentType]
        except (KeyError, IndexError, TypeError, ValueError):
            return False
        return True

    def keys(self) -> Iterator[Union[int, str]]:
        """Every key this atlas answers to, in table order.

        `range(len(self))` for an autogrid; the authored names, in the order
        they were given, otherwise -- a package is an authored file and its
        order is the author's, so it is preserved rather than sorted.
        """
        if self._names is not None:
            yield from self._names
        else:
            yield from range(len(self))

    def __len__(self) -> int:
        """How many rects the atlas holds.

        One question with one meaning whichever way the table was filled,
        which is why it is not an autogrid parameter. This is the question to
        ask an atlas: a charset needs 256 and does not care whether they
        arrive as 16x16 or 32x8.
        """
        return len(self._rects)

    def __iter__(self) -> Iterator[np.ndarray]:
        """Every rect's pixels, in `keys()` order."""
        for key in self.keys():
            yield self[key]

    def __repr__(self) -> str:
        if self._names is not None:
            return f"<Atlas {len(self._rects)} rects given>"
        assert self._cell is not None
        return (
            f"<Atlas {self._cols}x{self._rows} cells of "
            f"{self._cell.width}x{self._cell.height}, {len(self)} total>"
        )
