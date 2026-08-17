"""Image-backed, GL-free reading of a BPS-compatible TMX map.

TileMap is deliberately not a Layer and creates no Layers. Its map planes are
plain structured NumPy arrays containing local tile indices and orientation.
A game can use those arrays for pathfinding, editing, collision preparation or
rendering without depending on TileEngine storage or TMX global IDs.
"""

from __future__ import annotations

from typing import Dict, Iterator, Tuple

import numpy as np

from blitspersecond.graphics.common import PixelBuffer
from blitspersecond.resources import ResourceManager, TileMapSpec

from .tile_atlas import TileAtlas


class TileMap:
    """Loaded TileAtlases and ordinary map planes from one compatible TMX."""

    def __init__(self, spec: TileMapSpec) -> None:
        if not isinstance(spec, TileMapSpec):
            raise TypeError(f"expected TileMapSpec, got {type(spec)}")
        manager = ResourceManager()
        self._width = spec.width
        self._height = spec.height
        self._tile_size = (spec.tilewidth, spec.tileheight)
        self._tilesets: Dict[str, TileAtlas] = {}
        for tileset in spec.tilesets:
            self._tilesets[tileset.name] = TileAtlas(
                PixelBuffer(manager.image(tileset.image)),
                tile_size=(tileset.tilewidth, tileset.tileheight),
            )
        # dict preserves the authored back-to-front order validated by the
        # spec loader. Like PixelBuffer copying a cached ImageSpec, each map
        # owns mutable planes while the ResourceManager's spec stays pristine.
        self._layers: Dict[str, np.ndarray] = {
            name: np.array(gids) for name, gids in spec.layers
        }

    @classmethod
    def load(cls, filename: str) -> "TileMap":
        """Load a compatible TMX file through the shared resource cache."""
        return cls(ResourceManager().tile_map(filename))

    @property
    def width(self) -> int:
        """Map width in cells."""
        return self._width

    @property
    def height(self) -> int:
        """Map height in cells."""
        return self._height

    @property
    def size(self) -> Tuple[int, int]:
        """Map size in cells, `(width, height)`."""
        return self._width, self._height

    @property
    def pixel_size(self) -> Tuple[int, int]:
        """Map size in pixels."""
        tw, th = self._tile_size
        return self._width * tw, self._height * th

    @property
    def tile_size(self) -> Tuple[int, int]:
        return self._tile_size

    @property
    def tilesets(self) -> Dict[str, TileAtlas]:
        return self._tilesets

    @property
    def layers(self) -> Dict[str, np.ndarray]:
        """Named tile/flags arrays in authored back-to-front order."""
        return self._layers

    def __getitem__(self, name: str) -> np.ndarray:
        return self._layers[name]

    def __iter__(self) -> Iterator[np.ndarray]:
        return iter(self._layers.values())

    def __len__(self) -> int:
        return len(self._layers)
