"""The deliberately small TMX profile understood by Blits Per Second.

Tiled is the editor; TMX is the interchange file.  BPS does not attempt to
be a second Tiled renderer.  It accepts the subset which maps exactly onto
TileEngine's model and rejects the rest at load time with a useful name.

The loader is GL-free. It resolves external TSX files and validates their
indexed PNGs through ResourceManager. TMX global IDs are file-format plumbing;
map planes expose local tile indices and compact orientation flags instead.
"""

from __future__ import annotations

import base64
import gzip
import zlib
from dataclasses import dataclass, field
from os.path import abspath, dirname, join
from typing import TYPE_CHECKING, Dict, Tuple
from xml.etree import ElementTree

import numpy as np

if TYPE_CHECKING:
    from blitspersecond.graphics.tile import TileMap


_TMX_FLIP_H = 0x80000000
_TMX_FLIP_V = 0x40000000
_TMX_TRANSPOSE = 0x20000000
_TMX_HEX_ROTATE = 0x10000000
_TMX_FLAGS = _TMX_FLIP_H | _TMX_FLIP_V | _TMX_TRANSPOSE | _TMX_HEX_ROTATE

# One ordinary NumPy map plane: -1 is empty, otherwise `tile` is the local,
# zero-based index into the layer's one tileset. `flags` uses TileFlags' 0..7
# bit layout without importing graphics into the resource layer.
PLANE_DTYPE = np.dtype([("tile", np.int32), ("flags", np.uint8)])


@dataclass(frozen=True)
class _Tileset:
    """One grid tileset referenced by a TMX map -- LOAD-TIME ONLY.

    Private on purpose. Everything here except `image`/`w`/`h` is TMX plumbing
    that this module consumes and finishes with: `firstgid` and `tilecount`
    resolve a global ID to an owning tileset, `cols` validates the declared
    grid against the real one. Publishing them handed callers the file
    format's bookkeeping after the parser had already discharged it.

    What survives the load is carried on TiledLayer, which the loader has
    already PROVED uses exactly one tileset.
    """

    name: str
    firstgid: int
    image: str
    # One tile in PIXELS, and the sheet's grid in cells. BPS vocabulary --
    # the loader below reads TMX's own attribute names into locals, but what
    # it parses INTO is the engine's.
    w: int
    h: int
    cols: int
    tilecount: int


@dataclass(frozen=True)
class TiledLayer:
    """One tile layer: its grid, and the sheet that grid's indices mean.

    The pairing is not a convenience -- the loader PROVES it, rejecting any
    layer whose tiles span more than one tileset, because one texture per
    layer is what the batch depends on. Carrying the sheet here is what stops
    every consumer re-deriving a fact already established at parse time.

    `image`/`w`/`h` are the whole of what a consumer needs from the tileset:
    which sheet, at what cell size. They live here rather than on a separate
    published record because the tileset a layer uses is a property OF the
    layer once the file is parsed.
    """

    name: str
    #: The tileset's name in Tiled -- identity and error messages only.
    tileset: str
    #: Absolute path to the sheet, and one cell in PIXELS.
    image: str
    w: int
    h: int
    # Structured PLANE_DTYPE, indexed [x, y] -- X-MAJOR, screen order, the
    # same way GlyphLayer stores its cell grid. See TileMap.tiles.
    tiles: np.ndarray


