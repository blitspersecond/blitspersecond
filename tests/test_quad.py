"""The quad pillar's command-list and collision boundary."""

from math import cos, pi, sin

import numpy as np
import pytest

from blitspersecond.common import Rect
from blitspersecond.graphics import Atlas, PixelBuffer
from blitspersecond.graphics.quads import Quads
from blitspersecond.graphics.quads.layer import _inverse_homography


@pytest.fixture
def atlas() -> Atlas:
    return Atlas(
        PixelBuffer((2, 1), "RGBA"),
        {"red": Rect(0, 0, 1, 1), "blue": Rect(1, 0, 1, 1)},
    )


def test_one_atlas_and_an_ordered_command_list(atlas: Atlas) -> None:
    quads = Quads(atlas, tag="walls")
    assert quads.atlas is atlas
    assert quads.tag == "walls"
    assert len(quads) == 0

    assert (
        quads.add(
            texture="red", corners=((0, 0), (8, 0), (8, 8), (0, 8))
        )
        is None
    )
    quads.add(texture="blue", corners=((1, 1), (7, 1), (7, 7), (1, 7)))
    assert len(quads) == 2
    assert quads._rows[:2].tolist() == [0, 1]


def test_clear_retains_the_sheet_and_storage(atlas: Atlas) -> None:
    quads = Quads(atlas)
    quads.add(
        texture="red",
        corners=((0, 0), (1, 0), (1, 1), (0, 1)),
        clip=((0, 0), (2, 0), (1, 2)),
    )
    command_storage = quads._corners
    clip_storage = quads._clip_points
    quads.clear()
    assert len(quads) == 0
    assert quads.atlas is atlas
    assert quads._corners is command_storage
    assert quads._clip_points is clip_storage
    assert quads._clip_count == 0


def test_source_is_local_to_the_selected_texture(atlas: Atlas) -> None:
    quads = Quads(atlas)
    quads.add(
        texture="red",
        source=Rect(0, 0, 1, 1),
        corners=((0, 0), (1, 0), (1, 1), (0, 1)),
    )

    assert quads._source_rects[0].tolist() == [0.0, 0.0, 1.0, 1.0]
    assert quads._compact() == 0
    assert quads._sources[0].tolist() == [
        [0.0, 0.0],
        [1.0, 0.0],
        [1.0, 1.0],
        [0.0, 1.0],
    ]


def test_source_may_be_a_plain_pixel_rectangle() -> None:
    atlas = Atlas(PixelBuffer((8, 4), "RGBA"), 8, 4)
    quads = Quads(atlas)
    quads.add(
        texture=0,
        source=(2, 1, 4, 2),
        corners=((0, 0), (1, 0), (1, 1), (0, 1)),
    )

    assert quads._source_rects[0].tolist() == [2.0, 1.0, 4.0, 2.0]
    assert quads._compact() == 0
    assert quads._sources[0].tolist() == [
        [0.25, 0.25],
        [0.75, 0.25],
        [0.75, 0.75],
        [0.25, 0.75],
    ]


def test_clip_separates_texture_mapping_from_coverage(atlas: Atlas) -> None:
    quads = Quads(atlas)
    clip = ((-2, 2), (2, -2), (10, -2), (14, 2), (8, 12), (4, 12))
    quads.add(
        texture="red",
        corners=((0, 0), (8, 0), (8, 8), (0, 8)),
        clip=clip,
    )

    assert quads._clip_spans[0].tolist() == [0, len(clip)]
    assert quads._clip_points[: len(clip)].tolist() == [
        list(point) for point in clip
    ]


def test_rect_is_a_convenient_convex_clip(atlas: Atlas) -> None:
    quads = Quads(atlas)
    quads.add(
        texture="red",
        corners=((0, 0), (8, 0), (8, 8), (0, 8)),
        clip=Rect(0, 0, 640, 360),
    )

    assert quads._clip_spans[0].tolist() == [0, 4]
    assert quads._clip_points[:4].tolist() == [
        [0.0, 0.0],
        [640.0, 0.0],
        [640.0, 360.0],
        [0.0, 360.0],
    ]


