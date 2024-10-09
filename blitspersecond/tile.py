from numpy import ndarray, ones, zeros, uint8, bool_
from typing import Tuple
from .logger import Logger


class Tile(object):
    _tile: ndarray
    _mask: ndarray
    _rgba: ndarray

    def __init__(self, tile_data: ndarray) -> None:
        self._tile = tile_data.copy()
        self._mask = None
        self._rgba = None

    def invalidate(self) -> None:
        self._mask = None
        self._rgba = None

    @property
    def image(self) -> ndarray:
        return self._tile

    @property
    def mask(self) -> ndarray:
        return self._mask

    @property
    def size(self) -> Tuple[int, int]:
        return (self._tile.shape[1], self._tile.shape[0])

    @mask.setter
    def mask(self, mask_data: ndarray) -> None:
        if mask_data.shape != self._tile.shape:
            Logger().error("Mask shape does not match tile shape.")
            raise ValueError("Mask must be the same shape as the tile.")
        if mask_data.dtype != bool_:
            Logger().error("Mask data type is not bool.")
            raise ValueError("Mask must be of type bool.")
        self._mask = mask_data.copy()

    @property
    def rgba(self) -> ndarray:
        return self._rgba
