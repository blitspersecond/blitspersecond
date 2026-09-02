"""`SpriteSheet`: the immutable bundle a sprite binds.

Three maps -- tilesets, assemblies, clips -- plus the collision vocabulary
they declare between them. Construction is GL-free and creates NO playback
state: no clock, no current frame, no position. Everything here is a record.
"""

from __future__ import annotations

from typing import Dict, Mapping, NamedTuple, Optional, Tuple, Union

from blitspersecond.common import CacheableResource, Location, Rect

from ..atlas import Atlas
from ..palette import Palette, PaletteBank


class SpriteTileset(NamedTuple):
    """One image a sheet draws from: how it is cut, and how it is coloured.

    The cutting is an `Atlas` and nothing here duplicates it -- a sprite's
    tiles, a tilemap's tiles and a console's glyphs are one thing, so
    `atlas.rect(name)` and `atlas[name]` answer here exactly as they do
    anywhere else.

    What is left over is the colouring, and it is the pillar's own. An
    ordinary indexed image carries ONE palette; a sprite tileset carries a
    bank of them -- Chun-Li has 28 per image, six costume swaps and
    twenty-odd flash and effect tables -- and which one a presentation draws
    through is a register it selects at runtime. `SPRITES.md` calls that a
    logical material view: *"the same R8 index texture read through a
    different `Palette` uniform -- never duplicated pixels."*

    A `PaletteBank` rather than a tuple of them, because the bank is what
    the GPU stores: one 256xN texture, one row per member, so the register
    is a number the shader reads and not a texture the draw binds. It
    indexes and iterates exactly as the tuple did.

    **The bank is authoritative and the image's own table is not.** A package
    is compiled from art whose embedded palette is an authoring preview; the
    tables here are what the artist meant. The grid road is the one exception
    and it inverts this in exactly one place -- see `package.py`.

    Why a record rather than three parallel dicts on the sheet: the palette
    register is keyed by tileset name (`sprite.palettes["character"] = 3`), so
    a caller that has the name wants the cutting and the bank together, and
    keying two dicts the same way is the shape that drifts.
    """

    atlas: Atlas
    palettes: PaletteBank
    default_palette: int


class SpritePart(NamedTuple):
    """One drawn piece of an assembly: a named tile from a named tileset,
    placed at a signed offset in the assembly's local frame, in draw order.

    Which palette it draws under is the presentation's business, not the
    part's. A per-part override existed until 2026-08-20 and was removed
    unused -- measured at 0 of Chun-Li's 3237 parts -- because parts from
    different tilesets already draw under different registers, and two parts
    of one tileset wanting different tables is a sprite of its own, which is
    a layer of its own.
    """

    tileset: str
    tile: str
    offset: Location
    order: int


class Assembly(NamedTuple):
    """One timeless presentable pose: ordered parts plus named collision
    planes, all in one local frame with `(0, 0)` at the anchor.

    Timeless is the point. An assembly owns no clock, no frame counter and no
    position -- it is what a presentation shows, not when or where.

    Empty `parts` is a deliberate blank pose and not a bad export: six of
    Chun-Li's 3198 assemblies have none. Empty `collision` means the
    silhouette is all there is, which is the R-Type heritage case and the
    default the whole engine answers `collides_with` with.
    """

    parts: Tuple[SpritePart, ...]
    collision: Mapping[str, Tuple[Rect, ...]]


class Frame(NamedTuple):
    """One step of a clip: hold `assembly` for `duration_ticks` game ticks.

    **Ticks are the only time unit.** Milliseconds are a preview and export
    concern and never reach the runtime -- a clip advances on the fixed-step
    loop or not at all.
    """

    assembly: str
    duration_ticks: int


class Clip(NamedTuple):
    """One named deterministic sequence of assemblies -- carried, not played.

    Called a clip rather than an animation because the sheet holds the DATA
    and something else does the playing: `Animation` is the tick sequencer
    that walks one of these, and naming both the same thing is how a reader
    ends up unsure which one advances. Reference's own prose already made the
    distinction -- *"the tick sequencer over the sheet's carried clip data"*.

    The on-disk key stays `animations`; the loader is where the two meet.
    """

    frames: Tuple[Frame, ...]


