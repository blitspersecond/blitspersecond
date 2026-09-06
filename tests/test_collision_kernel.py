"""The compiled collision kernel, against a brute-force reference.

Two things this file has to do that an ordinary test file does not.

**Fuzz rather than enumerate.** The kernel is a funnel shift with four
independent clipping edges and two offsets; the interesting cases are the
ones nobody thinks to write down. So it is checked against a bit-by-bit
reference in plain Python -- obviously correct, hopelessly slow -- over
thousands of random configurations.

**Run the whole fuzz again with bounds checking on.** Numba does not check
indices by default: hand it a bad one and it reads whatever is next in memory
and returns a plausible answer. The first draft of the kernel did precisely
that, and passed 4000 random cases against this reference before anyone
noticed, because the memory it was reading happened to be zeros. A fuzz that
cannot see out-of-range reads is not testing the thing most likely to be
wrong.
"""

import numpy as np
import pytest
from numba import njit

from blitspersecond.graphics.collision.kernel import collide_any

#: The same source, compiled a second time with index checking on. Built from
#: `py_func` so it cannot drift from the shipped kernel.
checked = njit(cache=False, boundscheck=True)(collide_any.py_func)

ROWS, WORDS = 48, 4
WIDTH = WORDS * 32


def _unpack(mask):
    return np.unpackbits(
        np.ascontiguousarray(mask).view(np.uint8),
        axis=1, bitorder="little", count=WIDTH,
    ).view(np.bool_)


def _reference(a, b, ax, ay, bx, by, x0, x1, y0, y1):
    """One bit at a time, in Python. Screen (x, y) reads plane (x+off, y+off);
    anything outside a plane is empty."""
    ab, bb = _unpack(a), _unpack(b)
    for y in range(y0, y1):
        for x in range(x0, x1):
            py, px = y + ay, x + ax
            qy, qx = y + by, x + bx
            first = ab[py, px] if (0 <= py < ROWS and 0 <= px < WIDTH) else False
            if not first:
                continue
            if bb[qy, qx] if (0 <= qy < ROWS and 0 <= qx < WIDTH) else False:
                return True
    return False


def _case(rng):
    density = rng.choice([0.001, 0.02, 0.25, 0.95])
    a = rng.random((ROWS, WIDTH)) < density
    b = rng.random((ROWS, WIDTH)) < density
    pack = lambda m: np.packbits(m, axis=1, bitorder="little").view(np.uint32).copy()
    # Offsets run well past both ends, so every clipping edge is exercised.
    ax, bx = (int(rng.integers(-40, 41)) for _ in range(2))
    ay, by = (int(rng.integers(-8, 9)) for _ in range(2))
    x0 = int(rng.integers(0, WIDTH))
    y0 = int(rng.integers(0, ROWS))
    x1 = min(x0 + int(rng.integers(1, 48)), WIDTH)
    y1 = min(y0 + int(rng.integers(1, 10)), ROWS)
    return pack(a), pack(b), ax, ay, bx, by, x0, x1, y0, y1


@pytest.mark.parametrize("kernel", [collide_any, checked],
                         ids=["shipped", "boundscheck"])
def test_kernel_matches_a_bit_by_bit_reference(kernel):
    rng = np.random.default_rng(20260904)
    checked_count = 0
    for _ in range(1500):
        a, b, ax, ay, bx, by, x0, x1, y0, y1 = _case(rng)
        if x1 <= x0 or y1 <= y0:
            continue
        checked_count += 1
        assert bool(kernel(a, ax, ay, b, bx, by, x0, x1, y0, y1)) is _reference(
            a, b, ax, ay, bx, by, x0, x1, y0, y1
        ), f"offsets ({ax},{ay})/({bx},{by}) window ({x0},{x1},{y0},{y1})"
    assert checked_count > 1000


# -- the cases worth naming ----------------------------------------------


def _blank():
    return np.zeros((ROWS, WORDS), dtype=np.uint32)


