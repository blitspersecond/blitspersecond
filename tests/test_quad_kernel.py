"""The compiled ragged-clip expansion against a plain reference."""

import numpy as np
import pytest
from numba import njit

from blitspersecond.graphics.quads import kernel as quad_kernel
from blitspersecond.graphics.quads.kernel import compact_commands, expand_geometry


# The shipped source with Numba's normally-disabled bounds checks enabled. It
# cannot drift from the real kernel, and random cases exercise both versions.
checked = njit(cache=False, boundscheck=True)(expand_geometry.py_func)
checked_compact = njit(cache=False, boundscheck=True)(compact_commands.py_func)


def _outputs(vertex_capacity: int):
    return (
        np.zeros((vertex_capacity, 3), dtype=np.float32),
        np.zeros(vertex_capacity, dtype=np.int32),
        np.zeros(vertex_capacity * 3, dtype=np.uint32),
    )


def _reference(corners, spans, clip_points):
    positions = []
    owners = []
    indices = []
    for command, (offset, count) in enumerate(spans):
        if count == 0:
            points = corners[command]
        else:
            first = int(offset)
            points = clip_points[first : first + int(count)]
        vertex = len(positions)
        positions.extend((float(x), float(y), 0.0) for x, y in points)
        owners.extend([command] * len(points))
        for local in range(1, len(points) - 1):
            indices.extend((vertex, vertex + local, vertex + local + 1))
    return (
        np.asarray(positions, dtype=np.float32).reshape(-1, 3),
        np.asarray(owners, dtype=np.int32),
        np.asarray(indices, dtype=np.uint32),
    )


def _case(rng):
    commands = int(rng.integers(0, 80))
    corners = rng.uniform(-1000.0, 1000.0, (commands, 4, 2)).astype(
        np.float32
    )
    counts = rng.choice((0, 3, 4, 5, 6, 7, 8), commands)
    spans = np.zeros((commands, 2), dtype=np.uint32)
    point_count = int(counts.sum())
    clip_points = rng.uniform(-1000.0, 1000.0, (point_count, 2)).astype(
        np.float32
    )
    cursor = 0
    for command, count in enumerate(counts):
        if count:
            spans[command] = (cursor, count)
            cursor += int(count)
    vertex_count = int(np.where(counts == 0, 4, counts).sum())
    return corners, spans, clip_points, vertex_count


@pytest.mark.parametrize(
    "kernel", [expand_geometry, checked], ids=["shipped", "boundscheck"]
)
def test_kernel_matches_a_plain_ragged_reference(kernel) -> None:
    rng = np.random.default_rng(20260912)
    for _ in range(200):
        corners, spans, clip_points, vertex_count = _case(rng)
        expected = _reference(corners, spans, clip_points)
        positions, owners, indices = _outputs(vertex_count)

        vertices, index_count = kernel(
            corners, spans, clip_points, positions, owners, indices
        )

        assert vertices == len(expected[0])
        assert index_count == len(expected[2])
        np.testing.assert_array_equal(positions[:vertices], expected[0])
        np.testing.assert_array_equal(owners[:vertices], expected[1])
        np.testing.assert_array_equal(indices[:index_count], expected[2])


def test_default_corners_and_custom_clips_keep_command_order() -> None:
    corners = np.asarray(
        (
            ((0, 0), (4, 0), (4, 4), (0, 4)),
            ((10, 10), (14, 10), (14, 14), (10, 14)),
        ),
        dtype=np.float32,
    )
    spans = np.asarray(((0, 0), (0, 3)), dtype=np.uint32)
    clips = np.asarray(((11, 11), (15, 11), (13, 15)), dtype=np.float32)
    positions, owners, indices = _outputs(7)

    vertices, index_count = expand_geometry(
        corners, spans, clips, positions, owners, indices
    )

    assert (vertices, index_count) == (7, 9)
    assert owners[:vertices].tolist() == [0, 0, 0, 0, 1, 1, 1]
    assert positions[:vertices, :2].tolist() == [
        [0.0, 0.0],
        [4.0, 0.0],
        [4.0, 4.0],
        [0.0, 4.0],
        [11.0, 11.0],
        [15.0, 11.0],
        [13.0, 15.0],
    ]
    assert indices[:index_count].tolist() == [0, 1, 2, 0, 2, 3, 4, 5, 6]


@pytest.mark.parametrize(
    "kernel", [compact_commands, checked_compact], ids=["shipped", "boundscheck"]
)
def test_compaction_culls_bad_geometry_and_preserves_painter_order(kernel) -> None:
    visible = ((10, 10), (20, 10), (20, 20), (10, 20))
    corners = np.asarray(
        (
            visible,
            ((10, 10), (20, 20), (10, 20), (20, 10)),  # crossing
            ((-40, 10), (-30, 10), (-30, 20), (-40, 20)),
            ((700, 10), (710, 10), (710, 20), (700, 20)),
            visible,
            visible,
        ),
        dtype=np.float32,
    )
    rows = np.arange(6, dtype=np.int32)
    source_rects = np.repeat(
        np.asarray(((0.0, 0.0, -1.0, -1.0),)), 6, axis=0
    )
    source_rects[4] = np.nan
    sources = np.empty((6, 4, 2), dtype=np.float32)
    atlas_rects = np.repeat(
        np.asarray(((0, 0, 8, 8),), dtype=np.int32), 6, axis=0
    )
    clips = np.asarray(
        (
            (10, 10),
            (20, 10),
            (15, 20),
            (30, 30),
            (40, 30),
            (40, 40),
            (30, 40),
        ),
        dtype=np.float32,
    )
    spans = np.asarray(
        ((0, 0), (0, 0), (0, 3), (0, 0), (0, 0), (3, 4)),
        dtype=np.uint32,
    )

    count, clip_count = kernel(
        corners,
        rows,
        source_rects,
        sources,
        atlas_rects,
        spans,
        clips,
        6,
        6,
        640.0,
        360.0,
    )

    assert (count, clip_count) == (3, 7)
    assert rows[:count].tolist() == [0, 2, 5]
    assert spans[:count].tolist() == [[0, 0], [0, 3], [3, 4]]
    assert clips[:clip_count].tolist() == [
        [10.0, 10.0],
        [20.0, 10.0],
        [15.0, 20.0],
        [30.0, 30.0],
        [40.0, 30.0],
        [40.0, 40.0],
        [30.0, 40.0],
    ]


def test_warm_loads_the_one_shipped_signature_once(monkeypatch) -> None:
    calls = 0
    real = quad_kernel.expand_geometry

    def counted(*args):
        nonlocal calls
        calls += 1
        return real(*args)

    monkeypatch.setattr(quad_kernel, "_warmed", False)
    monkeypatch.setattr(quad_kernel, "expand_geometry", counted)

    quad_kernel.warm()
    quad_kernel.warm()

    assert calls == 1
