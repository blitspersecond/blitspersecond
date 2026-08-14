from typing import Dict, NamedTuple, Tuple

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
    """The immutable bundle a SpriteEngine binds: tilesets + assemblies +
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

    @classmethod
    def load(cls, filename: str) -> "SpriteSheet":
        """The mainline one-liner: package file -> bound-ready sheet.
        Spec parse + validation ride the ResourceManager memo, so loading
        the same package twice costs one parse and two sheets."""
        return cls(ResourceManager().sprite_sheet(filename))

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
