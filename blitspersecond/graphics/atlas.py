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

from .collision import pack
from .orientation import Orientation
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
    reaching through to `buffer`. A console guards `sheet.buffer.mode`, which is a
    question about the sheet it was given and should read like one.

    Margins and inter-cell spacing (Tiled supports both) are NOT handled by
    the autogrid. Nothing in BPS ships an atlas that needs them yet, and
    guessing the parameters would be worse than the raise you get from an
    image that does not divide -- and a sheet that genuinely has them can be
    given its rects outright, which is the escape hatch the second road
    already is.
    """

    __slots__ = (
        "_buffer",
        "_rects",
        "_names",
        "_cell",
        "_cols",
        "_rows",
        "_solid",
        "_primed",
        "_uvs",
    )

    def __init__(
        self,
        buffer: PixelBuffer,
        width: Union[int, Mapping[str, Rect]],
        height: Optional[int] = None,
    ) -> None:
        if not isinstance(buffer, PixelBuffer):
            raise TypeError(f"an atlas cuts up a PixelBuffer; got {type(buffer)}")
        self._buffer = buffer
        # Per-tile solidity, filled on first use. See `solidity`.
        self._solid: dict[int, list[Optional[np.ndarray]]] = {}
        self._uvs: Optional[np.ndarray] = None
        self._primed: bool = False
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

        **Every question about the IMAGE is asked here**, not repeated on the
        atlas: `size`, `mode`, `indexed`, `palette`, `edit()`, `copy()`, the
        texture. An atlas answers about the CUTTING -- the rects, the keys,
        and what is derived from a rect -- and proxying the image's own
        properties would be a second surface saying the same thing, when this
        one is public and one word away.

        `indexed` is the question a consumer asks before deciding it can use a
        sheet at all, and it asks the buffer.
        """
        return self._buffer

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

    def _prime(self, source: np.ndarray) -> None:
        """Pack every rect's UPRIGHT pattern, now, in one pass.

        The turned variants stay lazy; this is the one orientation every rect
        is certain to want. It runs on the first `solidity` call rather than
        at construction, so an atlas nobody collides against never packs
        anything -- and never forces the bool mask, which is the same bytes
        again as the image.

        Eagerly, to avoid a CLUSTER rather than to save one rect's 8us: a
        scene bringing two hundred new poses into view in one frame would pack
        them all in that frame. `bps/reference/atlas.md` has the figures, and
        the whole-sheet `packbits` form that was measured and rejected.
        """
        for key in self.keys():
            rect = self.rect(key)
            self._solid[self._row(key)] = [
                pack(
                    source[
                        rect.y : rect.y + rect.height,
                        rect.x : rect.x + rect.width,
                    ]
                )
            ] + [None] * 7

    def solidity(
        self, key: Union[int, str], orientation: Orientation = Orientation.NONE
    ) -> np.ndarray:
        """One rect's solid pixels, turned, as packed row bit patterns.

            atlas.solidity(42, Orientation.FLIP_H)

        `(height, words)` uint64, one bit per pixel along x -- the layout in
        `graphics.collision.packed`, ready to be shifted into place and OR'd
        into a screen-space mask. Height and width swap under the four
        transposing orientations.

        **Per rect, and it has to be**: an orientation belongs to the
        placement, so the pixels alone cannot answer this -- it takes a rect
        and a turn together, and the atlas is the only thing holding both.
        Each rect's eight variants fill lazily.

        **There is no staleness to detect and this is the wrong place to
        police it.** A sheet is immutable, so these are computed once and stay
        correct for as long as the atlas lives. An atlas holds a REFERENCE to
        a buffer it neither made nor owns; watching for a repainted one would
        leave new stamps correct and already-stamped screen bits stale, which
        is worse than either end of it. Do not add a watch back.

        Transpose first, then the mirrors -- the order the flag bits document.
        Getting it backwards is invisible on a square symmetric rect and wrong
        on everything else.

        Indexed surfaces only: solidity is palette index 0 meaning
        transparent, and `PixelBuffer.mask` says so if asked of a direct one.

        Why it lives here rather than in collision, and what sharing it buys:
        `bps/reference/atlas.md`, "Solidity: one rect, turned, packed".
        """
        source = self._buffer.mask
        if not self._primed:
            self._primed = True
            self._prime(source)
        # `_row` is the CACHE key, not an address: a named atlas resolves
        # only by name, so the rect has to be asked for with the key the
        # caller gave rather than with the row it resolved to.
        row = self._row(key)
        turn = int(orientation) & 7
        variants = self._solid.get(row)
        if variants is None:
            variants = [None] * 8
            self._solid[row] = variants
        pattern = variants[turn]
        if pattern is None:
            rect = self.rect(key)
            bits = source[
                rect.y : rect.y + rect.height, rect.x : rect.x + rect.width
            ]
            # Transpose first, then the mirrors -- the order the flag bits
            # document and the order `sprite.frame.local_rect` applies. All
            # three are views; only `pack` materialises anything. Getting it
            # backwards is invisible on a square, symmetric tile and wrong on
            # everything else.
            if turn & int(Orientation.TRANSPOSE):
                bits = bits.T
            if turn & int(Orientation.FLIP_H):
                bits = bits[:, ::-1]
            if turn & int(Orientation.FLIP_V):
                bits = bits[::-1, :]
            pattern = pack(bits)
            variants[turn] = pattern
        return pattern

    def rect(self, key: Union[int, str]) -> Rect:
        """Where rect `key` sits in the image -- `Rect(x, y, width, height)`,
        origin top-left.

            atlas[3]            the pixels
            atlas.rect(3)       the place those pixels came from
            named.rect("blue")  the same question, keyed by name

        Two questions about one rect, and which you want depends on whether
        you compose on the CPU or on the GPU -- see `bps/reference/atlas.md`,
        "What comes out of a rect".
        """
        x, y, w, h = self._rects[self._row(key)]
        return Rect(int(x), int(y), int(w), int(h))

    @property
    def uvs(self) -> np.ndarray:
        """Every rect's four texture corners: `(n, 4, 3)` float32.

            atlas.rect(3)       where rect 3 sits in the IMAGE, in pixels
            atlas.uvs[3]        the same rect as the SAMPLER wants it

        Corner order is this engine's -- (x1,y1)(x2,y1)(x2,y2)(x1,y2), so TL,
        TR, BR, BL -- matching `TileBatch.set_quad` and `_UV_PERMS`, which
        permutes these four rows to turn a placement.

        **Built once for the whole sheet, because it cannot change**: a rect
        table is fixed at construction and a sheet is one immutable texture.

        **Derived from the texture's OWN `tex_coords`, not from `0..1`**, so a
        driver that pads to a power of two is followed rather than assumed.
        `tests/test_atlas.py` checks every rect against `get_region`, which is
        the authority this arithmetic replaces.

        Lazy, because it needs a live GL context to ask the texture its size
        and an `Atlas` can be built without one.
        """
        if self._uvs is None:
            texture = self._buffer.texture
            corners = texture.tex_coords
            u0, v0 = float(corners[0]), float(corners[1])
            u1, v1 = float(corners[6]), float(corners[7])
            across = (u1 - u0) / texture.width
            down = (v1 - v0) / texture.height
            rects = self._rects.astype(np.float64)
            x, y, w, h = rects[:, 0], rects[:, 1], rects[:, 2], rects[:, 3]
            left, right = u0 + x * across, u0 + (x + w) * across
            top, bottom = v0 + y * down, v0 + (y + h) * down
            uvs = np.zeros((len(rects), 4, 3), dtype=np.float32)
            uvs[:, 0, 0], uvs[:, 0, 1] = left, top
            uvs[:, 1, 0], uvs[:, 1, 1] = right, top
            uvs[:, 2, 0], uvs[:, 2, 1] = right, bottom
            uvs[:, 3, 0], uvs[:, 3, 1] = left, bottom
            self._uvs = uvs
        return self._uvs

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
