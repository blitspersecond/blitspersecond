"""Integer-scaled presentation geometry."""

import pytest

from blitspersecond.display.internal.presentation import (
    PresentationViewport,
    integer_viewport,
)


def test_exact_4k_fit_uses_largest_integer_scale():
    assert integer_viewport(640, 360, 3840, 2160) == PresentationViewport(
        x=0,
        y=0,
        width=3840,
        height=2160,
        scale=6,
    )


def test_taller_mode_is_letterboxed_and_centred():
    assert integer_viewport(640, 360, 1920, 1200) == PresentationViewport(
        x=0,
        y=60,
        width=1920,
        height=1080,
        scale=3,
    )


def test_wider_mode_is_pillarboxed_and_centred():
    assert integer_viewport(640, 360, 2560, 1080) == PresentationViewport(
        x=320,
        y=0,
        width=1920,
        height=1080,
        scale=3,
    )


def test_odd_remainder_puts_extra_pixel_on_top_and_right():
    assert integer_viewport(640, 360, 1921, 1201) == PresentationViewport(
        x=0,
        y=60,
        width=1920,
        height=1080,
        scale=3,
    )


@pytest.mark.parametrize(
    "dimensions",
    [
        (0, 360, 1920, 1080),
        (640, -1, 1920, 1080),
        (640, 360, 0, 1080),
        (640, 360, 1920, -1),
        (640, 360, 639, 360),
        (640, 360, 640, 359),
    ],
)
def test_invalid_or_too_small_surfaces_are_rejected(dimensions):
    with pytest.raises(ValueError):
        integer_viewport(*dimensions)