@dataclass(frozen=True)
class TiledFile:
    """A complete, cached, GL-free reading of one compatible TMX file -- and
    the factory the TileMaps in it come from.

        maps = ResourceManager().tmx("island.tmx")
        island = maps.tilemap("blue_island")
        bps.layers.add(island)

    A TMX is an ASSET: immutable, shared, one per path in the ResourceManager
    memo. A TileMap is a GAME OBJECT: it has a camera and a stamped window.
    This type is the seam between them -- cached itself, minting fresh state
    on request -- which is the same split as ImageSpec (cached, pristine) to
    PixelBuffer (owned, mutable).

    Minted TileMaps are memoised per layer name, so asking twice gives the
    same map rather than two cameras over one world. Take `.copy()` on the
    result when you want an independent one.
    """

    # The map in cells, then one tile in pixels.
    cols: int
    rows: int
    w: int
    h: int
    # Authored back-to-front order.
    layers: Tuple[TiledLayer, ...]
    # Minted TileMaps, by layer name. Mutable inside a frozen dataclass: the
    # dict is rebound never, mutated in place always. Excluded from eq/repr so
    # the record still compares as the pure data it is.
    _minted: Dict[str, "TileMap"] = field(
        default_factory=dict, compare=False, repr=False
    )

    @property
    def names(self) -> Tuple[str, ...]:
        """Every tile layer's name, authored back to front -- so iterating
        this and adding each map reproduces the artist's stacking as the
        draw order."""
        return tuple(layer.name for layer in self.layers)

    def layer(self, name: str) -> TiledLayer:
        """One layer's raw record: its grid, and the sheet it means.

        The data road. `tilemap()` is the one that mints something drawable;
        this is for a caller building its own -- a repeating strip tiled
        across the screen, a plane the file did not literally contain.
        """
        for entry in self.layers:
            if entry.name == name:
                return entry
        raise KeyError(
            f"no tile layer {name!r} in this map; it has: "
            f"{', '.join(self.names)}"
        )

    def tilemap(self, name: str) -> "TileMap":
        """The TileMap for one tile layer, minted unattached and memoised.

        Unattached is the law every graphics type here obeys: build it, then
        `layers.add(...)` places it. Nothing is drawn until you do.

        Layers sharing a sheet share one TileAtlas -- and therefore one
        texture and one interned tile pool -- because the sheet is an asset
        and identity IS sharing for a PixelBuffer.
        """
        minted = self._minted.get(name)
        if minted is not None:
            return minted
        # Call-time import: graphics imports resources for ImageSpec/TiledFile,
        # so a module-level import here closes the loop. Same deferral, and the
        # same reason, as display/layers.py's mint methods.
        from blitspersecond.graphics.tile import TileMap

        entry = self.layer(name)
        minted = TileMap(_sheet(entry), np.array(entry.tiles), self)
        self._minted[name] = minted
        return minted


def _positive_int(element, attribute: str, where: str) -> int:
    try:
        value = int(element.attrib[attribute])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{where}: missing or invalid {attribute!r}") from exc
    if value <= 0:
        raise ValueError(f"{where}: {attribute} must be positive, got {value}")
    return value


def _integer_offset(element, axis: str, where: str) -> int:
    raw = element.get(f"offset{axis}", "0")
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{where}: invalid offset{axis} {raw!r}") from exc
    if not value.is_integer():
        raise ValueError(
            f"{where}: fractional offset{axis} {raw!r} is not BPS-compatible"
        )
    return int(value)


