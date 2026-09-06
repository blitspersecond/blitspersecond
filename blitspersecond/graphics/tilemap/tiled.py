"""Reading a world out of a Tiled file.

    island = TiledFile("island.tmx").tilemap("blue_island")
    bps.layers.add(island)

Tiled is the editor and TMX the file it writes. BPS is not a second Tiled: it
accepts the subset that maps exactly onto `TileMap` and refuses the rest by
name, at load, where the file is still the thing you can fix.

**This is a convenience and not the road in.** A world comes from a file OR
from a game writing through `cell`, and both are first-class. Nothing here is
a capability the hand-built road lacks; it is one way of filling in the same
cells, and it is the only caller of `TileMap._from_plane` -- a `.tmx` layer
arrives as a world already made, so building an empty map in order to
overwrite every cell of it would be work for its own sake.

Nothing in this module touches GL. It reads XML, decodes indices and cuts an
`Atlas`, all of which a `PixelBuffer` does without a window.
"""

from __future__ import annotations

import base64
import gzip
import zlib
from dataclasses import dataclass
from os.path import abspath, dirname, join
from typing import Optional, Tuple
from xml.etree import ElementTree

import numpy as np

from blitspersecond.common import CacheableResource, Location, Size

from ..atlas import Atlas
from ..orientation import Orientation, applies_to
from ..pixel_buffer import PixelBuffer
from .internal import EMPTY_TILE, PLANE_DTYPE
from .tilemap import TileMap

# Tiled packs a cell into one uint32: the top bits say how the tile is turned,
# the rest is a global id. The three orientation bits are the same three
# `Orientation` spells, in the same composition order -- see that module, which
# took Tiled's ordering deliberately so this import is a mask and a shift
# rather than a translation.
_TMX_FLIP_H = 0x80000000
_TMX_FLIP_V = 0x40000000
_TMX_TRANSPOSE = 0x20000000
_TMX_HEX_ROTATE = 0x10000000
_TMX_FLAGS = _TMX_FLIP_H | _TMX_FLIP_V | _TMX_TRANSPOSE | _TMX_HEX_ROTATE


@dataclass(frozen=True)
class _Tileset:
    """One `<tileset>` a map refers to -- LOAD-TIME ONLY.

    Private because everything on it except the atlas is bookkeeping this
    module consumes and finishes with: `firstgid` resolves a global id to its
    owning sheet, `name` says which sheet an error is about. Publishing them
    would hand a caller the file format's plumbing after the parser had
    already discharged it.

    What survives the load is the atlas, carried on `TiledLayer`, which the
    parser has already PROVED each layer uses exactly one of.
    """

    name: str
    firstgid: int
    atlas: Atlas

    @property
    def count(self) -> int:
        """How many tiles the sheet holds."""
        return len(self.atlas)


@dataclass(frozen=True)
class TiledLayer:
    """One tile layer: a world, and the sheet its indices mean.

    The pairing is not a convenience -- the parser PROVES it, refusing any
    layer whose tiles span more than one tileset, because one sheet per map is
    what `TileBatch` is built around. Carrying the sheet here is what stops
    every consumer re-deriving a fact settled at parse time.

    This is the DATA road: take one of these when the world you want is not
    literally what the file said -- a repeating strip tiled across the screen,
    a plane stitched from two files. `TiledFile.tilemap()` is the road that
    hands back something drawable.
    """

    name: str
    #: The sheet, already cut. `PixelBuffer` interns by path, so every layer
    #: over one image -- in this file, in another, or built by hand -- holds
    #: one texture.
    tileset: Atlas
    #: The world: `map[x, y]` -> `(tile, flags)`, x-major, `PLANE_DTYPE`.
    #:
    #: READ-ONLY, because a `TiledFile` is shared: it is the file's reading of
    #: the world, and two maps minted from it must not be able to edit each
    #: other. `tilemap()` copies out of this; so should you.
    map: np.ndarray
    #: Where the artist put this layer, from `offsetx`/`offsety`.
    #:
    #: `(0, 0)` is LEFT, TOP -- in that order, the way `bounds` reads and
    #: the way x-then-y reads -- and is what a layer with no offset attributes
    #: gets -- which is every ordinary file. So this is the answer for the
    #: common case rather than a sentinel meaning "unset", and a map only
    #: needs placing when the artist actually moved the layer.
    #:
    #: `tilemap()` applies it as `TileMap.origin`.
    origin: Location

    @property
    def size(self) -> Size:
        """The world in CELLS."""
        return Size(int(self.map.shape[0]), int(self.map.shape[1]))

    @property
    def tile_size(self) -> Size:
        """One cell in PIXELS -- the sheet's."""
        return self.tileset.cell

    def __repr__(self) -> str:
        cols, rows = self.size
        cw, ch = self.tile_size
        return f"<TiledLayer {self.name!r} {cols}x{rows} cells of {cw}x{ch}>"


