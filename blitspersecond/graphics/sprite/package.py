"""Reading a sprite sheet off a disk: the `.sprite.json` package, and the
bare-image grid road that needs no package at all.

One road in among two, and not the privileged one -- the same relationship
`TiledFile` has with `TileMap`. A game generating its own sheet never imports
this and loses nothing.

**Validation is the point.** The format's invariants become errors with names
in them -- which tileset, which assembly, which frame -- so a bad export fails
here, loudly, rather than as garbage pixels three subsystems later. See
`docs/bps/SPRITE_FORMAT.md` for the format itself; this module is the only
thing that enforces it, so the two are meant to be read together.

Nothing here touches GL. It reads JSON, resolves paths and builds `Atlas`
objects over `PixelBuffer`s, all of which work without a window.
"""

from __future__ import annotations

import json
import re
from os.path import abspath, dirname, join
from typing import Dict, FrozenSet, Optional, Tuple

from blitspersecond.common import Location, Rect

from ..atlas import Atlas
from ..palette import Palette, PaletteBank
from ..pixel_buffer import PixelBuffer
from .sheet import Assembly, Clip, Frame, SpritePart, SpriteTileset

#: "#rrggbbaa", either case -- the package's colour literal.
_HEX_RGBA = re.compile(r"^#[0-9a-fA-F]{8}$")

#: The only transparent index BPS has. A package may state it, and must state
#: this; it is not stored afterwards, because a value with one legal setting
#: is not data.
TRANSPARENT_INDEX = 0

#: The synthetic tileset every grid sheet carries. Named rather than derived
#: from the filename so `sprite.palettes[...]` reads the same whatever the
#: image is called -- and a grid sheet has exactly one.
GRID_TILESET = "sheet"

#: What a loader hands back: the six things a SpriteSheet holds.
Built = Tuple[
    Dict[str, SpriteTileset],
    Dict[str, Assembly],
    Dict[str, Clip],
    FrozenSet[str],
    Optional[Tuple[int, int]],
    str,
]


def _rect(raw, where: str, signed: bool) -> Rect:
    """A validated `[x, y, w, h]`.

    Source rects index into an image and are unsigned; local-frame rects
    (collision planes) are signed, because an assembly's `(0, 0)` is the
    anchor and boxes sit around it.
    """
    if not (isinstance(raw, (list, tuple)) and len(raw) == 4):
        raise ValueError(f"{where}: expected [x, y, w, h], got {raw!r}")
    x, y, w, h = (int(v) for v in raw)
    if w <= 0 or h <= 0:
        raise ValueError(f"{where}: non-positive size in {raw!r}")
    if not signed and (x < 0 or y < 0):
        raise ValueError(f"{where}: negative origin in source rect {raw!r}")
    return Rect(x, y, w, h)