def test_clips_are_packed_and_commands_hold_point_spans(atlas: Atlas) -> None:
    quads = Quads(atlas)
    square = ((0, 0), (8, 0), (8, 8), (0, 8))
    first = ((0, 0), (6, 0), (3, 6))
    second = ((1, 1), (7, 1), (8, 4), (4, 8), (0, 4))

    quads.add(texture="red", corners=square, clip=first)
    quads.add(texture="blue", corners=square)
    quads.add(texture="red", corners=square, clip=second)

    assert quads._clip_spans[:3].tolist() == [[0, 3], [0, 0], [3, 5]]
    assert quads._clip_count == 8
    assert quads._clip_points[:8].tolist() == [
        *[list(point) for point in first],
        *[list(point) for point in second],
    ]


def test_clear_rewinds_both_live_prefixes(atlas: Atlas) -> None:
    quads = Quads(atlas)
    square = ((0, 0), (8, 0), (8, 8), (0, 8))
    quads.add(
        texture="red",
        corners=square,
        clip=((0, 0), (8, 0), (8, 8), (4, 10), (0, 8)),
    )

    quads.clear()
    quads.add(
        texture="blue",
        corners=square,
        clip=((1, 1), (7, 1), (4, 7)),
    )

    assert quads._clip_spans[0].tolist() == [0, 3]
    assert quads._clip_count == 3
    assert quads._clip_points[:3].tolist() == [
        [1.0, 1.0],
        [7.0, 1.0],
        [4.0, 7.0],
    ]

    quads.clear()
    quads.add(texture="red", corners=square)

    assert quads._clip_spans[0].tolist() == [0, 0]
    assert quads._clip_count == 0


def test_clip_storage_grows_independently_and_keeps_live_points(
    atlas: Atlas,
) -> None:
    quads = Quads(atlas)
    square = ((0, 0), (8, 0), (8, 8), (0, 8))
    triangle = ((1, 1), (7, 1), (4, 7))
    polygon = tuple(
        (
            4.0 + 3.0 * cos(index * 2.0 * pi / 40),
            4.0 + 3.0 * sin(index * 2.0 * pi / 40),
        )
        for index in range(40)
    )

    quads.add(texture="red", corners=square, clip=triangle)
    quads.add(texture="blue", corners=square, clip=polygon)

    assert quads._clip_capacity >= 43
    assert quads._capacity == 8
    assert quads._clip_spans[:2].tolist() == [[0, 3], [3, 40]]
    assert quads._clip_points[:3].tolist() == [
        list(point) for point in triangle
    ]
    np.testing.assert_allclose(quads._clip_points[3:43], polygon)


@pytest.mark.parametrize(
    "clip",
    [
        (),
        ((0, 0), (1, 0)),
        ((0, 0), (1, 0), (1,)),
        ((0, 0), (np.inf, 0), (0, 1)),
        ((0, 0), (4, 0), (2, 1), (4, 4), (0, 4)),
        ((0, 0), (4, 4), (0, 4), (4, 0)),
    ],
)
def test_clip_must_be_a_finite_convex_perimeter(atlas: Atlas, clip) -> None:
    quads = Quads(atlas)
    quads.add(
        texture="red",
        corners=((0, 0), (8, 0), (8, 8), (0, 8)),
        clip=clip,
    )
    assert len(quads) == 1
    assert quads._compact() == 1
    assert len(quads) == 0


def test_mapping_corners_define_a_projective_coordinate_frame() -> None:
    corners = ((2.0, 2.0), (14.0, 4.0), (12.0, 14.0), (4.0, 12.0))
    mapping = _inverse_homography(corners)
    expected = ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))

    for point, uv in zip(corners, expected):
        homogeneous = mapping @ (*point, 1.0)
        assert homogeneous[:2] / homogeneous[2] == pytest.approx(uv)