class TiledFile(CacheableResource):
    """One compatible Tiled map, read and validated -- and the factory the
    `TileMap`s in it come from.

        maps = TiledFile("island.tmx")
        maps.names                      # every tile layer, back to front
        island = maps.tilemap("blue_island")

    **Naming the same file twice gives you the same reading of it**, on the
    same terms as `PixelBuffer`: interned, weakly held, and re-read once the
    last holder lets go.

    **Minting is NOT interned, and that is the point.** Every `tilemap()` call
    builds a fresh `TileMap` with its own writable copy of the world. Asking
    twice gives two maps that can be dug through independently -- eating the
    dirt in a Boulder Dash without destroying the file's world -- and a caller
    who wants one map in two places already has `a = b`.

    An earlier arrangement memoised mints per layer name. Three things broke
    it here. Interning the file made it non-deterministic: drop the `TiledFile`
    while keeping the map, ask again, and the weak table has let go, so the
    same call answers differently. `TileMap.map` is now taken and writable, so
    a shared mint is a shared mutable world rather than merely a shared
    camera. And a `TileMap` IS a `Layer`, so handing the same one back twice
    makes adding it to the draw order twice easy to write. Identity belongs to
    the FILE, which is cached; the world you mint from it is yours.
    """

    _size: Size
    _tile_size: Size
    _layers: Tuple[TiledLayer, ...]
    _filename: str

    @classmethod
    def _cache_key(cls, filename: str) -> str:
        return cls._path_key(filename)

    def _load(self, filename: str) -> None:
        self._filename = abspath(filename)
        _parse(self, self._filename)

    # -- what the file says ----------------------------------------------

    @property
    def size(self) -> Size:
        """The map in CELLS. Every tile layer in a finite TMX is this size."""
        return self._size

    @property
    def tile_size(self) -> Size:
        """One cell in PIXELS, as the map declares it. Every tileset the file
        refers to is checked against this."""
        return self._tile_size

    @property
    def layers(self) -> Tuple[TiledLayer, ...]:
        """Every tile layer, in the artist's back-to-front order."""
        return self._layers

    @property
    def names(self) -> Tuple[str, ...]:
        """Every tile layer's name, back to front -- so iterating this and
        adding each map reproduces the artist's stacking as the draw order."""
        return tuple(layer.name for layer in self._layers)

    def layer(self, name: str) -> TiledLayer:
        """One layer's record: its world, and the sheet that world means."""
        for entry in self._layers:
            if entry.name == name:
                return entry
        raise KeyError(
            f"no tile layer {name!r} in {self._filename!r}; it has: "
            f"{', '.join(self.names)}"
        )

    # -- minting ---------------------------------------------------------

    def tilemap(
        self,
        name: str,
        *,
        collidable: bool = False,
        tag: Optional[str] = None,
    ) -> TileMap:
        """A fresh `TileMap` over one tile layer, unattached.

            island = TiledFile("island.tmx").tilemap("blue_island")
            bps.layers.add(island)

        Unattached is the law every layer obeys: build it, then place it.
        Nothing is drawn until you do -- and nothing is stamped until the
        camera runs, so a map minted here is empty until it is scrolled or
        stamped by hand.

        The world is COPIED out of the file's read-only plane, so this map is
        yours to deform. `tag` defaults to the layer's name, which is what you
        would otherwise write on the next line.

        The layer's Tiled offset becomes `origin`, so a map arrives where the
        artist put it. That is the whole of what the offset meant, and the
        game can move it afterwards like any other.
        """
        entry = self.layer(name)
        tilemap = TileMap._from_plane(
            entry.tileset,
            np.array(entry.map),
            collidable=collidable,
            tag=name if tag is None else tag,
        )
        tilemap.origin = entry.origin
        return tilemap

    def __repr__(self) -> str:
        cols, rows = self._size
        cw, ch = self._tile_size
        return (
            f"<TiledFile {cols}x{rows} cells of {cw}x{ch}, "
            f"{len(self._layers)} layers>"
        )