def _palette(raw, where: str) -> Palette:
    """One authored colour table.

    Enforces the all-or-nothing transparency law at load: alpha is `00` at
    index 0 and `ff` everywhere else, nothing between. A mid-table transparent
    entry or a translucent colour is a bad export, not a feature -- BPS has no
    palette-space alpha, and fades come from the presentation's colour
    registers instead.

    `Palette` re-derives alpha from the index anyway, so this check exists to
    make a disagreeing file fail loudly rather than quietly mean something
    else.
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
        expected = 0 if i == TRANSPARENT_INDEX else 255
        if alpha != expected:
            raise ValueError(
                f"{where}[{i}]: alpha {alpha:#04x} -- transparency is "
                f"all-or-nothing (00 at index {TRANSPARENT_INDEX}, ff elsewhere)"
            )
        flat.extend(int(colour[j : j + 2], 16) for j in (1, 3, 5))
    return Palette(flat)


def _tileset(name: str, raw: dict, base: str) -> SpriteTileset:
    """One tileset: an image, cut to its authored rects and given its bank."""
    where = f"tileset {name!r}"
    if not isinstance(raw, dict):
        raise ValueError(f"{where}: expected an object, got {type(raw).__name__}")
    image = raw.get("image")
    if not isinstance(image, str):
        raise ValueError(f"{where}: 'image' must be a filename, got {image!r}")
    # Relative to the PACKAGE, not the working directory -- a package and its
    # art travel together. `PixelBuffer` interns by absolute path, so two
    # tilesets naming one image share the buffer and its texture.
    buffer = PixelBuffer(abspath(join(base, image)))
    if not buffer.indexed:
        raise ValueError(
            f"{where}: image {image!r} is mode {buffer.mode!r} -- a sprite "
            f"tileset is an indexed (P) surface, because it is read through a "
            f"palette bank at draw time"
        )

    transparent = int(raw.get("transparent_index", TRANSPARENT_INDEX))
    if transparent != TRANSPARENT_INDEX:
        raise ValueError(
            f"{where}: transparent_index {transparent}, but BPS requires "
            f"{TRANSPARENT_INDEX} -- collision and blending both derive from "
            f"index {TRANSPARENT_INDEX} meaning nothing is there"
        )

    palettes = tuple(
        _palette(p, f"{where} palette {i}")
        for i, p in enumerate(raw.get("palettes") or [])
    )
    if not palettes:
        raise ValueError(
            f"{where}: no palettes. An indexed image is unreadable without "
            f"one, and the package's tables are authoritative -- the image's "
            f"embedded palette is an authoring preview"
        )
    default = int(raw.get("default_palette", 0))
    if not 0 <= default < len(palettes):
        raise ValueError(
            f"{where}: default_palette {default} out of range "
            f"(this tileset has {len(palettes)})"
        )

    tiles = raw.get("tiles") or {}
    if not tiles:
        raise ValueError(f"{where}: no tiles")
    rects = {
        str(tile): _rect(spec.get("source"), f"{where} tile {tile!r}", signed=False)
        for tile, spec in tiles.items()
    }
    try:
        # Atlas re-checks every rect against the image. Wrapped so the message
        # names the tileset too -- it knows the image, not which tileset of
        # which package named it.
        atlas = Atlas(buffer, rects)
    except ValueError as error:
        raise ValueError(f"{where}: {error}") from None
    return SpriteTileset(
        atlas=atlas, palettes=PaletteBank(palettes), default_palette=default
    )


def _assembly(name: str, raw: dict, tilesets: Dict[str, SpriteTileset]) -> Assembly:
    """One pose: ordered parts plus named collision planes."""
    where = f"assembly {name!r}"
    if not isinstance(raw, dict):
        raise ValueError(f"{where}: expected an object, got {type(raw).__name__}")
    parts = []
    last_order = None
    for i, part in enumerate(raw.get("parts") or []):
        pwhere = f"{where} part {i}"
        tileset_name = part.get("tileset")
        tileset = tilesets.get(tileset_name)
        if tileset is None:
            raise ValueError(
                f"{pwhere}: unknown tileset {tileset_name!r} "
                f"(this package has {sorted(tilesets)})"
            )
        tile = part.get("tile")
        if tile not in tileset.atlas:
            raise ValueError(
                f"{pwhere}: unknown tile {tile!r} in tileset {tileset_name!r}"
            )
        offset = part.get("offset")
        if not (isinstance(offset, (list, tuple)) and len(offset) == 2):
            raise ValueError(f"{pwhere}: expected offset [x, y], got {offset!r}")
        order = int(part.get("order", 0))
        if last_order is not None and order < last_order:
            raise ValueError(
                f"{pwhere}: order {order} after {last_order} -- parts are "
                f"stored in non-decreasing draw order, so the file's order IS "
                f"the draw order and nothing has to sort at runtime"
            )
        last_order = order
        if "palette" in part:
            # Refused rather than ignored: silently dropping it would change
            # how such a package draws. Removed 2026-08-20 after measuring 0
            # uses in 3237 parts and failing to find a case for it.
            raise ValueError(
                f"{pwhere}: per-part 'palette' was removed -- selection is per "
                f"presentation, per tileset (sprite.palettes[{tileset_name!r}]), "
                f"and a part wanting its own is a sprite of its own, which is a "
                f"layer of its own"
            )
        parts.append(
            SpritePart(
                tileset=tileset_name,
                tile=tile,
                offset=Location(int(offset[0]), int(offset[1])),
                order=order,
            )
        )
    collision = {
        str(plane): tuple(
            _rect(box, f"{where} plane {plane!r} box {i}", signed=True)
            for i, box in enumerate(boxes)
        )
        for plane, boxes in (raw.get("collision") or {}).items()
    }
    return Assembly(parts=tuple(parts), collision=collision)


def _clip(name: str, raw: dict, assemblies: Dict[str, Assembly]) -> Clip:
    """One carried animation: assemblies held for whole numbers of ticks."""
    where = f"animation {name!r}"
    if not isinstance(raw, dict):
        raise ValueError(f"{where}: expected an object, got {type(raw).__name__}")
    frames = []
    for i, frame in enumerate(raw.get("frames") or []):
        assembly = frame.get("assembly")
        if assembly not in assemblies:
            raise ValueError(f"{where} frame {i}: unknown assembly {assembly!r}")
        duration = frame.get("duration_ticks")
        if not isinstance(duration, int) or isinstance(duration, bool) or duration <= 0:
            raise ValueError(
                f"{where} frame {i}: duration_ticks must be a positive whole "
                f"number of game ticks, got {duration!r}"
            )
        frames.append(Frame(assembly=assembly, duration_ticks=duration))
    if not frames:
        raise ValueError(f"{where}: no frames")
    return Clip(frames=tuple(frames))


def _primary_tileset(raw_tilesets: dict, path: str) -> str:
    """Which tileset every presentation but slot 0 draws from.

    Authored as `"primary": true` ON the tileset rather than as a name at the
    top of the file. The fact belongs to the thing it describes: there is no
    reference to misspell and no pointer to update when a tileset is renamed.
    Not `"default"`, which would sit beside `default_palette` in the same
    object meaning something entirely different.

    Authored at all -- rather than inferred -- because the tempting guess,
    "whatever tileset slot 0 is currently using", moves every tick under a
    clip and spans two tilesets during a multipart pose. It would invalidate a
    population mid-animation. This is a fact about the ART, not about a pose.

    **A package with a choice to make must make it.** One tileset needs no
    flag, because there is nothing to choose. More than one and the flag is
    REQUIRED -- deliberately not "the first one wins", because JSON object
    order is not a contract: a prettifier, a `sort_keys=True`, or any tool
    that round-trips the file may reorder it, and a silently different primary
    tileset would show up as art that stopped drawing. Kris: *"first is a bad
    rule, the json may get resorted."*
    """
    marked = [str(name) for name, raw in raw_tilesets.items() if raw.get("primary")]
    if len(marked) > 1:
        raise ValueError(
            f'{path}: {len(marked)} tilesets are marked "primary": true '
            f"({marked}) -- a sprite's instanced presentations all draw from "
            f"ONE texture, so exactly one tileset may claim it"
        )
    if marked:
        return marked[0]
    if len(raw_tilesets) == 1:
        return str(next(iter(raw_tilesets)))
    raise ValueError(
        f"{path}: this package has {len(raw_tilesets)} tilesets "
        f"({sorted(map(str, raw_tilesets))}) and none is marked "
        f'"primary": true -- every presentation but the sprite\'s own draws '
        f"from ONE of them, so the package must say which. Add "
        f'"primary": true to that tileset.'
    )


def read_package(filename: str) -> Built:
    """A `.sprite.json` package -> the six things a `SpriteSheet` holds.

    Image paths inside the package resolve against the package file, so a
    package and its art travel together.
    """
    path = abspath(filename)
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a JSON object at the top level")
    version = data.get("version")
    if version != 1:
        raise ValueError(
            f"{path}: unsupported package version {version!r} (this engine "
            f"reads version 1)"
        )
    base = dirname(path)

    raw_tilesets = data.get("tilesets") or {}
    if not raw_tilesets:
        raise ValueError(f"{path}: no tilesets")
    tilesets = {
        str(name): _tileset(str(name), raw, base) for name, raw in raw_tilesets.items()
    }

    assemblies = {
        str(name): _assembly(str(name), raw, tilesets)
        for name, raw in (data.get("assemblies") or {}).items()
    }
    if not assemblies:
        raise ValueError(f"{path}: no assemblies")

    clips = {
        str(name): _clip(str(name), raw, assemblies)
        for name, raw in (data.get("animations") or {}).items()
    }
    planes = frozenset(
        plane for assembly in assemblies.values() for plane in assembly.collision
    )
    return (
        tilesets,
        assemblies,
        clips,
        planes,
        None,
        _primary_tileset(raw_tilesets, path),
    )


def read_grid(filename: str, width: int, height: int, ox: int, oy: int) -> Built:
    """A bare indexed image read as a uniform grid, with no package involved.

    `bps` has always let a game skip Tiled entirely for tiles; this is the
    same door on the sprite side. The synthesis is mechanical because there is
    no authoring tool in the loop and nothing to name things: one tileset, one
    single-part assembly per cell named by its row-major index, no clips and
    no named planes. What you get is the silhouette and nothing else, which is
    the R-Type heritage case.

    The anchor is the cell's top-left, because a bare grid cell is still a
    quad and a quad goes down from its top-left everywhere else in the
    machine. `ox`/`oy` shift it -- the format's own `offset`, same meaning and
    same sign -- so `read_grid("bullets.png", 8, 8, -4, -4)` centres, which is
    exactly what the bullets package authors by hand.

    **This inverts the format's palette law in one place, deliberately.** A
    package's tables are authoritative and the image's embedded palette is an
    authoring preview -- but there is no package here, so the embedded palette
    IS the table. Indexed is still required: a sprite tileset is an index
    texture read through a bank, and an RGB(A) picture has no indices;
    `TileMap` is the pillar for those.
    """
    path = abspath(filename)
    buffer = PixelBuffer(path)
    if not buffer.indexed:
        raise ValueError(
            f"{path}: image is mode {buffer.mode!r} -- a sprite sheet is an "
            f"indexed (P) surface; use a TileMap for RGB(A) pictures"
        )
    # The autogrid does the dividing, and raises if the image does not.
    atlas = Atlas(buffer, width, height)
    palette = buffer.palette if buffer.palette is not None else Palette()

    cols = atlas.cols
    assemblies = {
        str(index): Assembly(
            parts=(
                SpritePart(
                    tileset=GRID_TILESET,
                    tile=str(index),
                    offset=Location(ox, oy),
                    order=0,
                ),
            ),
            collision={},
        )
        for index in range(len(atlas))
    }
    # Re-cut by NAME, so a part's `tile` is a string on both roads and nothing
    # downstream has to know which road it came from.
    named = Atlas(buffer, {str(i): atlas.rect(i) for i in range(len(atlas))})
    tilesets = {
        GRID_TILESET: SpriteTileset(
            atlas=named, palettes=PaletteBank((palette,)), default_palette=0
        )
    }
    # A grid sheet has exactly one tileset, so there is nothing to choose.
    return tilesets, assemblies, {}, frozenset(), (cols, atlas.rows), GRID_TILESET
