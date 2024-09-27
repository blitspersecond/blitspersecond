import sys
import os
import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import pytest
from blitspersecond.palette import Palette


def test_palette_initialization():
    palette = Palette()
    assert isinstance(palette, np.ndarray)
    assert palette.shape == (32, 4)
    assert (palette == 0x00).all()


def test_palette_setitem():
    palette = Palette()
    palette[0] = [255, 128, 64, 255]

    # Adjust expectations based on the new clamping logic
    assert list(palette[0]) == [0xFF, 0x88, 0x44, 0xFF]

    with pytest.raises(ValueError):
        palette[0] = [255, 128, 64]  # Should raise an error


def test_clamp():
    palette = Palette()

    # Clamping based on steps of 0x11
    assert palette._clamp(255) == 0xFF  # 255 should map to 0xFF
    assert palette._clamp(128) == 0x88  # 128 should map to 0x88
    assert palette._clamp(0) == 0x00  # 0 should map to 0x00
    assert palette._clamp(17) == 0x11  # 17 should map to 0x11
    assert palette._clamp(34) == 0x22  # 34 should map to 0x22


def test_clamp_alpha():
    palette = Palette()

    # We expect alpha 0 to clamp to 0x00 (transparent)
    assert palette._clamp_alpha(0) == 0x00

    # We expect alpha 1 to clamp to 0xFF (opaque)
    assert palette._clamp_alpha(1) == 0xFF

    # We expect alpha 255 to clamp to 0xFF (opaque)
    assert palette._clamp_alpha(255) == 0xFF
