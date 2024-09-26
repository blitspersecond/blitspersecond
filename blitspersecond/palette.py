import numpy as np


class Palette(np.ndarray):
    """
    Palette class representing a fixed-size array of RGBA color values.
    Inherits from numpy.ndarray and allows setting colors with clamping
    to 4-bit precision for RGB values and full alpha transparency.
    """

    def __new__(cls) -> "Palette":
        """
        Creates a new instance of the Palette, initialized to 32 colors,
        with each color represented as an RGBA tuple.
        """
        obj = super().__new__(cls, (32, 4), dtype=np.uint8)
        obj.fill(0x00)  # Initializes the palette with 0 (black).
        return obj

    def __setitem__(self, index, value):
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
                self._clamp_alpha(
                    a
                ),  # TODO: Think about whether we want to use _clamp_alpha or use the _clamp method
            ]
            super().__setitem__(index, clamped_color)
        else:
            raise ValueError("Expected a 4-element sequence for RGBA values.")

    def _clamp(self, value: np.uint8) -> np.uint8:
        """
        Clamps the RGB values to 4-bit precision (nibbles).
        """
        if value < 0x00:
            value = 0x00
        elif value > 0xFF:
            value = 0xFF
        nibble = value & 0xF0
        return nibble + (nibble >> 4)

    def _clamp_alpha(self, value: np.uint8) -> np.uint8:
        """
        Clamps the alpha value to either fully transparent (0x00) or fully opaque (0xFF).
        """
        return 0x00 if value <= 0 else 0xFF
