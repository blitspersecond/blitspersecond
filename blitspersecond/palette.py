from numpy import ndarray, zeros, uint8
from .logger import Logger

PALETTE_SIZE = 16


class Palette(ndarray):
    """
    Palette class representing a fixed-size array of RGBA color values.
    Inherits from numpy.ndarray and allows setting colors with clamping
    to 4-bit precision for RGB values and full alpha transparency.
    """

    def __new__(cls) -> "Palette":
        obj = super().__new__(cls, (PALETTE_SIZE, 4), dtype=uint8)
        obj._version = 0
        return obj

    def __setitem__(self, index, value) -> None:
        """
        Sets an RGBA color value at the given index, clamping the RGB values
        to 4-bit precision and handling the alpha channel separately.
        """
        if isinstance(value, (list, tuple)) and len(value) == 4:
            r, g, b, a = value
            clamped_color = [
                self._clamp(r),
                self._clamp(g),
                self._clamp(b),
                self._clamp(a),
            ]
            super().__setitem__(index, clamped_color)
            if hasattr(self, "_version"):
                self._version += 1
            else:
                self._version = 1
        else:
            Logger().error("Expected a 4-element sequence for RGBA values.")
            raise ValueError("Expected a 4-element sequence for RGBA values.")

    @property
    def version(self) -> int:
        """
        Returns the current version number of the palette.
        """
        if not hasattr(self, "_version"):
            self._version = 0
        return self._version

    def _clamp(self, value: uint8) -> uint8:
        """
        Normalizes the given uint8 to a range of 16 values spread over 0x00 to 0xFF.
        """
        if value < 0x00:
            value = uint8(0x00)
        elif value > 0xFF:
            value = uint8(0xFF)
        nibble = value & 0xF0
        return uint8(nibble + (nibble >> 4))
