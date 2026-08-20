from typing import Dict, NamedTuple, Optional, Tuple, Union

from blitspersecond.common import Rect
from blitspersecond.graphics.common import PixelBuffer
from blitspersecond.resources import (
    Palette,
    ResourceManager,
    SpriteSheetSpec,
)
from blitspersecond.resources.sprite_sheet import AnimationSpec, AssemblySpec


class SpriteTileset(NamedTuple):
    """One tileset, runtime form: the PixelBuffer its parts sample, the
    sheet-owned palette tables, and the name -> source-rect registry.

    Deliberately not a grid -- TileAtlas remains the happy path for uniform
    grids; this is arbitrary named rects over one indexed image. The
    palettes here are the authoritative tables from the package (the PNG's
    embedded palette is an authoring preview), and they are THIS SHEET'S
    mutable copies: super-flash, damage-flash and colour-cycling write them
    via Palette.__setitem__ without contaminating the shared spec.
    """

    buffer: PixelBuffer
    palettes: Tuple[Palette, ...]
    default_palette: int
    transparent_index: int
    tiles: Dict[str, Rect]


class SpriteSheet:
    """The immutable bundle a SpriteLayer binds: tilesets + assemblies +
    animation data, built from a validated SpriteSheetSpec.

    Construction is GL-free and creates NO playback state -- no clock, no
    current frame, no positions. What it does build: one PixelBuffer per
    tileset (texture uploads lazily at first draw; sharing this sheet across
    engines shares those textures -- the mirror match is two layers binding
    ONE sheet) and per-sheet mutable copies of the palette tables (the spec
    stays pristine in the ResourceManager cache, same law as PixelBuffer
    copying ImageSpec's pixels).

    Assemblies and animations pass through as the spec's immutable records:
    the runtime reading is the same reading. Animation data is carried, not
    played -- a future Animation instance owns playback; the SpriteEngine
    never reads it.
    """

    def __init__(self, spec: SpriteSheetSpec) -> None:
        if not isinstance(spec, SpriteSheetSpec):
            raise TypeError(f"expected SpriteSheetSpec, got {type(spec)}")
        manager = ResourceManager()
        self._tilesets: Dict[str, SpriteTileset] = {}
        for name, ts in spec.tilesets.items():
            self._tilesets[name] = SpriteTileset(
                # The loader validated through the same memo cache, so this
                # is a cache hit, not a second disk read.
                buffer=PixelBuffer(manager.image(ts.image)),
                # raw() -> fresh Palette: this sheet's own mutable registers.
                palettes=tuple(Palette(p.raw()) for p in ts.palettes),
                default_palette=ts.default_palette,
                transparent_index=ts.transparent_index,
                tiles=ts.tiles,
            )
        self._assemblies = spec.assemblies
        self._animations = spec.animations
        self._planes = spec.planes
        self._grid = spec.grid

    @classmethod
    def load(cls, filename: str) -> "SpriteSheet":
        """The mainline one-liner: package file -> bound-ready sheet.
        Spec parse + validation ride the ResourceManager memo, so loading
        the same package twice costs one parse and two sheets."""
        return cls(ResourceManager().sprite_sheet(filename))

    @classmethod
    def grid(
        cls, filename: str, w: int, h: int, ox: int = 0, oy: int = 0
    ) -> "SpriteSheet":
        """The bare-image path: an indexed image read as a uniform grid of
        `w` x `h` cells, no package file involved.

            band = SpriteSheet.grid("band.png", 12, 12)
            two_d = layer.sprite(0)          # or layer.sprite((0, 0))

            bullets = SpriteSheet.grid("bullets.png", 8, 8, -4, -4)

        One single-part assembly per cell, anchored at the cell's top-left
        and named by its row-major index, no animations and no named
        collision planes -- the silhouette is all there is, which is the
        R-Type case. `ox`/`oy` shift that anchor (the format's `offset`, same
        sign) for sheets that want centring or a floor anchor. See
        resources.grid_sprite_sheet_spec for what is and isn't synthesised."""
        from blitspersecond.resources.sprite_sheet import grid_sprite_sheet_spec

        return cls(grid_sprite_sheet_spec(filename, w, h, ox, oy))

    @property
    def tilesets(self) -> Dict[str, SpriteTileset]:
        return self._tilesets

    @property
    def assemblies(self) -> Dict[str, AssemblySpec]:
        return self._assemblies

    @property
    def animations(self) -> Dict[str, AnimationSpec]:
        return self._animations

    @property
    def planes(self) -> frozenset:
        """The package's collision-plane vocabulary (precalculated at load).
        Query selectors validate against this so unknown names fail loudly."""
        return self._planes

    @property
    def grid_size(self) -> Optional[Tuple[int, int]]:
        """`(cols, rows)` for a sheet built by `grid()`, else None. Only a
        grid sheet can be addressed by index or by column and row."""
        return self._grid

    def assembly_name(self, key: Union[str, int, Tuple[int, int]]) -> str:
        """Resolve an assembly address to its name.

        A string is a name and passes straight through (unchecked here -- the
        caller's lookup reports an unknown one). An int, or a `(col, row)`
        pair, is a grid address, the way `atlas[i]` and `atlas[col, row]`
        address a TileAtlas -- and only a grid sheet has those, so asking a
        package sheet for one is an error rather than a coincidence."""
        if isinstance(key, str):
            return key
        if self._grid is None:
            raise TypeError(
                f"assembly {key!r}: this sheet came from a package, so its "
                "assemblies have authored names -- index addressing exists "
                "only for sheets built by SpriteSheet.grid()"
            )
        cols, rows = self._grid
        if isinstance(key, tuple):
            col, row = int(key[0]), int(key[1])
            if not (0 <= col < cols and 0 <= row < rows):
                raise IndexError(
                    f"cell {(col, row)} outside the {cols}x{rows} grid"
                )
            key = row * cols + col
        index = int(key)
        if not 0 <= index < cols * rows:
            raise IndexError(
                f"assembly {index} outside the {cols}x{rows} grid "
                f"({cols * rows} cells)"
            )
        return str(index)