def _load_tileset(
    reference,
    map_dir: str,
    map_tile_size: Tuple[int, int],
) -> _Tileset:
    where = "tileset"
    firstgid = _positive_int(reference, "firstgid", where)
    source = reference.get("source")
    if source:
        tsx_path = abspath(join(map_dir, source))
        try:
            root = ElementTree.parse(tsx_path).getroot()
        except (OSError, ElementTree.ParseError) as exc:
            raise ValueError(f"tileset {source!r}: cannot read TSX: {exc}") from exc
        if root.tag != "tileset":
            raise ValueError(f"tileset {source!r}: root element is not <tileset>")
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
    if (tilewidth, tileheight) != map_tile_size:
        raise ValueError(
            f"{where}: tile size {(tilewidth, tileheight)} does not match "
            f"map grid {map_tile_size}"
        )
    spacing = int(root.get("spacing", "0"))
    margin = int(root.get("margin", "0"))
    if spacing or margin:
        raise ValueError(
            f"{where}: spacing and margin must both be zero for TileAtlas"
        )

    images = root.findall("image")
    if len(images) != 1:
        raise ValueError(
            f"{where}: expected one sheet <image>; image collections are not "
            "BPS-compatible"
        )
    image_source = images[0].get("source")
    if not image_source:
        raise ValueError(f"{where}: tileset image has no source")
    image_path = abspath(join(tileset_dir, image_source))

    # Use the ordinary image memo here: validation and the eventual
    # PixelBuffer construction then share one decode.
    from .resource_manager import ResourceManager

    image = ResourceManager().image(image_path)
    if not image.indexed:
        raise ValueError(
            f"{where}: image {image_source!r} is mode {image.mode!r}; BPS tile "
            "maps require an indexed PNG"
        )
    iw, ih = image.size
    if iw % tilewidth or ih % tileheight:
        raise ValueError(
            f"{where}: image size {(iw, ih)} is not an exact "
            f"{(tilewidth, tileheight)} grid"
        )
    columns = iw // tilewidth
    tilecount = columns * (ih // tileheight)
    declared_columns = int(root.get("columns", str(columns)))
    declared_count = int(root.get("tilecount", str(tilecount)))
    if declared_columns != columns or declared_count != tilecount:
        raise ValueError(
            f"{where}: declared grid {declared_columns} columns / "
            f"{declared_count} tiles does not match image grid "
            f"{columns} columns / {tilecount} tiles"
        )
    return _Tileset(
        name=name,
        firstgid=firstgid,
        image=image_path,
        w=tilewidth,
        h=tileheight,
        cols=columns,
        tilecount=tilecount,
    )


def _decode_data(data, count: int, where: str) -> np.ndarray:
    encoding = data.get("encoding")
    compression = data.get("compression")
    text = data.text or ""
    if encoding == "csv":
        if compression:
            raise ValueError(f"{where}: CSV data cannot use compression")
        try:
            values = [int(part.strip()) for part in text.split(",") if part.strip()]
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
                    "BPS-compatible (use none, zlib or gzip)"
                )
        except (gzip.BadGzipFile, zlib.error) as exc:
            raise ValueError(f"{where}: invalid {compression} data") from exc
        if len(raw) % 4:
            raise ValueError(f"{where}: decoded data is not a sequence of uint32 GIDs")
        gids = np.frombuffer(raw, dtype="<u4").copy()
    else:
        raise ValueError(
            f"{where}: encoding {encoding!r} is not BPS-compatible "
            "(use CSV or base64)"
        )
    if gids.size != count:
        raise ValueError(f"{where}: expected {count} cells, found {gids.size}")
    return gids


def _owner(gid: int, tilesets: Tuple[_Tileset, ...], where: str) -> _Tileset:
    for tileset in reversed(tilesets):
        if gid >= tileset.firstgid:
            local = gid - tileset.firstgid
            if local < tileset.tilecount:
                return tileset
            break
    raise ValueError(f"{where}: GID {gid} does not belong to a tileset")


def _load_layer(
    element,
    width: int,
    height: int,
    tilesets: Tuple[_Tileset, ...],
) -> TiledLayer:
    name = element.get("name", "")
    where = f"layer {name!r}"
    if not name:
        raise ValueError("tile layer needs a name")
    if _positive_int(element, "width", where) != width or _positive_int(
        element, "height", where
    ) != height:
        raise ValueError(f"{where}: dimensions must match the finite map")
    if float(element.get("parallaxx", "1")) != 1.0 or float(
        element.get("parallaxy", "1")
    ) != 1.0:
        raise ValueError(f"{where}: parallax tile layers are not BPS-compatible")
    offset = (
        _integer_offset(element, "x", where),
        _integer_offset(element, "y", where),
    )
    if offset != (0, 0):
        raise ValueError(f"{where}: tile layer offsets are not BPS-compatible")
    if element.get("visible", "1") == "0":
        raise ValueError(f"{where}: hidden tile layers are not BPS-compatible")
    if float(element.get("opacity", "1")) != 1.0:
        raise ValueError(f"{where}: tile layer opacity must be 1")
    data = element.find("data")
    if data is None or data.findall("chunk"):
        raise ValueError(f"{where}: expected finite, unchunked tile data")
    gids = _decode_data(data, width * height, where).reshape(height, width)
    if np.any(gids & _TMX_HEX_ROTATE):
        raise ValueError(f"{where}: hexagonal rotation flags are not compatible")

    raw = gids & ~np.uint32(_TMX_FLAGS)
    used = np.unique(raw[raw != 0])
    owners = {_owner(int(gid), tilesets, where).name for gid in used}
    if len(owners) > 1:
        names = ", ".join(sorted(owners))
        raise ValueError(
            f"{where}: uses multiple tilesets ({names}); BPS-compatible TMX "
            "uses one tileset per tile layer"
        )
    plane = np.zeros((height, width), dtype=PLANE_DTYPE)
    plane["tile"] = -1
    occupied = raw != 0
    # An empty layer names no tileset of its own; it takes the map's first so
    # that every layer answers the question, drawing nothing either way.
    tileset_name = next(iter(owners)) if owners else tilesets[0].name
    tileset = next(ts for ts in tilesets if ts.name == tileset_name)
    if owners:
        plane["tile"][occupied] = (
            raw[occupied].astype(np.int64) - tileset.firstgid
        )
    plane["flags"] = (
        ((gids & _TMX_TRANSPOSE) != 0).astype(np.uint8)
        | (((gids & _TMX_FLIP_H) != 0).astype(np.uint8) << 1)
        | (((gids & _TMX_FLIP_V) != 0).astype(np.uint8) << 2)
    )
    # TMX decodes row-major; the engine addresses grids [x, y]. Transpose ONCE
    # here, at load, so every downstream read is native and no per-access
    # wrapper or per-frame transpose is needed. This mirrors GlyphLayer, whose
    # cell grid is likewise stored x-major precisely so `cell[x, y]` is free.
    tiles = np.ascontiguousarray(plane.T)
    tiles.setflags(write=False)
    return TiledLayer(
        name=name,
        tileset=tileset_name,
        image=tileset.image,
        w=tileset.w,
        h=tileset.h,
        tiles=tiles,
    )