# -- parsing ---------------------------------------------------------------
#
# Below here the vocabulary is TMX's, because it is reading TMX: `tilewidth`,
# `firstgid`, `parallaxx`. What it parses INTO is the engine's -- `Size`,
# `Atlas`, `Orientation`, `PLANE_DTYPE` -- so the file format's spelling stops
# at the boundary of this section and never reaches a caller.


def _positive_int(element, attribute: str, where: str) -> int:
    try:
        value = int(element.attrib[attribute])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{where}: missing or invalid {attribute!r}") from exc
    if value <= 0:
        raise ValueError(f"{where}: {attribute} must be positive, got {value}")
    return value


def _integer_offset(element, axis: str, where: str) -> int:
    """One axis of a layer offset, in whole pixels.

    Fractional offsets are refused rather than rounded: half a pixel is not a
    place a tile can be put on a fixed 640x360 screen, and rounding it would
    move the artist's layer somewhere they did not ask for.
    """
    raw = element.get(f"offset{axis}", "0")
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{where}: invalid offset{axis} {raw!r}") from exc
    if not value.is_integer():
        raise ValueError(
            f"{where}: fractional offset{axis} {raw!r} is not BPS-compatible; "
            f"a layer sits at whole pixels"
        )
    return int(value)


def _load_tileset(reference, map_dir: str, tile_size: Size) -> _Tileset:
    """One `<tileset>` reference, resolved through its TSX if it has one.

    The image is loaded here rather than at mint time, so a file that names
    art it cannot cut fails while the filename is still in hand.
    """
    where = "tileset"
    firstgid = _positive_int(reference, "firstgid", where)
    source = reference.get("source")
    if source:
        tsx_path = abspath(join(map_dir, source))
        try:
            root = ElementTree.parse(tsx_path).getroot()
        except (OSError, ElementTree.ParseError) as exc:
            raise ValueError(
                f"tileset {source!r}: cannot read TSX: {exc}"
            ) from exc
        if root.tag != "tileset":
            raise ValueError(
                f"tileset {source!r}: root element is not <tileset>"
            )
        tileset_dir = dirname(tsx_path)
        where = f"tileset {source!r}"
    else:
        root = reference
        tileset_dir = map_dir
        where = f"tileset {root.get('name', '')!r}"

    name = root.get("name", "")
    if not name:
        raise ValueError(f"{where}: tileset needs a name")
    tilewidth = _positive_int(root, "tilewidth", where)
    tileheight = _positive_int(root, "tileheight", where)
    if (tilewidth, tileheight) != tuple(tile_size):
        raise ValueError(
            f"{where}: tile size {(tilewidth, tileheight)} does not match the "
            f"map's grid {tuple(tile_size)}"
        )
    if int(root.get("spacing", "0")) or int(root.get("margin", "0")):
        raise ValueError(
            f"{where}: spacing and margin must both be zero. An Atlas cuts a "
            f"sheet into touching cells; nothing in BPS ships one that needs "
            f"gaps, and guessing them would be worse than this refusal."
        )

    images = root.findall("image")
    if len(images) != 1:
        raise ValueError(
            f"{where}: expected one sheet <image>; image collections are not "
            f"BPS-compatible"
        )
    image_source = images[0].get("source")
    if not image_source:
        raise ValueError(f"{where}: tileset image has no source")
    image_path = abspath(join(tileset_dir, image_source))

    # Indexed is NOT required. An RGB or RGBA sheet draws through the direct
    # program; what it cannot do is collide, because solidity is derived from
    # palette index 0 meaning transparent. `TileMap` says so when a collidable
    # map is asked for -- there is nothing to refuse here.
    try:
        atlas = Atlas(PixelBuffer(image_path), tilewidth, tileheight)
    except (OSError, ValueError) as exc:
        raise ValueError(f"{where}: {exc}") from exc

    declared_columns = int(root.get("columns", str(atlas.cols)))
    declared_count = int(root.get("tilecount", str(len(atlas))))
    if declared_columns != atlas.cols or declared_count != len(atlas):
        raise ValueError(
            f"{where}: declares {declared_columns} columns / {declared_count} "
            f"tiles, but {image_source!r} cuts into {atlas.cols} columns / "
            f"{len(atlas)} tiles"
        )
    return _Tileset(name=name, firstgid=firstgid, atlas=atlas)


