"""Sprite package loading: the interchange JSON becomes a SpriteSheetSpec.

The package format (see docs/SPRITES.md) is three maps -- tilesets,
assemblies, animations -- and this loader turns a `.sprite.json` file into
the validated, immutable value object the graphics-side sprite machinery
will consume. Loading creates NO playback state: no clocks, no current
frame, no world positions. Everything here is a record.

Validation is the point. The format doc's invariants become loader errors
with names in them (which tileset, which assembly), so a bad export fails
at load time, loudly, rather than as garbage pixels three subsystems later:
every referenced tileset/tile/assembly exists, source rects sit inside
their image, dimensions are positive, part order is non-decreasing,
durations are positive integer ticks, and palette alpha is all-or-nothing
(00 only at the transparent index -- palette-space fade does not exist in
bps, by design).

Images are pulled through ResourceManager.image() -- the memo cache --
so the bounds check here and the PixelBuffer construction later share one
disk hit. The PNG's embedded palette is an authoring preview; the package's
`palettes` tables are authoritative and land here as real Palette instances
(stored as authored, like every palette in the machine).

There is a second, package-free way in: `grid_sprite_sheet_spec()` reads a
plain indexed image as a uniform grid and synthesises the same spec type --
the bare-image path, matching `bps.layers.tile("island.png", 16, 16)`. It
loads nothing and validates nothing beyond the image itself, because there is
no authored file to be wrong.
"""

import json
import re
from os.path import abspath, dirname, join
from typing import Dict, NamedTuple, Optional, Tuple

from blitspersecond.common import Location, Rect

from blitspersecond.colors import Palette

# "#rrggbbaa", lowercase or upper -- the package's colour literal.
_HEX_RGBA = re.compile(r"^#[0-9a-fA-F]{8}$")


class SpritePart(NamedTuple):
    """One drawn part of an assembly: a named rect from a named tileset,
    placed at a signed local offset, in stable draw order. `palette`
    overrides the tileset's default_palette for this part when set."""

    tileset: str
    tile: str
    offset: Location
    order: int
    palette: Optional[int] = None


class SpriteFrame(NamedTuple):
    """One animation step: hold `assembly` for `duration_ticks` simulation
    ticks. Ticks are the only time unit -- milliseconds never enter."""

    assembly: str
    duration_ticks: int


class TilesetSpec(NamedTuple):
    """One indexed image and its reading: authoritative palette tables plus
    named source rects. `image` is the resolved absolute path -- feed it to
    ResourceManager.image() and the loader's validation disk hit is reused."""

    image: str
    transparent_index: int
    default_palette: int
    palettes: Tuple[Palette, ...]
    tiles: Dict[str, Rect]


class AssemblySpec(NamedTuple):
    """One timeless presentable pose: ordered parts plus named collision
    planes, all in one local frame with (0, 0) at the anchor. Empty parts
    is a deliberate blank pose; empty collision means silhouette-only."""

    parts: Tuple[SpritePart, ...]
    collision: Dict[str, Tuple[Rect, ...]]


class AnimationSpec(NamedTuple):
    """One named deterministic sequence of assemblies. Carried data, not a
    player: whoever ticks it (the future Animation module) lives upstream."""

    frames: Tuple[SpriteFrame, ...]


class SpriteSheetSpec(NamedTuple):
    """A whole sprite package, loaded and validated: the bundle a
    SpriteEngine will bind. Shared read-only from the ResourceManager cache,
    like every spec.

    `planes` is the package's collision-plane vocabulary, precalculated at
    load: every plane name any assembly declares. Names are opaque to the
    engine; the set exists so a query selector can be validated loudly
    (a typo'd plane name must be an error, not a hitbox that never hits).

    `grid` is set only by the bare-image path below -- `(cols, rows)` of the
    uniform grid the assemblies were synthesised from, so a caller may address
    them the way TileAtlas is addressed (by index, or by column and row)
    rather than by name. A real package has no grid and is None."""

    tilesets: Dict[str, TilesetSpec]
    assemblies: Dict[str, AssemblySpec]
    animations: Dict[str, AnimationSpec]
    planes: frozenset
    grid: Optional[Tuple[int, int]] = None