@pytest.mark.parametrize(
    "source",
    [
        (-1, 0, 1, 1),
        (0, -1, 1, 1),
        (0, 0, 0, 1),
        (0, 0, 1, 0),
        (1, 0, 1, 1),
        (0, 1, 1, 1),
        (0, 0, 1),
        (0.5, 0, 1, 1),
    ],
)
def test_source_must_be_integer_pixels_inside_the_texture(
    atlas: Atlas, source
) -> None:
    quads = Quads(atlas)
    quads.add(
        texture="red",
        source=source,
        corners=((0, 0), (1, 0), (1, 1), (0, 1)),
    )
    assert len(quads) == 1
    assert quads._compact() == 1
    assert len(quads) == 0


def test_storage_grows_without_losing_painter_order(atlas: Atlas) -> None:
    quads = Quads(atlas)
    square = ((0, 0), (1, 0), (1, 1), (0, 1))
    for index in range(9):
        quads.add(texture=("red", "blue")[index % 2], corners=square)
    assert quads._compact() == 0
    assert len(quads) == 9
    assert quads._rows[:9].tolist() == [0, 1, 0, 1, 0, 1, 0, 1, 0]
    assert quads._sources[8].tolist() == [
        [0.0, 0.0],
        [1.0, 0.0],
        [1.0, 1.0],
        [0.0, 1.0],
    ]


def test_extend_is_a_contiguous_bulk_array_door(atlas: Atlas) -> None:
    quads = Quads(atlas)
    commands = quads.extend(3, clip_counts=(0, 3, 4))

    commands.texture_ids[:] = (
        quads.texture_id("red"),
        quads.texture_id("blue"),
        quads.texture_id("red"),
    )
    commands.corners[:] = (
        ((0, 0), (8, 0), (8, 8), (0, 8)),
        ((10, 0), (18, 0), (18, 8), (10, 8)),
        ((20, 0), (28, 0), (28, 8), (20, 8)),
    )
    commands.clip_points[:] = (
        (11, 1),
        (17, 1),
        (14, 7),
        (21, 1),
        (27, 1),
        (27, 7),
        (21, 7),
    )

    assert commands.texture_ids.base is not None
    assert commands.corners.base is not None
    assert commands.clip_points.base is not None
    assert commands.source_rects.base is not None
    assert quads._compact() == 0
    assert quads._rows[:3].tolist() == [0, 1, 0]
    assert quads._clip_spans[:3].tolist() == [[0, 0], [0, 3], [3, 4]]


def test_unwritten_or_bad_bulk_rows_are_culled(atlas: Atlas) -> None:
    quads = Quads(atlas)
    commands = quads.extend(3)
    commands.texture_ids[:2] = (quads.texture_id("red"), 999)
    commands.corners[:] = ((0, 0), (8, 0), (8, 8), (0, 8))

    assert quads._compact() == 2
    assert len(quads) == 1
    assert quads._rows[0] == quads.texture_id("red")


def test_bulk_source_rectangles_are_validated_and_normalised_together() -> None:
    atlas = Atlas(PixelBuffer((8, 4), "RGBA"), 8, 4)
    quads = Quads(atlas)
    commands = quads.extend(2)
    commands.texture_ids[:] = quads.texture_id(0)
    commands.corners[:] = ((0, 0), (8, 0), (8, 8), (0, 8))
    commands.source_rects[:] = ((2, 1, 4, 2), (7, 0, 2, 1))

    assert quads._compact() == 1
    assert len(quads) == 1
    assert quads._sources[0].tolist() == [
        [0.25, 0.25],
        [0.75, 0.25],
        [0.75, 0.75],
        [0.25, 0.75],
    ]


def test_clear_invalidates_a_bulk_command_view(atlas: Atlas) -> None:
    quads = Quads(atlas)
    commands = quads.extend(1)
    quads.clear()

    with pytest.raises(ValueError, match="invalidated"):
        _ = commands.corners


def test_bulk_edit_marks_the_layer_dirty_once(atlas: Atlas) -> None:
    quads = Quads(atlas)
    commands = quads.extend(1)
    commands.texture_ids[:] = quads.texture_id("red")
    commands.corners[:] = ((0, 0), (8, 0), (8, 8), (0, 8))
    quads._dirty = False

    with commands.edit():
        commands.corners[:, :, 0] += 1

    assert quads._dirty is True


