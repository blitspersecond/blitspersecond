"""The quad pillar against real pixels: UVs, corners, and painter order."""

import numpy as np
import pytest

from blitspersecond.common import Rect
from blitspersecond.graphics import Atlas, PixelBuffer
from blitspersecond.graphics.quads import Quads

pyglet = pytest.importorskip("pyglet")
SIZE = 16


@pytest.fixture(scope="module")
def window():
    from pyglet.math import Mat4

    try:
        win = pyglet.window.Window(width=SIZE, height=SIZE, visible=False)
    except Exception as exc:  # pragma: no cover - machine-dependent
        pytest.skip(f"no GL context available: {exc}")
    win.switch_to()
    win.projection = Mat4.orthogonal_projection(0, SIZE, 0, SIZE, -1, 1)
    yield win
    win.close()


def _atlas() -> Atlas:
    buffer = PixelBuffer((2, 1), "RGBA")
    with buffer.edit() as pixels:
        pixels[0, 0] = (255, 0, 0, 255)
        pixels[0, 1] = (0, 0, 255, 255)
    return Atlas(
        buffer,
        {"red": Rect(0, 0, 1, 1), "blue": Rect(1, 0, 1, 1)},
    )


def _draw(window, quads: Quads) -> np.ndarray:
    from pyglet.gl import (
        GL_COLOR_BUFFER_BIT,
        GL_RGBA,
        GL_UNSIGNED_BYTE,
        glClear,
        glClearColor,
        glReadPixels,
        glViewport,
    )

    window.switch_to()
    glViewport(0, 0, SIZE, SIZE)
    glClearColor(0.0, 0.0, 0.0, 1.0)
    glClear(GL_COLOR_BUFFER_BIT)
    quads.composite()
    data = (pyglet.gl.GLubyte * (SIZE * SIZE * 4))()
    glReadPixels(0, 0, SIZE, SIZE, GL_RGBA, GL_UNSIGNED_BYTE, data)
    return np.frombuffer(data, dtype=np.uint8).reshape(SIZE, SIZE, 4)


def test_later_commands_paint_over_earlier_commands(window) -> None:
    quads = Quads(_atlas())
    square = ((2, 2), (14, 2), (14, 14), (2, 14))
    quads.add(texture="red", corners=square)
    quads.add(texture="blue", corners=((6, 6), (10, 6), (10, 10), (6, 10)))
    pixels = _draw(window, quads)
    assert tuple(pixels[4, 4, :3]) == (255, 0, 0)
    assert tuple(pixels[8, 8, :3]) == (0, 0, 255)


def test_clear_rebuilds_the_next_draw(window) -> None:
    quads = Quads(_atlas())
    square = ((2, 2), (14, 2), (14, 14), (2, 14))
    quads.add(texture="red", corners=square)
    _draw(window, quads)
    quads.clear()
    quads.add(texture="blue", corners=square)
    pixels = _draw(window, quads)
    assert tuple(pixels[8, 8, :3]) == (0, 0, 255)


def test_all_four_destination_corners_shape_the_draw(window) -> None:
    quads = Quads(_atlas())
    quads.add(
        texture="blue", corners=((2, 2), (14, 4), (12, 14), (4, 12))
    )
    pixels = _draw(window, quads)
    assert tuple(pixels[8, 8, :3]) == (0, 0, 255)
    assert tuple(pixels[13, 13, :3]) == (0, 0, 0)


def test_a_trapezoid_uses_one_projective_mapping_not_two_affine_halves(
    window,
) -> None:
    buffer = PixelBuffer((4, 1), "RGBA")
    with buffer.edit() as pixels:
        pixels[0] = (
            (255, 0, 0, 255),
            (0, 255, 0, 255),
            (0, 0, 255, 255),
            (255, 255, 255, 255),
        )
    quads = Quads(Atlas(buffer, 4, 1))
    quads.add(
        texture=0,
        corners=((2, 2), (14, 2), (10, 14), (6, 14)),
    )

    pixels = _draw(window, quads)

    # The inverse homography puts this pixel at u=0.174 (red). Interpolating
    # UVs independently over the old two triangles put it at u=0.333 (green).
    assert tuple(pixels[8, 5, :3]) == (255, 0, 0)


def test_an_indexed_atlas_draws_through_the_layer_palette(window) -> None:
    buffer = PixelBuffer((1, 1), "P")
    with buffer.edit() as pixels:
        pixels[0, 0] = 1
    quads = Quads(Atlas(buffer, 1, 1))
    quads.palette.texels[0, 1] = (0, 255, 0, 255)
    quads.palette.dirty = True
    quads.add(texture=0, corners=((2, 2), (14, 2), (14, 14), (2, 14)))
    pixels = _draw(window, quads)
    assert tuple(pixels[8, 8, :3]) == (0, 255, 0)