def _rect(raw, where: str, signed: bool) -> Rect:
    """A validated [x, y, w, h] -> Rect. Source rects are unsigned (they
    index into an image); local-frame rects (collision) are signed."""
    if not (isinstance(raw, (list, tuple)) and len(raw) == 4):
        raise ValueError(f"{where}: expected [x, y, w, h], got {raw!r}")
    x, y, w, h = (int(v) for v in raw)
    if w <= 0 or h <= 0:
        raise ValueError(f"{where}: non-positive size in {raw!r}")
    if not signed and (x < 0 or y < 0):
        raise ValueError(f"{where}: negative origin in source rect {raw!r}")
    return Rect(x, y, w, h)


def _palette(raw, where: str, transparent_index: int) -> Palette:
    """One package colour table -> a Palette.

    Enforces the all-or-nothing transparency law at load time: alpha is 00
    at the transparent index and ff everywhere else, nothing in between --
    a mid-table transparent entry or a translucent colour is a bad export,
    not a feature. Palette itself re-forces alpha by index, so this check
    exists to make a disagreeing file fail loudly instead of silently
    meaning something else.
    """
    if not isinstance(raw, (list, tuple)) or not raw:
        raise ValueError(f"{where}: expected a non-empty colour list")
    if len(raw) > 256:
        raise ValueError(f"{where}: {len(raw)} entries exceeds 256")
    flat = []
    for i, colour in enumerate(raw):
        if not isinstance(colour, str) or not _HEX_RGBA.match(colour):
            raise ValueError(f"{where}[{i}]: expected '#rrggbbaa', got {colour!r}")
        alpha = int(colour[7:9], 16)
        expected = 0 if i == transparent_index else 255
        if alpha != expected:
            raise ValueError(
                f"{where}[{i}]: alpha {alpha:#04x} -- transparency is "
                f"all-or-nothing (00 at index {transparent_index}, ff elsewhere)"
            )
        flat.extend(int(colour[j : j + 2], 16) for j in (1, 3, 5))
    return Palette(flat)


def _tileset(name: str, raw: dict, base: str) -> TilesetSpec:
    where = f"tileset {name!r}"
    # The memo cache: this validation load IS the load PixelBuffer
    # construction reuses later. Lazy import mirrors ResourceManager's own
    # loader imports (and keeps module import cheap).
    from .resource_manager import ResourceManager

    image_path = abspath(join(base, str(raw["image"])))
    image = ResourceManager().image(image_path)
    if not image.indexed:
        raise ValueError(
            f"{where}: image {raw['image']!r} is mode {image.mode!r} -- "
            "sprite tilesets are indexed (P) surfaces"
        )
    transparent_index = int(raw.get("transparent_index", 0))
    if transparent_index != 0:
        raise ValueError(
            f"{where}: transparent_index {transparent_index}, but bps "
            "requires index 0"
        )
    palettes = tuple(
        _palette(p, f"{where} palette {i}", transparent_index)
        for i, p in enumerate(raw.get("palettes", []))
    )
    if not palettes:
        raise ValueError(f"{where}: no palettes")
    default_palette = int(raw.get("default_palette", 0))
    if not 0 <= default_palette < len(palettes):
        raise ValueError(
            f"{where}: default_palette {default_palette} out of range "
            f"(have {len(palettes)})"
        )
    width, height = image.size
    tiles: Dict[str, Rect] = {}
    for tile_name, tile_raw in raw.get("tiles", {}).items():
        r = _rect(
            tile_raw.get("source"), f"{where} tile {tile_name!r}", signed=False
        )
        if r.x + r.width > width or r.y + r.height > height:
            raise ValueError(
                f"{where} tile {tile_name!r}: source {tuple(r)} exceeds "
                f"image size ({width}, {height})"
            )
        tiles[tile_name] = r
    if not tiles:
        raise ValueError(f"{where}: no tiles")
    return TilesetSpec(
        image=image_path,
        transparent_index=transparent_index,
        default_palette=default_palette,
        palettes=palettes,
        tiles=tiles,
    )