def test_bad_texture_does_not_append(atlas: Atlas) -> None:
    quads = Quads(atlas)
    with pytest.raises(KeyError):
        quads.add(
            texture="missing", corners=((0, 0), (1, 0), (1, 1), (0, 1))
        )
    assert len(quads) == 0


@pytest.mark.parametrize(
    "corners",
    [
        ((0, 0), (1, 0), (1, 1)),
        ((0, 0), (1, 0), (1, 1), (0, 1), (2, 2)),
        ((0, 0), (1, 0), (1, 1), (0,)),
    ],
)
def test_a_command_needs_four_xy_corners(atlas: Atlas, corners) -> None:
    quads = Quads(atlas)
    quads.add(texture="red", corners=corners)
    assert len(quads) == 1
    assert quads._compact() == 1
    assert len(quads) == 0


def test_corners_must_be_finite(atlas: Atlas) -> None:
    quads = Quads(atlas)
    quads.add(
        texture="red",
        corners=((0, 0), (np.inf, 0), (1, 1), (0, 1)),
    )
    assert quads._compact() == 1
    assert len(quads) == 0


def test_both_winding_orders_are_valid(atlas: Atlas) -> None:
    quads = Quads(atlas)
    quads.add(texture="red", corners=((0, 0), (4, 0), (4, 4), (0, 4)))
    quads.add(texture="blue", corners=((4, 0), (0, 0), (0, 4), (4, 4)))
    assert quads._compact() == 0
    assert len(quads) == 2


@pytest.mark.parametrize(
    "corners",
    [
        ((0, 0), (4, 4), (0, 4), (4, 0)),  # crossing
        ((0, 0), (4, 0), (2, 1), (0, 4)),  # concave
        ((0, 0), (4, 0), (4, 0), (0, 4)),  # collapsed edge
    ],
)
def test_a_quad_must_be_a_convex_non_crossing_perimeter(
    atlas: Atlas, corners
) -> None:
    quads = Quads(atlas)
    quads.add(texture="red", corners=corners)
    assert len(quads) == 1
    assert quads._compact() == 1
    assert len(quads) == 0


@pytest.mark.parametrize(
    "corners",
    [
        ((-20, 10), (-10, 10), (-10, 20), (-20, 20)),
        ((650, 10), (660, 10), (660, 20), (650, 20)),
        ((10, -20), (20, -20), (20, -10), (10, -10)),
        ((10, 370), (20, 370), (20, 380), (10, 380)),
    ],
)
def test_a_quad_wholly_beyond_one_screen_edge_is_batch_culled(
    atlas: Atlas, corners
) -> None:
    quads = Quads(atlas)
    assert quads.add(texture="red", corners=corners) is None
    assert quads._compact() == 1
    assert len(quads) == 0


def test_a_partially_visible_quad_is_kept(atlas: Atlas) -> None:
    quads = Quads(atlas)
    quads.add(texture="red", corners=((-5, 5), (5, 5), (5, 15), (-5, 15)))
    assert quads._compact() == 0
    assert len(quads) == 1


def test_a_point_on_the_screen_extent_is_enough_to_keep_it(atlas: Atlas) -> None:
    quads = Quads(atlas)
    quads.add(
        texture="red", corners=((0, 10), (-10, 5), (-20, 10), (-10, 15))
    )
    assert quads._compact() == 0
    assert len(quads) == 1


def test_a_quad_surrounding_the_screen_is_kept(atlas: Atlas) -> None:
    quads = Quads(atlas)
    quads.add(
        texture="red",
        corners=((-10, -10), (650, -10), (650, 370), (-10, 370)),
    )
    assert quads._compact() == 0
    assert len(quads) == 1


def test_quad_is_structurally_non_collidable(atlas: Atlas) -> None:
    quads = Quads(atlas)
    assert quads.collidable is False
    with pytest.raises(ValueError, match="presentation only"):
        quads.collidable = True
    with pytest.raises(ValueError, match="presentation only"):
        quads.collider()


def test_constructor_needs_an_atlas() -> None:
    with pytest.raises(TypeError, match="draws from an Atlas"):
        Quads(object())  # type: ignore[arg-type]