def _decode_data(data, count: int, where: str) -> np.ndarray:
    """A layer's cells as raw global ids, however the file stored them."""
    encoding = data.get("encoding")
    compression = data.get("compression")
    text = data.text or ""
    if encoding == "csv":
        if compression:
            raise ValueError(f"{where}: CSV data cannot use compression")
        try:
            values = [
                int(part.strip()) for part in text.split(",") if part.strip()
            ]
        except ValueError as exc:
            raise ValueError(f"{where}: invalid integer in CSV data") from exc
        gids = np.asarray(values, dtype=np.uint32)
    elif encoding == "base64":
        try:
            raw = base64.b64decode("".join(text.split()), validate=True)
        except ValueError as exc:
            raise ValueError(f"{where}: invalid base64 data") from exc
        try:
            if compression is None:
                pass
            elif compression == "zlib":
                raw = zlib.decompress(raw)
            elif compression == "gzip":
                raw = gzip.decompress(raw)
            else:
                raise ValueError(
                    f"{where}: compression {compression!r} is not "
                    f"BPS-compatible (use none, zlib or gzip)"
                )
        except (gzip.BadGzipFile, zlib.error) as exc:
            raise ValueError(f"{where}: invalid {compression} data") from exc
        if len(raw) % 4:
            raise ValueError(
                f"{where}: decoded data is not a sequence of uint32 cells"
            )
        gids = np.frombuffer(raw, dtype="<u4").copy()
    else:
        raise ValueError(
            f"{where}: encoding {encoding!r} is not BPS-compatible "
            f"(use CSV or base64)"
        )
    if gids.size != count:
        raise ValueError(f"{where}: expected {count} cells, found {gids.size}")
    return gids


def _owner(gid: int, tilesets: Tuple[_Tileset, ...], where: str) -> _Tileset:
    for tileset in reversed(tilesets):
        if gid >= tileset.firstgid:
            if gid - tileset.firstgid < tileset.count:
                return tileset
            break
    raise ValueError(f"{where}: tile {gid} does not belong to a tileset")


def _load_layer(
    element, size: Size, tilesets: Tuple[_Tileset, ...]
) -> TiledLayer:
    name = element.get("name", "")
    where = f"layer {name!r}"
    if not name:
        raise ValueError("tile layer needs a name")
    cols, rows = size
    if (
        _positive_int(element, "width", where) != cols
        or _positive_int(element, "height", where) != rows
    ):
        raise ValueError(f"{where}: dimensions must match the finite map")
    if (
        float(element.get("parallaxx", "1")) != 1.0
        or float(element.get("parallaxy", "1")) != 1.0
    ):
        raise ValueError(f"{where}: parallax tile layers are not BPS-compatible")
    if element.get("visible", "1") == "0":
        raise ValueError(f"{where}: hidden tile layers are not BPS-compatible")
    if float(element.get("opacity", "1")) != 1.0:
        raise ValueError(f"{where}: tile layer opacity must be 1")
    origin = Location(
        _integer_offset(element, "x", where),
        _integer_offset(element, "y", where),
    )

    data = element.find("data")
    if data is None or data.findall("chunk"):
        raise ValueError(f"{where}: expected finite, unchunked tile data")
    gids = _decode_data(data, cols * rows, where).reshape(rows, cols)
    if np.any(gids & _TMX_HEX_ROTATE):
        raise ValueError(
            f"{where}: hexagonal rotation is not BPS-compatible; this is an "
            f"orthogonal-only reader"
        )

    raw = gids & ~np.uint32(_TMX_FLAGS)
    used = np.unique(raw[raw != 0])
    owners = {_owner(int(gid), tilesets, where).name for gid in used}
    if len(owners) > 1:
        raise ValueError(
            f"{where}: uses {len(owners)} tilesets "
            f"({', '.join(sorted(owners))}). One TileMap draws from one sheet "
            f"-- that is what buys a single texture bind and a single draw "
            f"call for the whole layer, at any tile count. Split it into one "
            f"Tiled layer per sheet; they become one BPS layer each."
        )
    # An empty layer names no tileset of its own, so it takes the map's first.
    # It draws nothing either way, and this is what lets every layer answer
    # the question rather than one of them being a special case.
    owner = next(iter(owners)) if owners else tilesets[0].name
    tileset = next(ts for ts in tilesets if ts.name == owner)

    plane = np.zeros((rows, cols), dtype=PLANE_DTYPE)
    plane["tile"] = EMPTY_TILE
    occupied = raw != 0
    if owners:
        plane["tile"][occupied] = (
            raw[occupied].astype(np.int64) - tileset.firstgid
        )
    plane["flags"] = (
        ((gids & _TMX_TRANSPOSE) != 0).astype(np.uint8)
        | (((gids & _TMX_FLIP_H) != 0).astype(np.uint8) << 1)
        | (((gids & _TMX_FLIP_V) != 0).astype(np.uint8) << 2)
    )

    cell = tileset.atlas.cell
    if not applies_to(Orientation.TRANSPOSE, cell):
        turned = plane["flags"] & int(Orientation.TRANSPOSE)
        if turned.any():
            y, x = (int(i) for i in np.argwhere(turned)[0])
            raise ValueError(
                f"{where}: cell [{x}, {y}] is turned diagonally, and "
                f"{tileset.name!r}'s tiles are {cell.width}x{cell.height}. A "
                f"quarter turn would make them {cell.height}x{cell.width}: it "
                f"would either overflow the neighbouring cells or have to be "
                f"squeezed, and squeezing stops the pixels being square. "
                f"Square tilesets take all eight orientations; rectangular "
                f"ones take the four that leave the shape alone (none, "
                f"horizontal flip, vertical flip, 180). Tiled lets you author "
                f"this, so the map is not broken -- BPS will not draw it."
            )

    # TMX stores rows; the engine addresses worlds [x, y]. Transpose ONCE, at
    # load, so every read downstream is native and nothing pays per frame or
    # wraps an accessor around it.
    world = np.ascontiguousarray(plane.T)
    world.setflags(write=False)
    return TiledLayer(name=name, tileset=tileset.atlas, map=world, origin=origin)