def _assembly(
    name: str, raw: dict, tilesets: Dict[str, TilesetSpec]
) -> AssemblySpec:
    where = f"assembly {name!r}"
    parts = []
    last_order = None
    for i, p in enumerate(raw.get("parts", [])):
        pwhere = f"{where} part {i}"
        tileset_name = p.get("tileset")
        tileset = tilesets.get(tileset_name)
        if tileset is None:
            raise ValueError(f"{pwhere}: unknown tileset {tileset_name!r}")
        tile_name = p.get("tile")
        if tile_name not in tileset.tiles:
            raise ValueError(
                f"{pwhere}: unknown tile {tile_name!r} in tileset "
                f"{tileset_name!r}"
            )
        offset = p.get("offset")
        if not (isinstance(offset, (list, tuple)) and len(offset) == 2):
            raise ValueError(f"{pwhere}: expected offset [x, y], got {offset!r}")
        order = int(p.get("order", 0))
        if last_order is not None and order < last_order:
            raise ValueError(
                f"{pwhere}: order {order} after {last_order} -- parts must "
                "be stored in non-decreasing draw order"
            )
        last_order = order
        palette = p.get("palette")
        if palette is not None:
            palette = int(palette)
            if not 0 <= palette < len(tileset.palettes):
                raise ValueError(
                    f"{pwhere}: palette {palette} out of range "
                    f"(tileset {tileset_name!r} has {len(tileset.palettes)})"
                )
        parts.append(
            SpritePart(
                tileset=tileset_name,
                tile=tile_name,
                offset=Location(int(offset[0]), int(offset[1])),
                order=order,
                palette=palette,
            )
        )
    collision: Dict[str, Tuple[Rect, ...]] = {}
    for plane, rects in (raw.get("collision") or {}).items():
        collision[plane] = tuple(
            _rect(r, f"{where} plane {plane!r} rect {i}", signed=True)
            for i, r in enumerate(rects)
        )
    return AssemblySpec(parts=tuple(parts), collision=collision)


def _animation(
    name: str, raw: dict, assemblies: Dict[str, AssemblySpec]
) -> AnimationSpec:
    where = f"animation {name!r}"
    frames = []
    for i, f in enumerate(raw.get("frames", [])):
        assembly = f.get("assembly")
        if assembly not in assemblies:
            raise ValueError(
                f"{where} frame {i}: unknown assembly {assembly!r}"
            )
        duration = f.get("duration_ticks")
        if not isinstance(duration, int) or duration <= 0:
            raise ValueError(
                f"{where} frame {i}: duration_ticks must be a positive "
                f"integer, got {duration!r}"
            )
        frames.append(SpriteFrame(assembly=assembly, duration_ticks=duration))
    if not frames:
        raise ValueError(f"{where}: no frames")
    return AnimationSpec(frames=tuple(frames))


#: The synthetic tileset every grid sheet carries. Named rather than derived
#: from the filename so `sprite.palettes[...]` reads the same whatever the
#: image is called -- and a grid sheet has exactly one palette anyway.
GRID_TILESET = "sheet"


