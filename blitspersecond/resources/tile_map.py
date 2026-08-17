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
from dataclasses import dataclass
from os.path import abspath, dirname, join
from typing import Tuple
from xml.etree import ElementTree

import numpy as np


_TMX_FLIP_H = 0x80000000
_TMX_FLIP_V = 0x40000000
_TMX_TRANSPOSE = 0x20000000
_TMX_HEX_ROTATE = 0x10000000
_TMX_FLAGS = _TMX_FLIP_H | _TMX_FLIP_V | _TMX_TRANSPOSE | _TMX_HEX_ROTATE

# One ordinary NumPy map plane: -1 is empty, otherwise `tile` is the local,
# zero-based index into the layer's one tileset. `flags` uses TileFlags' 0..7
# bit layout without importing graphics into the resource layer.
TILE_MAP_DTYPE = np.dtype([("tile", np.int32), ("flags", np.uint8)])


@dataclass(frozen=True)
class TileSetSpec:
    """One BPS-compatible grid tileset referenced by a TMX map."""

    name: str
    firstgid: int
    image: str
    tilewidth: int
    tileheight: int
    columns: int
    tilecount: int


@dataclass(frozen=True)
class TileMapSpec:
    """A complete, cached, GL-free reading of one compatible TMX file."""

    width: int
    height: int
    tilewidth: int
    tileheight: int
    tilesets: Tuple[TileSetSpec, ...]
    # Authored back-to-front order. Each plane is a plain structured NumPy
    # array of local tile indices plus orientation; tile -1 is empty.
    layers: Tuple[Tuple[str, np.ndarray], ...]


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
) -> TileSetSpec:
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
    return TileSetSpec(
        name=name,
        firstgid=firstgid,
        image=image_path,
        tilewidth=tilewidth,
        tileheight=tileheight,
        columns=columns,
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


def _owner(gid: int, tilesets: Tuple[TileSetSpec, ...], where: str) -> TileSetSpec:
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
    tilesets: Tuple[TileSetSpec, ...],
) -> Tuple[str, np.ndarray]:
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
    plane = np.zeros((height, width), dtype=TILE_MAP_DTYPE)
    plane["tile"] = -1
    occupied = raw != 0
    if owners:
        tileset_name = next(iter(owners))
        tileset = next(ts for ts in tilesets if ts.name == tileset_name)
        plane["tile"][occupied] = (
            raw[occupied].astype(np.int64) - tileset.firstgid
        )
    plane["flags"] = (
        ((gids & _TMX_TRANSPOSE) != 0).astype(np.uint8)
        | (((gids & _TMX_FLIP_H) != 0).astype(np.uint8) << 1)
        | (((gids & _TMX_FLIP_V) != 0).astype(np.uint8) << 2)
    )
    plane.setflags(write=False)
    return name, plane


def load_tile_map_spec(filename: str) -> TileMapSpec:
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
    if len({name for name, _ in layers}) != len(layers):
        raise ValueError("TMX map: tile layer names must be unique")
    return TileMapSpec(
        width=width,
        height=height,
        tilewidth=tilewidth,
        tileheight=tileheight,
        tilesets=tilesets,
        layers=layers,
    )