class SpriteSheet(CacheableResource):
    """The immutable bundle a sprite binds: tilesets, assemblies and clips.

        chun    = SpriteSheet("chun-li.sprite.json")     # an authored package
        bullets = SpriteSheet("bullets.png", 8, 8, -4, -4)   # a bare grid

    **Naming the same package twice gives you THE sheet for that file**, the
    same law `PixelBuffer` applies one level down, and for the same reason:
    the thing is scarce. Two sheets over one package is two sets of palette
    tables and two of every `Atlas`, and -- since a palette's GPU row is keyed
    on palette IDENTITY -- two GPU tables where the artist authored one.

    A mirror match is therefore two sprites binding ONE sheet: shared pixels,
    one texture upload, and per-presentation registers selecting different
    tables out of the shared bank. That is what makes red and blue Chun-Li
    cost one sheet, and it is why the bank is shared rather than copied.

    **`copy()` is how you opt out**, and it means what it means everywhere
    else: your own palette tables, for a super-flash or a colour cycle that
    must not reach the other holder. The atlases are shared even then, because
    pixels are shared by `PixelBuffer` regardless and copying them is a
    different request (`buffer.copy()`).

    ## Two roads in

    A package file is the rich road: authored names, multipart composition
    across several tilesets, palette banks, authored collision planes and
    clips. A bare indexed image read as a uniform grid is the other, and it
    synthesises the same records -- one single-part assembly per cell, no
    clips, no named planes. **A package is to a sprite sheet what a TMX is to
    a tileset**: where the structure comes from, not where the pixels come
    from.

    ## What is NOT here

    No playback state, and no separate "spec" type. Reference split this in
    two -- a validated `SpriteSheetSpec` in a `resources` package, and a
    runtime `SpriteSheet` that copied palettes out of it -- because a global
    `ResourceManager` held one shared spec that per-sheet mutation must not
    contaminate. There is no `ResourceManager`; interning by path is the
    sharing, `copy()` is the escape, and the second type had nothing left to
    do. See `SPRITE_PORT.md` F4.
    """

    @classmethod
    def _cache_key(
        cls,
        source: str,
        width: Optional[int] = None,
        height: Optional[int] = None,
        ox: int = 0,
        oy: int = 0,
    ) -> Optional[str]:
        # A package is identified by its path. A grid is identified by the
        # path AND how it was cut: two cell sizes over one image are two
        # different sets of assemblies, and neither is the other's.
        key = cls._path_key(source)
        if width is None:
            return key
        return f"{key}|{int(width)}x{int(height or 0)}+{int(ox)},{int(oy)}"

    def _load(
        self,
        source: str,
        width: Optional[int] = None,
        height: Optional[int] = None,
        ox: int = 0,
        oy: int = 0,
    ) -> None:
        from .package import read_grid, read_package

        if width is None:
            if height is not None or ox or oy:
                raise TypeError(
                    "SpriteSheet(path) reads a package file, which brings its "
                    "own cells and offsets -- pass a cell size only when "
                    "reading a bare image as a grid"
                )
            built = read_package(source)
        else:
            if height is None:
                raise TypeError(
                    "SpriteSheet(image, width, height) needs a cell height -- "
                    "or pass a .sprite.json package path on its own"
                )
            built = read_grid(source, int(width), int(height), int(ox), int(oy))
        (
            self._tilesets,
            self._assemblies,
            self._clips,
            self._planes,
            self._grid,
            self._primary_tileset,
        ) = built

    # -- what it holds -----------------------------------------------------

    @property
    def primary_tileset(self) -> str:
        """The tileset every presentation but slot 0 draws from.

        Named `primary_tileset` rather than `primary` because "the primary"
        means slot 0 throughout this pillar, and this is a different thing --
        a fact about the ART, not about a presentation.

        **Why a sprite has one at all.** Slot 0 may draw from any tileset the
        sheet has, and a multipart pose spans several. Every other slot is
        pinned to this one, which makes the whole instanced block a single
        texture and a single palette -- one bind, no material transitions, and
        the batch cannot be broken by a stray assembly. Chun-Li has 194
        single-part assemblies outside `character` that would otherwise
        qualify: they are the fireball's frames, and a travelling fireball is
        its own sprite with its own collision identity, whose OWN primary
        tileset is `effects`.

        Authored in the package as `primary_tileset`, defaulting to the first
        tileset in the file. A one-tileset package -- every grid sheet, and
        the bullets -- never has to say anything.
        """
        return self._primary_tileset

    @property
    def tilesets(self) -> Mapping[str, SpriteTileset]:
        """Name -> the image it draws from, cut and coloured."""
        return self._tilesets

    @property
    def assemblies(self) -> Mapping[str, Assembly]:
        """Name -> one timeless pose."""
        return self._assemblies

    @property
    def clips(self) -> Mapping[str, Clip]:
        """Name -> one carried animation. Nothing here plays them."""
        return self._clips

    @property
    def planes(self) -> frozenset:
        """Every collision plane name any assembly declares, precalculated.

        The vocabulary a query selector validates against, so a typo'd plane
        name is an error rather than a hitbox that never hits. Empty on a
        sheet that authors none -- the bullets, and every grid sheet -- which
        is a real and exercised case rather than a degenerate one.
        """
        return self._planes

    @property
    def grid(self) -> Optional[Tuple[int, int]]:
        """`(cols, rows)` for a sheet read as a grid, else None.

        Only a grid sheet can be addressed by index or `(col, row)`; a package
        has authored names and asking it for cell 3 is an error rather than a
        coincidence. See `assembly_name`.
        """
        return self._grid

    def copy(self) -> "SpriteSheet":
        """An independent sheet with its own palette tables.

        The only way to own colour that arrived shared: the twin's banks are
        fresh `Palette` objects in a fresh `PaletteBank`, so a super-flash or
        a colour cycle written into them reaches nothing else -- and, because
        `PaletteTexture` keys its GPU table on the bank's identity, the twin
        gets its own table too.

        Pixels are NOT copied. Two sheets over one image sharing one texture
        is the point of `PixelBuffer` interning, and a caller who wants
        destructible art is asking `buffer.copy()`, which is a different
        request one level down.
        """
        twin = object.__new__(type(self))
        twin._tilesets = {
            name: SpriteTileset(
                atlas=tileset.atlas,
                palettes=PaletteBank(Palette(p.raw()) for p in tileset.palettes),
                default_palette=tileset.default_palette,
            )
            for name, tileset in self._tilesets.items()
        }
        twin._assemblies = self._assemblies
        twin._clips = self._clips
        twin._planes = self._planes
        twin._grid = self._grid
        twin._primary_tileset = self._primary_tileset
        return twin

    # -- addressing --------------------------------------------------------

    def assembly_name(self, key: Union[str, int, Tuple[int, int]]) -> str:
        """Resolve an assembly address to its name.

        A string is a name and passes straight through -- the caller's lookup
        reports an unknown one, with the sheet's own vocabulary in the
        message. An int, or a `(col, row)` pair, is a grid address, exactly as
        `atlas[i]` addresses an autogrid, and only a grid sheet has those.
        """
        if isinstance(key, str):
            return key
        if self._grid is None:
            raise TypeError(
                f"assembly {key!r}: this sheet came from a package, so its "
                f"assemblies have authored names -- index addressing exists "
                f"only for a sheet read as a grid"
            )
        cols, rows = self._grid
        if isinstance(key, tuple):
            col, row = int(key[0]), int(key[1])
            if not (0 <= col < cols and 0 <= row < rows):
                raise IndexError(f"cell {(col, row)} outside the {cols}x{rows} grid")
            key = row * cols + col
        index = int(key)
        if not 0 <= index < cols * rows:
            raise IndexError(
                f"assembly {index} outside the {cols}x{rows} grid "
                f"({cols * rows} cells)"
            )
        return str(index)

    def assembly(self, key: Union[str, int, Tuple[int, int]]) -> Assembly:
        """One pose, by name or -- on a grid sheet -- by cell address.

        Raises with the sheet's vocabulary rather than a bare `KeyError`: an
        assembly name arrives from a package, a clip or game code, and "which
        of these did you mean" is the useful half of the answer.
        """
        name = self.assembly_name(key)
        try:
            return self._assemblies[name]
        except KeyError:
            known = sorted(self._assemblies)
            shown = ", ".join(repr(k) for k in known[:8])
            if len(known) > 8:
                shown += f", ... ({len(known)} total)"
            raise KeyError(
                f"no assembly named {name!r} in this sheet -- it holds {shown}"
            ) from None

    def __repr__(self) -> str:
        shape = f"{self._grid[0]}x{self._grid[1]} grid" if self._grid else "package"
        return (
            f"<SpriteSheet {shape}: {len(self._tilesets)} tilesets, "
            f"{len(self._assemblies)} assemblies, {len(self._clips)} clips>"
        )