def _sheet(entry: TiledLayer):
    """The shared TileAtlas for one layer's tileset.

    The PixelBuffer comes from the ResourceManager memo, so every layer over
    the same sheet -- in this map, in another map, or placed by hand through
    `layers.tile()` -- gets the SAME buffer and therefore one texture. The
    atlas on top is cheap arithmetic and not worth caching: two grids over one
    buffer is a legitimate thing to want.
    """
    from blitspersecond.graphics.tile import TileAtlas

    from .resource_manager import ResourceManager

    return TileAtlas(ResourceManager().buffer(entry.image), entry.w, entry.h)


def load_tiled_file(filename: str) -> TiledFile:
    """Load and validate one BPS-compatible finite orthogonal TMX map."""

    filename = abspath(filename)
    try:
        root = ElementTree.parse(filename).getroot()
    except (OSError, ElementTree.ParseError) as exc:
        raise ValueError(f"TMX {filename!r}: cannot read map: {exc}") from exc
    if root.tag != "map":
        raise ValueError(f"TMX {filename!r}: root element is not <map>")
    if root.get("orientation") != "orthogonal":
        raise ValueError("TMX map: only orthogonal maps are BPS-compatible")
    if root.get("infinite", "0") != "0":
        raise ValueError("TMX map: infinite maps are not BPS-compatible")
    if root.get("renderorder", "right-down") != "right-down":
        raise ValueError("TMX map: renderorder must be 'right-down'")
    width = _positive_int(root, "width", "TMX map")
    height = _positive_int(root, "height", "TMX map")
    tilewidth = _positive_int(root, "tilewidth", "TMX map")
    tileheight = _positive_int(root, "tileheight", "TMX map")

    map_dir = dirname(filename)
    tilesets = tuple(
        _load_tileset(element, map_dir, (tilewidth, tileheight))
        for element in root.findall("tileset")
    )
    if not tilesets:
        raise ValueError("TMX map: expected at least one tileset")
    if tuple(sorted(ts.firstgid for ts in tilesets)) != tuple(
        ts.firstgid for ts in tilesets
    ):
        raise ValueError("TMX map: tilesets must be ordered by firstgid")
    if len({ts.name for ts in tilesets}) != len(tilesets):
        raise ValueError("TMX map: tileset names must be unique")

    unsupported = [
        child.tag
        for child in root
        if child.tag in {"objectgroup", "imagelayer", "group"}
    ]
    if unsupported:
        names = ", ".join(f"<{tag}>" for tag in unsupported)
        raise ValueError(f"TMX map: unsupported layer types: {names}")
    layer_elements = root.findall("layer")
    if not layer_elements:
        raise ValueError("TMX map: expected at least one tile layer")
    layers = tuple(
        _load_layer(element, width, height, tilesets)
        for element in layer_elements
    )
    if len({layer.name for layer in layers}) != len(layers):
        raise ValueError("TMX map: tile layer names must be unique")
    return TiledFile(
        cols=width,
        rows=height,
        w=tilewidth,
        h=tileheight,
        layers=layers,
    )
