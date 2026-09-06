"""Uploading only what changed.

`dirty` is a flag and a flag has no extent, so a buffer that changed one pixel
sent its whole self to the GPU. On a level-sized picture that is the
difference between a feature and not: 11.31ms for a 4096x4096 RGBA buffer
against 0.07ms for a 40x40 crater in it.

`PixelBuffer.upload(x, y, w, h)` is the mechanism; the EXTENT belongs to
whoever owns a camera, because what a layer knows that a buffer cannot is
which part of itself is being looked at. Sending the window is bounded by the
screen whatever the level costs -- 0.9MB, 0.09ms, half a percent of a frame --
so it can be done unconditionally and there is no staleness bug to have.
"""

from __future__ import annotations

import numpy as np
import pytest

from blitspersecond.graphics import PixelBuffer


def _buffer(size=(64, 32)) -> PixelBuffer:
    return PixelBuffer(size, "RGBA")


# -- the mechanism --------------------------------------------------------


def test_a_rectangle_outside_the_buffer_sends_nothing() -> None:
    buffer = _buffer()
    with buffer.edit() as px:
        px[0, 0] = (1, 2, 3, 4)
    buffer.upload(900, 900, 4, 4)
    assert buffer.dirty is True, "nothing was sent, so nothing is resolved"


def test_uploading_resolves_dirty_so_texture_does_not_resend(window) -> None:
    """Otherwise the region upload is followed by the whole-buffer one it was
    meant to replace, and costs more than doing nothing."""
    buffer = _buffer((128, 96))
    with buffer.edit() as px:
        px[:] = (10, 20, 30, 255)
    buffer.texture

    with buffer.edit() as px:
        px[30:42, 40:56] = (255, 0, 0, 255)
    assert buffer.dirty is True
    buffer.upload(40, 30, 16, 12)
    assert buffer.dirty is False


def test_it_does_not_consult_dirty(window) -> None:
    """The policy is the caller's. Sending the window every frame is
    defensible at 0.09ms and has no staleness bug in it."""
    buffer = _buffer((128, 96))
    with buffer.edit() as px:
        px[:] = (10, 20, 30, 255)
    buffer.texture
    buffer.pixels[30:42, 40:56] = (255, 0, 0, 255)   # NOT through edit()
    assert buffer.dirty is False, "a raw write marks nothing"
    buffer.upload(40, 30, 16, 12)
    assert np.array_equal(_readback(buffer), buffer.pixels)


# -- and it actually reaches the GPU --------------------------------------

pyglet = pytest.importorskip("pyglet")


@pytest.fixture(scope="module")
def window():
    try:
        win = pyglet.window.Window(width=64, height=64, visible=False)
    except Exception as exc:  # pragma: no cover - depends on the machine
        pytest.skip(f"no GL context available: {exc}")
    win.switch_to()
    yield win
    win.close()


def _readback(buffer: PixelBuffer) -> np.ndarray:
    from pyglet.gl import (
        GL_RGBA,
        GL_UNSIGNED_BYTE,
        GLubyte,
        glBindTexture,
        glGetTexImage,
    )

    texture = buffer.texture
    width, height = buffer.size
    raw = (GLubyte * (width * height * 4))()
    glBindTexture(texture.target, texture.id)
    glGetTexImage(texture.target, 0, GL_RGBA, GL_UNSIGNED_BYTE, raw)
    return np.frombuffer(raw, dtype=np.uint8).reshape(height, width, 4)


def test_a_region_lands_where_it_says_and_nowhere_else(window) -> None:
    buffer = _buffer((128, 96))
    with buffer.edit() as px:
        px[:] = (10, 20, 30, 255)
    buffer.texture

    with buffer.edit() as px:
        px[30:42, 40:56] = (255, 0, 0, 255)
    buffer.upload(40, 30, 16, 12)
    got = _readback(buffer)

    assert tuple(got[35, 45]) == (255, 0, 0, 255), "the patch is there"
    assert tuple(got[29, 40]) == (10, 20, 30, 255), "one row above is not"
    assert tuple(got[30, 39]) == (10, 20, 30, 255), "one column left is not"
    assert tuple(got[42, 56]) == (10, 20, 30, 255), "past the far corner is not"
    assert np.array_equal(got, buffer.pixels), "and the whole thing agrees"


def test_a_region_at_the_far_corner(window) -> None:
    """Skip-rows and skip-pixels are easy to get right in the middle and
    wrong at the edge."""
    buffer = _buffer((128, 96))
    with buffer.edit() as px:
        px[:] = (10, 20, 30, 255)
    buffer.texture
    with buffer.edit() as px:
        px[88:96, 120:128] = (0, 255, 0, 255)
    buffer.upload(120, 88, 8, 8)
    assert np.array_equal(_readback(buffer), buffer.pixels)


def test_a_rectangle_is_clipped_to_the_buffer(window) -> None:
    buffer = _buffer((128, 96))
    with buffer.edit() as px:
        px[:] = (10, 20, 30, 255)
    buffer.texture
    with buffer.edit() as px:
        px[88:96, 120:128] = (0, 255, 0, 255)
    buffer.upload(120, 88, 999, 999)          # runs off both edges
    assert np.array_equal(_readback(buffer), buffer.pixels)


def test_the_unpack_state_is_left_clean_for_the_next_buffer(window) -> None:
    """Unpack state is GLOBAL. Left set, the next whole-buffer upload anywhere
    reads its rows at the wrong stride and draws a sheared picture in a layer
    that never asked for a region."""
    narrow = _buffer((128, 96))
    with narrow.edit() as px:
        px[:] = (1, 2, 3, 255)
    narrow.texture
    with narrow.edit() as px:
        px[30:42, 40:56] = (255, 0, 0, 255)
    narrow.upload(40, 30, 16, 12)

    other = _buffer((64, 48))
    with other.edit() as px:
        px[:] = (9, 8, 7, 255)
        px[10:20, 10:20] = (0, 0, 255, 255)
    assert np.array_equal(_readback(other), other.pixels)


def test_an_indexed_buffer_uploads_a_region_too(window) -> None:
    """One channel rather than four, so the format differs and the stride
    arithmetic is the place that would not notice."""
    from pyglet.gl import (
        GL_RED,
        GL_UNSIGNED_BYTE,
        GLubyte,
        glBindTexture,
        glGetTexImage,
    )

    buffer = PixelBuffer((128, 96), "P")
    with buffer.edit() as px:
        px[:] = 3
    buffer.texture
    with buffer.edit() as px:
        px[30:42, 40:56] = 7
    buffer.upload(40, 30, 16, 12)

    texture = buffer.texture
    raw = (GLubyte * (128 * 96))()
    glBindTexture(texture.target, texture.id)
    glGetTexImage(texture.target, 0, GL_RED, GL_UNSIGNED_BYTE, raw)
    got = np.frombuffer(raw, dtype=np.uint8).reshape(96, 128)
    assert np.array_equal(got, buffer.pixels)


def test_uploading_before_there_is_a_texture_sends_the_whole_thing(window):
    """A first frame has nothing to patch into, and creating the texture
    uploads everything anyway -- which is the right answer, not a fallback."""
    buffer = _buffer((128, 96))
    with buffer.edit() as px:
        px[:] = (7, 7, 7, 255)
    assert buffer._texture is None
    buffer.upload(0, 0, 8, 8)
    assert buffer._texture is not None
    assert np.array_equal(_readback(buffer), buffer.pixels)
