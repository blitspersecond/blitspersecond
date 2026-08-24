# The console: teletype semantics over a glyph layer -- cursor, write(), wrap,
# newline, scrolling, and input. It is a controller, not a layer; the
# GlyphLayer it controls is the thing in the draw order.
#
# ---------------------------------------------------------------------------
# API DECISIONS (2026-08-21, branch `inversion`) -- why this surface is shaped
# the way it is. The narrative version is in docs/bps/design-log.md under
# "Console joins the inversion"; this is the reconstructable summary, kept
# next to the code it constrains. Read before "simplifying" any of it.
#
# This block is the SPEC, not a description of the current internals. The
# graphics component is to be rebuilt against these decisions, so everything
# here must survive its implementation being deleted. Each entry carries:
#
#   DECISION    -- what the surface promises.
#   REQUIREMENT -- what that promise is IN SERVICE OF. Without this a
#                  rebuilder can only inherit the decision, never honour its
#                  intent or challenge it on the merits.
#   EVIDENCE    -- what backs it: a measurement, an observed failure, or a
#                  standing principle. Implementation convenience is NOT
#                  evidence and never appears here; the capability and
#                  performance questions were settled by the experiments
#                  before this API was designed.
#
# Same shape applies to the tile / sprite / glyph blocks. Shared vocabulary
# that all three owe lives in the cross-pillar block, not restated here.
#
# 1. DECISION: Console is NOT a Layer -- it is a controller over a GlyphLayer,
#    and `layers.add` accepts it without it becoming one. A re-decision, not
#    an oversight: 2026-07-21 briefly made it Layerable, 2026-07-22 demoted it
#    back within the day, and that demotion stands. What changed 2026-08-21 is
#    only that `add` now takes a controller; the wrapper stays a wrapper.
#    REQUIREMENT: teletype semantics (cursor, wrap, scroll, echo) are not a
#    compositing concern, and the same glyph grid must be drivable WITHOUT
#    them. Two audiences, one surface each: a game wanting text gets Console,
#    a game wanting the grid gets GlyphLayer directly.
#
#    The split also decides HOW HARD TO TRY on ergonomics: the normal path
#    earns convenience, the advanced path earns power and honesty. A caller
#    who has reached `.layer` to define their own glyphs has NumPy in hand,
#    so sugar there buys little and costs a name everyone must learn. This
#    is why `glyph.blank()` was built and then removed --
#    `np.zeros_like(layer.glyph[0])` already says it.
#    EVIDENCE: the second audience is real and already documented -- the
#    design-log's "Known gap: Console cannot reach magic pens ... the demo
#    drives GlyphLayer" records a caller who needs the grid without the
#    teletype. (That gap is CLOSED as of 2026-08-22 -- there is one Pen class
#    now and Console can hold any of them -- but it closed because the pen
#    surface was fixed, not because the two audiences merged. They did not.)
#    Fusing Console and layer was tried once and lasted a day.
#
# 2. DECISION: `layers.add(Console(...))` finds the layer through the PUBLIC
#    `.layer` property. Not `isinstance(item, Console)`, not a private
#    `_layer`. `layers.remove` uses the same hook.
#    REQUIREMENT: `add`/`remove` must accept any wrapper without display/
#    knowing wrapper types, and one word must mean "the layer inside this"
#    across every wrapper, so a caller holding a mixed bag needs no idea
#    which kind it has.
#    EVIDENCE: the import cycle is real and verified -- display/layers.py's
#    own module comment records that hoisting the graphics imports breaks a
#    graphics-first import. TileMap already carries `.layer`, and 2026-08-18
#    parked the convergence as "the pillars converge together or the
#    vocabulary is worth less."
#
# 3. DECISION: the GlyphLayer is built EAGERLY in __init__, unattached, and
#    stays unattached until `layers.add` places it.
#    REQUIREMENT: ONE construction law across every graphics type -- build
#    unattached, `add` places -- so a caller never has to know which types
#    defer. Errors that are decidable at construction are raised there.
#    EVIDENCE: the deferred cut (console holds a charset request, mints on
#    add) was built and walked back the same day: it required a half-built
#    `cast(GlyphLayer, None)` state, and it postponed a decidable error -- a
#    grid that cannot tile the screen -- until whenever someone added it.
#    `Layer`'s own docstring already states the law for the three pillars.
#
# 4. DECISION: `Console(cols, rows)` is a GRID, in cells, as two plain ints.
#    Not a tuple, not pixels. It derives whatever cell size tiles the screen
#    into that grid and refuses a grid that cannot divide it. `GlyphLayer`
#    keeps its own `(w, h)` = CELL PIXEL SIZE reading, untouched.
#    REQUIREMENT: a type is constructed in the unit it is measured in.
#    Console is addressed in cells by every other call on it -- `.cols`,
#    `.rows`, `cursor`, `write(at=)` -- so a constructor in pixels is the one
#    member of the surface disagreeing with the rest of it.
#    EVIDENCE: observed failure, not aesthetics. `Console((80, 45))` was read
#    as 80x45-PIXEL cells, silently built an 8x8 grid, and failed later and
#    elsewhere with `cursor (24, 15) outside console 0,0-7,7`. Both readings
#    are plausible, which is exactly why the shapes must differ: the cost of
#    the ambiguity is a failure far from its cause.
#
# 4b. DECISION: the grid form resolves its cell size to a SYSTEM CHARSET
#    (`CharSet.for_cell`) and raises, naming the grids that do work, when no
#    shipped font is that size. It never falls back to blank glyphs.
#    REQUIREMENT: a Console is a TEXT device -- `write`/`print` are its whole
#    point -- so the grid road must arrive at real letters. Blank glyphs are
#    a different intent (a game filling `layer.glyph[...]` itself) and get a
#    different, explicit road: `Console(GlyphLayer((w, h)))`.
#    EVIDENCE: the same failure-far-from-cause bite as #4, in the same
#    constructor, one day later. Forwarding the derived `(8, 8)` to
#    `GlyphLayer` hit ITS tuple reading -- "256 blank glyphs of this size" --
#    so `Console(80, 45)` built a correct 80x45 grid of empty patterns. Every
#    character then stamped plane 0, which paints `pen[0]`, and the screen
#    rendered as one solid block of the background colour. Nothing raised;
#    the geometry was right; only the font was missing. An API that can
#    silently produce a console that cannot show text has not met its
#    requirement, however correct its dimensions.
#
# 5. DECISION: ASSIGNMENT MEANS REPLACE. The load-bearing rule, and the one
#    most likely to be "tidied" back into a bug:
#
#      - assigning an object of the property's own type REPLACES it
#        (`console.pen = pen`, `console.cursor = cursor`)
#      - assigning a plain value SETS or MOVES through to the live object
#        (`console.cursor = (x, y)`, `console.pen[1] = WHITE`)
#      - reach-through properties (`tag`, `layers`, `layer`, `pen`) hold NO
#        duplicate state -- the layer is the single source of truth. The
#        layer owns the brush and sizes it to its own cells; Console is
#        where you reach for it, not where it lives.
#
#    REQUIREMENT: identical syntax must mean the same thing regardless of
#    which accessor it was reached through, and a caller holding a reference
#    must be able to predict whether it stays live. Whether you get a copy or
#    the real thing cannot depend on trivia the call site does not show.
#    EVIDENCE: both replace cases were previously fake, and fake ONLY through
#    Console -- `layer.pen = ...` always rebound properly, so the semantics
#    genuinely did depend on the accessor:
#
#      - `console.pen = new_pen` copied new_pen's four slots into the
#        console's existing Pen and discarded new_pen, so an earlier
#        `p = console.pen` stayed live and `new_pen is console.pen` was
#        False. Now it rebinds; `_using_pen` saves/restores the OBJECT, not a
#        tuple of its values (that tuple copy is what made the old behaviour
#        look deliberate -- it wasn't load-bearing, and isn't now).
#      - `console.cursor = some_cursor` fell through `Cursor.__iter__`, so
#        the tuple unpack SUCCEEDED and quietly moved the old cursor to the
#        new one's coordinates -- a silent wrong answer, the worst failure
#        mode of the three. Now it rebinds, and refuses a cursor built over a
#        different glyph layer: a cursor carries the surface it bounds-checks
#        and reads `char` from, so one aimed elsewhere would answer every
#        question about the wrong screen.
# ---------------------------------------------------------------------------