def grid_sprite_sheet_spec(
    filename: str, w: int, h: int, ox: int = 0, oy: int = 0
) -> SpriteSheetSpec:
    """The bare-image path: one indexed image read as a uniform grid of
    `w` x `h` cells becomes a package, with no package file involved.

    This is `bps.layers.tile("island.png", 16, 16)`'s opposite number. There
    is no authoring tool in the loop and nothing to name things, so the
    synthesis is entirely mechanical: one tileset (`GRID_TILESET`), one
    single-part assembly per cell anchored at its top-left, named by its
    row-major index -- `"0"`, `"1"`, ... -- and no animations and no
    collision planes. What you get is the silhouette plane and nothing else,
    which is exactly the R-Type heritage case docs/SPRITES.md calls the
    default.

    Top-left because a bare grid cell is still a quad, and a quad goes down
    from its top-left everywhere else in the machine. An authored package
    picks a meaningful anchor instead -- Chun-Li's 3198 assemblies put (0, 0)
    at her feet, centred -- and `ox`/`oy` is how a bare sheet says the same
    thing: the format's own `offset` field, same meaning and same sign,
    applied to every cell. `ox=-w // 2, oy=-h // 2` centres the cell on the
    sprite's position, which is what a particle wants (the bullets package
    authors exactly that, by hand, for 8x8 tiles: `[-4, -4]`).

    The one place this inverts the format's law: a package's `palettes`
    tables are authoritative and the PNG's embedded palette is only an
    authoring preview, but here there is no package, so the embedded palette
    IS the table. Indexed is required -- sprite tilesets are R8 index
    textures read through a palette, and an RGB(A) image has no indices to
    read; `bps.layers.tile()` is the path for those.
    """
    from .resource_manager import ResourceManager

    path = abspath(filename)
    image = ResourceManager().image(path)
    if not image.indexed:
        raise ValueError(
            f"{path}: image is mode {image.mode!r} -- a sprite sheet is an "
            "indexed (P) surface; use bps.layers.tile() for RGB(A) pictures"
        )
    w, h = int(w), int(h)
    if w <= 0 or h <= 0:
        raise ValueError(f"{path}: non-positive cell size ({w}, {h})")
    width, height = image.size
    if width % w or height % h:
        raise ValueError(
            f"{path}: image ({width}, {height}) is not a whole number of "
            f"({w}, {h}) cells"
        )
    cols, rows = width // w, height // h
    ox, oy = int(ox), int(oy)
    # The embedded table, copied by SpriteSheet like every other palette --
    # the ImageSpec's own stays pristine in the ResourceManager cache.
    palette = image.palette if image.palette is not None else Palette()
    tiles: Dict[str, Rect] = {}
    assemblies: Dict[str, AssemblySpec] = {}
    for index in range(cols * rows):
        name = str(index)
        col, row = index % cols, index // cols
        tiles[name] = Rect(col * w, row * h, w, h)
        assemblies[name] = AssemblySpec(
            parts=(
                SpritePart(
                    tileset=GRID_TILESET,
                    tile=name,
                    # Anchor at the cell's top-left, shifted by ox/oy: the
                    # same origin a tile blit uses, so a grid sprite lands
                    # where a tile would until you say otherwise.
                    offset=Location(ox, oy),
                    order=0,
                ),
            ),
            collision={},
        )
    return SpriteSheetSpec(
        tilesets={
            GRID_TILESET: TilesetSpec(
                image=path,
                transparent_index=0,
                default_palette=0,
                palettes=(palette,),
                tiles=tiles,
            )
        },
        assemblies=assemblies,
        animations={},
        planes=frozenset(),
        grid=(cols, rows),
    )


def load_sprite_sheet_spec(filename: str) -> SpriteSheetSpec:
    """Load a `.sprite.json` package into a SpriteSheetSpec (the loader
    step; JSON and path resolution live here).

    Image paths inside the package are relative to the package file, and
    come out absolute. Every format invariant is checked here, with the
    offending tileset/assembly/animation named in the error.
    """
    path = abspath(filename)
    with open(path) as f:
        data = json.load(f)
    version = data.get("version")
    if version != 1:
        raise ValueError(f"{path}: unsupported package version {version!r}")
    base = dirname(path)
    raw_tilesets = data.get("tilesets") or {}
    if not raw_tilesets:
        raise ValueError(f"{path}: no tilesets")
    tilesets = {
        name: _tileset(name, raw, base) for name, raw in raw_tilesets.items()
    }
    assemblies = {
        name: _assembly(name, raw, tilesets)
        for name, raw in (data.get("assemblies") or {}).items()
    }
    if not assemblies:
        raise ValueError(f"{path}: no assemblies")
    animations = {
        name: _animation(name, raw, assemblies)
        for name, raw in (data.get("animations") or {}).items()
    }
    planes = frozenset(
        name for a in assemblies.values() for name in a.collision
    )
    return SpriteSheetSpec(
        tilesets=tilesets,
        assemblies=assemblies,
        animations=animations,
        planes=planes,
    )