def test_reversing_the_winding_can_mirror_the_texture(window) -> None:
    buffer = PixelBuffer((2, 1), "RGBA")
    with buffer.edit() as pixels:
        pixels[0, 0] = (255, 0, 0, 255)
        pixels[0, 1] = (0, 0, 255, 255)
    atlas = Atlas(buffer, 2, 1)

    forward = Quads(atlas)
    forward.add(texture=0, corners=((2, 2), (14, 2), (14, 14), (2, 14)))
    normal = _draw(window, forward)

    reverse = Quads(atlas)
    reverse.add(texture=0, corners=((14, 2), (2, 2), (2, 14), (14, 14)))
    mirrored = _draw(window, reverse)

    assert tuple(normal[8, 4, :3]) == (255, 0, 0)
    assert tuple(normal[8, 12, :3]) == (0, 0, 255)
    assert tuple(mirrored[8, 4, :3]) == (0, 0, 255)
    assert tuple(mirrored[8, 12, :3]) == (255, 0, 0)


def test_source_selects_pixels_local_to_the_named_texture(window) -> None:
    buffer = PixelBuffer((6, 1), "RGBA")
    with buffer.edit() as pixels:
        pixels[0, 0] = (255, 0, 255, 255)
        pixels[0, 1] = (255, 0, 0, 255)
        pixels[0, 2] = (0, 255, 0, 255)
        pixels[0, 3] = (0, 0, 255, 255)
        pixels[0, 4] = (255, 255, 255, 255)
        pixels[0, 5] = (255, 0, 255, 255)
    atlas = Atlas(buffer, {"strip": Rect(1, 0, 4, 1)})
    quads = Quads(atlas)
    quads.add(
        texture="strip",
        source=Rect(1, 0, 1, 1),
        corners=((2, 2), (14, 2), (14, 14), (2, 14)),
    )

    pixels = _draw(window, quads)

    assert tuple(pixels[8, 8, :3]) == (0, 255, 0)


def test_cropped_and_whole_texture_commands_can_share_one_draw(window) -> None:
    buffer = PixelBuffer((2, 1), "RGBA")
    with buffer.edit() as pixels:
        pixels[0, 0] = (255, 0, 0, 255)
        pixels[0, 1] = (0, 0, 255, 255)
    quads = Quads(Atlas(buffer, 2, 1))
    quads.add(
        texture=0,
        corners=((1, 1), (7, 1), (7, 15), (1, 15)),
    )
    quads.add(
        texture=0,
        source=(1, 0, 1, 1),
        corners=((9, 1), (15, 1), (15, 15), (9, 15)),
    )

    pixels = _draw(window, quads)

    assert tuple(pixels[8, 3, :3]) == (255, 0, 0)
    assert tuple(pixels[8, 5, :3]) == (0, 0, 255)
    assert tuple(pixels[8, 12, :3]) == (0, 0, 255)


def test_convex_clip_can_extend_and_tile_a_mapping_quad(window) -> None:
    buffer = PixelBuffer((4, 1), "RGBA")
    with buffer.edit() as pixels:
        pixels[0, 0] = (255, 0, 255, 255)
        pixels[0, 1] = (255, 0, 0, 255)
        pixels[0, 2] = (0, 0, 255, 255)
        pixels[0, 3] = (255, 0, 255, 255)
    quads = Quads(Atlas(buffer, {"pair": Rect(1, 0, 2, 1)}))
    quads.add(
        texture="pair",
        corners=((2, 2), (6, 2), (6, 6), (2, 6)),
        clip=Rect(2, 2, 12, 4),
    )

    pixels = _draw(window, quads)

    assert [tuple(pixels[4, x, :3]) for x in (3, 5, 7, 9, 11, 13)] == [
        (255, 0, 0),
        (0, 0, 255),
        (255, 0, 0),
        (0, 0, 255),
        (255, 0, 0),
        (0, 0, 255),
    ]


def test_clip_polygon_not_mapping_quad_controls_coverage(window) -> None:
    quads = Quads(_atlas())
    quads.add(
        texture="blue",
        corners=((4, 4), (12, 4), (12, 12), (4, 12)),
        clip=((2, 2), (14, 2), (8, 14)),
    )

    pixels = _draw(window, quads)

    assert tuple(pixels[6, 8, :3]) == (0, 0, 255)
    assert tuple(pixels[12, 3, :3]) == (0, 0, 0)