from __future__ import annotations

from contextlib import contextmanager
from typing import cast, Iterator, Optional, Tuple, TYPE_CHECKING, Union

from blitspersecond.colors import Palette
from blitspersecond.graphics.common import Pen
from blitspersecond.graphics.glyph import CharSet, GlyphLayer
from blitspersecond.system.config import Config

from .cursor import Cursor

if TYPE_CHECKING:
    from blitspersecond.display.layers import Layers
    from blitspersecond.input.kbm.input import Input


class Console:
    """A text console: clear / write / print with a per-cell colour pen.
    Spectrum / Amiga flavour, not a VT220.

    Console controls a GlyphLayer, built unattached and off-screen until
    `layers.add` places it -- the same rule as TileLayer/SpriteLayer/
    GlyphLayer built directly:

        console = bps.layers.add(Console(CharSet.ASCII8X8))
        console = bps.layers.add(Console(80, 45))   # cols, rows
        console = bps.layers.add(Console())         # bundled default

    or hand it a GlyphLayer you already made (attached or not):

        console = Console(bps.layers.glyph(CharSet.ASCII8X12))
        console.glyphs.after(background)

    `Console(cols, rows)` is grid-shaped, matching every other Console
    measurement (`.cols`, `.rows`, `cursor`, `write(at=...)`) -- it picks
    whatever pixel cell size tiles the screen into exactly that grid, unlike
    `GlyphLayer`'s own `(w, h)` constructor arg, which is a cell's PIXEL size
    and answers a different question. A grid that doesn't tile the screen
    raises immediately, at construction -- not deferred to `add`.

    The console adds no rendering state of its own."""

    def __init__(
        self,
        glyphs: Optional[Union[GlyphLayer, CharSet, int]] = None,
        rows: Optional[int] = None,
    ) -> None:
        if rows is not None:
            cols = glyphs
            if not isinstance(cols, int) or isinstance(cols, bool):
                raise TypeError(f"Console(cols, rows) takes two integers, got {type(cols)}")
            if not isinstance(rows, int) or isinstance(rows, bool):
                raise TypeError(f"Console(cols, rows) takes two integers, got {type(rows)}")
            screen = Config().display
            if screen.width % cols or screen.height % rows:
                raise ValueError(
                    f"a {cols}x{rows} console does not tile the "
                    f"{screen.width}x{screen.height} screen"
                )
            cell = (screen.width // cols, screen.height // rows)
            charset = CharSet.for_cell(*cell)
            if charset is None:
                grids = ", ".join(
                    f"{screen.width // w}x{screen.height // h} ({c.name})"
                    for c in CharSet
                    for w, h in (c.cell,)
                    if not (screen.width % w or screen.height % h)
                )
                raise ValueError(
                    f"a {cols}x{rows} console needs {cell[0]}x{cell[1]} glyphs "
                    f"and no system charset is that size. Available: {grids}. "
                    f"For a console of blank glyphs to fill yourself, build "
                    f"the layer explicitly: Console(GlyphLayer({cell}))"
                )
            glyphs = GlyphLayer(charset)
        elif glyphs is None or isinstance(glyphs, CharSet):
            glyphs = GlyphLayer(glyphs if glyphs is not None else CharSet.ASCII8X12)
        elif not isinstance(glyphs, GlyphLayer):
            raise TypeError(
                f"expected GlyphLayer, CharSet, or Console(cols, rows); got {type(glyphs)}"
            )
        self._glyphs = glyphs
        self._cursor = Cursor(self._glyphs)

    # -- the composed pieces ------------------------------------------------

    @property
    def glyphs(self) -> GlyphLayer:
        """The glyph layer underneath the console."""
        return self._glyphs

    @property
    def layer(self) -> GlyphLayer:
        """The layer this console controls, attached or not -- read-only,
        there's nothing to rebind it to. Same object as `.glyphs`; this is
        the generic name for advanced work (reordering, tagging via
        `layers.find`, or `layers.add(Console(...))`'s attach hook), where
        `.glyphs` is the GlyphLayer-specific one (`.cell[...]`, `.glyph[...]`)
        -- the name TileMap's own `.layer` will eventually share, so a
        caller working across wrapper types doesn't need to know which one
        it's holding."""
        return self._glyphs

    @property
    def attached(self) -> bool:
        """Whether this console's layer is in a draw order, and so on screen.
        Read-through to `.layer.attached` -- Console carries no draw-order
        state of its own."""
        return self._glyphs.attached

    @property
    def tag(self) -> Optional[str]:
        """The underlying glyph layer's tag -- `console.tag` reaches through
        to `console.glyphs.tag` rather than the console carrying its own,
        since tag is a draw-order/lookup concept and the layer is the thing
        actually sitting in one."""
        return self._glyphs.tag

    @tag.setter
    def tag(self, value: Optional[str]) -> None:
        self._glyphs.tag = value

    # -- geometry -----------------------------------------------------------

    @property
    def cols(self) -> int:
        return self._glyphs.cols

    @property
    def rows(self) -> int:
        return self._glyphs.rows

    @property
    def cursor(self) -> Cursor:
        """The live cursor: its position and the addressed cell's contents."""
        return self._cursor

    @cursor.setter
    def cursor(self, value: Union[Cursor, Tuple[int, int]]) -> None:
        """Move the cursor -- `console.cursor = (x, y)` -- or replace it
        outright with another `Cursor`.

        A replacement must already address this console's layer: a cursor
        carries the surface it measures itself against (its bounds check,
        its `char` reads and writes), so one aimed elsewhere would answer
        every question about the wrong screen."""
        if isinstance(value, Cursor):
            if value.layer is not self._glyphs:
                raise ValueError(
                    "cursor addresses a different glyph layer -- build it "
                    "over this console's layer (Cursor(console.layer))"
                )
            self._cursor = value
            return
        x, y = value
        self._cursor._move(int(x), int(y))

    # -- palette and pen (delegated to the glyph layer) ----------------------

    @property
    def palette(self) -> Palette:
        """The console's live index-to-colour table."""
        return self._glyphs.palette

    @palette.setter
    def palette(self, value: Palette) -> None:
        self._glyphs.palette = value

    @property
    def pen(self) -> Pen:
        """The console's pen -- the four palette indices written into each cell.
        Mutable: `console.pen.foreground = RED`, `console.pen[2] = LIME`. Assign
        a whole `Pen` to replace it."""
        return self._pen()

    @pen.setter
    def pen(self, value: Pen) -> None:
        self._set_pen(value)

    @property
    def foreground(self) -> int:
        """Shortcut for `pen.foreground` (slot 1)."""
        return self._pen().foreground

    @foreground.setter
    def foreground(self, value: int) -> None:
        self._pen().foreground = value

    @property
    def background(self) -> int:
        """Shortcut for `pen.background` (slot 0)."""
        return self._pen().background

    @background.setter
    def background(self, value: int) -> None:
        self._pen().background = value

    def _pen(self) -> Pen:
        """The layer's current pen. Console holds no pen of its own -- `pen`
        is a reach-through, the same rule as `tag`, `layers` and `layer`."""
        return self._glyphs.pen

    # -- writing --------------------------------------------------------------

    def clear(self, pen: Optional[Pen] = None) -> None:
        """Blank the whole console -- every cell zeroed to char 0 -- and home
        the cursor to (0, 0) -- top-left. Pass a `Pen` to paint every cleared
        cell with it and leave it as the current pen for subsequent writes.

        There is no partial-clear mode. Blanking part of the screen is
        writing cells, so it goes through the grid:

            console.layer.cell[:, LYRIC_ROW] = 0     # blank one line

        which is one NumPy write rather than a run of stamped spaces, and
        leaves empty cells rather than glyph 32."""
        if pen is not None:
            self._set_pen(pen)
        self._glyphs.clear()
        self._cursor._move(0, 0)

    def write(
        self,
        text: str,
        *,
        at: Optional[Tuple[int, int]] = None,
    ) -> None:
        r"""Stamp `text` at the cursor in the current pen, advancing across the
        line, wrapping at the right edge and scrolling at the bottom. `\n`
        starts a new line. Pass `at=(x, y)` to move before writing.

        Each cell write adopts the current pen."""
        if at is not None:
            self.cursor = at
        for ch in text:
            if ch == "\n":
                self._newline()
                continue
            self._glyphs.cell[self._cursor.x, self._cursor.y] = ord(ch)
            x = self._cursor.x + 1
            if x >= self.cols:
                self._newline()
            else:
                self._cursor._move(x, self._cursor.y)

    def print(
        self,
        value: object = "",
        *,
        at: Optional[Tuple[int, int]] = None,
    ) -> None:
        """Write an object's string form, then start a new line."""
        self.write(f"{value}\n", at=at)

    def echo(self, entry: "Input") -> "Input":
        """Echo a keyboard-owned Input from the current cursor."""
        from blitspersecond.input.kbm.input import Input
        from .echo import _ConsoleEcho

        if not isinstance(entry, Input):
            raise TypeError(f"expected keyboard Input, got {type(entry)}")
        _ConsoleEcho(self, entry)
        return entry

    def _newline(self) -> None:
        y = self._cursor.y + 1
        if y >= self.rows:
            self._scroll()
            y = self.rows - 1
        self._cursor._move(0, y)

    def _scroll(self) -> None:
        """Shift every row up one and blank the bottom row in the current pen."""
        self._glyphs.scroll((0, -1))

    def _set_pen(self, value: Pen) -> None:
        """Swap the layer's pen. The layer validates type, facing and cell
        size, so Console adds no gate of its own -- that gate is what used to
        keep per-pixel pens out of the console."""
        self._glyphs.pen = value

    @contextmanager
    def _using_pen(self, value: Pen) -> Iterator[None]:
        previous = self._pen()
        self._set_pen(value)
        try:
            yield
        finally:
            self._set_pen(previous)
