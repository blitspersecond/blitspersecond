import numpy as np

from blitspersecond.graphics import GlyphEngine
from tests.examples.clouds import CloudField, install_quadrants, paint


def test_clouds_defines_all_quadrants_through_the_public_glyph_api():
    glyphs = GlyphEngine((8, 8))

    install_quadrants(glyphs)

    for code in range(16):
        pattern = glyphs.glyph[code]
        assert np.all(pattern[:4, :4] == bool(code & 0b0001))
        assert np.all(pattern[:4, 4:] == bool(code & 0b0010))
        assert np.all(pattern[4:, :4] == bool(code & 0b0100))
        assert np.all(pattern[4:, 4:] == bool(code & 0b1000))


def test_clouds_paints_through_public_cells_and_pen():
    glyphs = GlyphEngine((8, 8))
    install_quadrants(glyphs)
    clouds = CloudField()
    clouds.render = lambda _tick: (
        np.ones((90, 160), dtype=np.float32),
        np.ones((90, 160), dtype=np.float32),
        np.ones((90, 160), dtype=np.float32),
    )

    paint(glyphs, clouds, 0)

    assert all(
        glyphs.cell[x, y] == 15
        for y in range(glyphs.rows)
        for x in range(glyphs.cols)
    )
    assert np.all(glyphs._image.pixels == 40)