def _dot(x, y):
    mask = _blank()
    mask[y, x >> 5] |= np.uint32(1 << (x & 31))
    return mask


def test_coincident_pixels_collide():
    assert collide_any(_dot(40, 9), 0, 0, _dot(40, 9), 0, 0, 0, WIDTH, 0, ROWS)


def test_adjacent_pixels_do_not():
    assert not collide_any(_dot(40, 9), 0, 0, _dot(41, 9), 0, 0, 0, WIDTH, 0, ROWS)
    assert not collide_any(_dot(40, 9), 0, 0, _dot(40, 10), 0, 0, 0, WIDTH, 0, ROWS)


def test_an_offset_moves_a_plane_under_the_screen():
    # B's pixel is at plane x=40; with offset 5 it appears at screen 35.
    a, b = _dot(35, 9), _dot(40, 9)
    assert collide_any(a, 0, 0, b, 5, 0, 0, WIDTH, 0, ROWS)
    assert not collide_any(a, 0, 0, b, 4, 0, 0, WIDTH, 0, ROWS)


def test_a_vertical_offset_is_just_a_row():
    a, b = _dot(40, 9), _dot(40, 12)
    assert collide_any(a, 0, 0, b, 0, 3, 0, WIDTH, 0, ROWS)
    assert not collide_any(a, 0, 0, b, 0, 2, 0, WIDTH, 0, ROWS)


@pytest.mark.parametrize("x", [31, 32, 33, 63, 64])
def test_a_pixel_on_a_word_boundary(x):
    a = b = _dot(x, 5)
    assert collide_any(a, 0, 0, b, 0, 0, x, x + 1, 5, 6)
    assert not collide_any(a, 0, 0, b, 0, 0, x + 1, x + 2, 5, 6)
    assert not collide_any(a, 0, 0, b, 0, 0, x - 1, x, 5, 6)


def test_the_window_is_half_open():
    a = b = _dot(40, 5)
    assert not collide_any(a, 0, 0, b, 0, 0, 30, 40, 5, 6)   # stops before 40
    assert collide_any(a, 0, 0, b, 0, 0, 30, 41, 5, 6)


def test_a_window_wider_than_a_chunk_trims_only_its_last():
    # 70 pixels is two full chunks and a 6-bit tail; a bit past the tail must
    # not answer for it.
    a = b = _dot(70, 5)
    assert not collide_any(a, 0, 0, b, 0, 0, 0, 70, 5, 6)
    assert collide_any(a, 0, 0, b, 0, 0, 0, 71, 5, 6)


def test_reading_off_the_plane_is_empty_not_an_error():
    a = b = _dot(40, 5)
    assert not collide_any(a, 500, 0, b, 500, 0, 0, WIDTH, 0, ROWS)
    assert not collide_any(a, -500, 0, b, -500, 0, 0, WIDTH, 0, ROWS)
    assert not collide_any(a, 0, 900, b, 0, 900, 0, WIDTH, 0, ROWS)


def test_empty_planes_never_collide():
    assert not collide_any(_blank(), 0, 0, _blank(), 0, 0, 0, WIDTH, 0, ROWS)


def test_an_empty_window_is_false():
    a = b = _dot(40, 5)
    assert not collide_any(a, 0, 0, b, 0, 0, 40, 40, 5, 6)
    assert not collide_any(a, 0, 0, b, 0, 0, 40, 41, 5, 5)


def test_planes_of_different_shapes_are_allowed():
    # A screen-sized sprite plane against a bordered tile plane is the normal
    # cross-pillar case; neither side may assume the other's dimensions.
    small = _dot(40, 5)
    big = np.zeros((ROWS * 2, WORDS + 3), dtype=np.uint32)
    big[5, 40 >> 5] |= np.uint32(1 << (40 & 31))
    assert collide_any(small, 0, 0, big, 0, 0, 40, 41, 5, 6)
    assert collide_any(big, 0, 0, small, 0, 0, 40, 41, 5, 6)