def _parse(target: TiledFile, filename: str) -> None:
    """Read one file into `target`. The whole profile, in reading order."""
    try:
        root = ElementTree.parse(filename).getroot()
    except (OSError, ElementTree.ParseError) as exc:
        raise ValueError(f"TMX {filename!r}: cannot read map: {exc}") from exc
    if root.tag != "map":
        raise ValueError(f"TMX {filename!r}: root element is not <map>")
    if root.get("orientation") != "orthogonal":
        raise ValueError(
            f"TMX {filename!r}: only orthogonal maps are BPS-compatible"
        )
    if root.get("infinite", "0") != "0":
        raise ValueError(
            f"TMX {filename!r}: infinite maps are not BPS-compatible"
        )
    if root.get("renderorder", "right-down") != "right-down":
        raise ValueError(f"TMX {filename!r}: renderorder must be 'right-down'")

    where = f"TMX {filename!r}"
    size = Size(
        _positive_int(root, "width", where),
        _positive_int(root, "height", where),
    )
    tile_size = Size(
        _positive_int(root, "tilewidth", where),
        _positive_int(root, "tileheight", where),
    )

    map_dir = dirname(filename)
    tilesets = tuple(
        _load_tileset(element, map_dir, tile_size)
        for element in root.findall("tileset")
    )
    if not tilesets:
        raise ValueError(f"{where}: expected at least one tileset")
    firstgids = tuple(ts.firstgid for ts in tilesets)
    if firstgids != tuple(sorted(firstgids)):
        raise ValueError(f"{where}: tilesets must be ordered by firstgid")
    if len({ts.name for ts in tilesets}) != len(tilesets):
        raise ValueError(f"{where}: tileset names must be unique")

    unsupported = [
        child.tag
        for child in root
        if child.tag in {"objectgroup", "imagelayer", "group"}
    ]
    if unsupported:
        names = ", ".join(f"<{tag}>" for tag in unsupported)
        raise ValueError(f"{where}: unsupported layer types: {names}")
    elements = root.findall("layer")
    if not elements:
        raise ValueError(f"{where}: expected at least one tile layer")
    layers = tuple(_load_layer(element, size, tilesets) for element in elements)
    if len({layer.name for layer in layers}) != len(layers):
        raise ValueError(f"{where}: tile layer names must be unique")

    target._size = size
    target._tile_size = tile_size
    target._layers = layers
