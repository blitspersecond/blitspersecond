"""The two ragged walks in the quad pillar.

Commands and custom clip points arrive as packed NumPy arrays. Validating and
compacting variable convex perimeters, then expanding their spans into one
vertex stream and sequence of triangle fans, are nevertheless scalar walks:
expressing either with NumPy requires expanded owner/local-index arrays and
fresh prefix-sum intermediates, while Python dispatches many tiny operations.

That is the boundary this kernel owns. Projective mapping and texture-region
attributes remain ordinary broadcast NumPy in ``layer.py``. Compiling is not a
general quad policy; it is confined to the operation for which NumPy has
already fallen off its allocation-free bulk path.

The spans are private packed state. Their counts are structurally safe before
entry; the first walk rejects bad geometry and repacks the survivors before
the caller sizes expansion output from those same counts. Tests also compile
these kernels with bounds checking because an unchecked indexing mistake could
otherwise return plausible geometry.
"""

import numpy as np
from numba import njit

__all__ = ["compact_commands", "expand_geometry", "warm"]

_warmed = False


@njit(cache=True)
def _turn(ax, ay, bx, by, cx, cy):
    return (bx - ax) * (cy - by) - (by - ay) * (cx - bx)


@njit(cache=True)
def _edges_cross(ax, ay, bx, by, cx, cy, dx, dy):
    ab_c = _turn(ax, ay, bx, by, cx, cy)
    ab_d = _turn(ax, ay, bx, by, dx, dy)
    cd_a = _turn(cx, cy, dx, dy, ax, ay)
    cd_b = _turn(cx, cy, dx, dy, bx, by)
    return ab_c * ab_d <= 0.0 and cd_a * cd_b <= 0.0


@njit(cache=True)
def _valid_corners(points, command):
    direction = 0
    for index in range(4):
        following = (index + 1) % 4
        after = (index + 2) % 4
        ax = points[command, index, 0]
        ay = points[command, index, 1]
        bx = points[command, following, 0]
        by = points[command, following, 1]
        cx = points[command, after, 0]
        cy = points[command, after, 1]
        if not (
            np.isfinite(ax)
            and np.isfinite(ay)
            and np.isfinite(bx)
            and np.isfinite(by)
            and np.isfinite(cx)
            and np.isfinite(cy)
        ):
            return False
        turn = _turn(ax, ay, bx, by, cx, cy)
        if turn == 0.0:
            return False
        current = 1 if turn > 0.0 else -1
        if direction == 0:
            direction = current
        elif current != direction:
            return False
    if _edges_cross(
        points[command, 0, 0],
        points[command, 0, 1],
        points[command, 1, 0],
        points[command, 1, 1],
        points[command, 2, 0],
        points[command, 2, 1],
        points[command, 3, 0],
        points[command, 3, 1],
    ):
        return False
    return not _edges_cross(
        points[command, 1, 0],
        points[command, 1, 1],
        points[command, 2, 0],
        points[command, 2, 1],
        points[command, 3, 0],
        points[command, 3, 1],
        points[command, 0, 0],
        points[command, 0, 1],
    )


@njit(cache=True)
def _write_source(source_rects, atlas_rects, row, command, sources, target):
    x = source_rects[command, 0]
    y = source_rects[command, 1]
    width = source_rects[command, 2]
    height = source_rects[command, 3]
    if width == -1.0 and height == -1.0:
        sources[target, 0, 0] = 0.0
        sources[target, 0, 1] = 0.0
        sources[target, 1, 0] = 1.0
        sources[target, 1, 1] = 0.0
        sources[target, 2, 0] = 1.0
        sources[target, 2, 1] = 1.0
        sources[target, 3, 0] = 0.0
        sources[target, 3, 1] = 1.0
        return True
    if not (
        np.isfinite(x)
        and np.isfinite(y)
        and np.isfinite(width)
        and np.isfinite(height)
        and x == np.floor(x)
        and y == np.floor(y)
        and width == np.floor(width)
        and height == np.floor(height)
    ):
        return False
    texture_width = atlas_rects[row, 2]
    texture_height = atlas_rects[row, 3]
    if (
        x < 0.0
        or y < 0.0
        or width <= 0.0
        or height <= 0.0
        or x + width > texture_width
        or y + height > texture_height
    ):
        return False
    left = x / texture_width
    top = y / texture_height
    right = (x + width) / texture_width
    bottom = (y + height) / texture_height
    sources[target, 0, 0] = left
    sources[target, 0, 1] = top
    sources[target, 1, 0] = right
    sources[target, 1, 1] = top
    sources[target, 2, 0] = right
    sources[target, 2, 1] = bottom
    sources[target, 3, 0] = left
    sources[target, 3, 1] = bottom
    return True


@njit(cache=True)
def _valid_clip(points, offset, count):
    if count < 3:
        return False
    direction = 0
    for index in range(count):
        following = (index + 1) % count
        after = (index + 2) % count
        ax = points[offset + index, 0]
        ay = points[offset + index, 1]
        bx = points[offset + following, 0]
        by = points[offset + following, 1]
        cx = points[offset + after, 0]
        cy = points[offset + after, 1]
        if not (
            np.isfinite(ax)
            and np.isfinite(ay)
            and np.isfinite(bx)
            and np.isfinite(by)
            and np.isfinite(cx)
            and np.isfinite(cy)
        ):
            return False
        turn = _turn(ax, ay, bx, by, cx, cy)
        if turn == 0.0:
            return False
        current = 1 if turn > 0.0 else -1
        if direction == 0:
            direction = current
        elif current != direction:
            return False

    for first in range(count):
        second_point = (first + 1) % count
        for second in range(first + 1, count):
            if second == first + 1 or (first == 0 and second == count - 1):
                continue
            fourth_point = (second + 1) % count
            if _edges_cross(
                points[offset + first, 0],
                points[offset + first, 1],
                points[offset + second_point, 0],
                points[offset + second_point, 1],
                points[offset + second, 0],
                points[offset + second, 1],
                points[offset + fourth_point, 0],
                points[offset + fourth_point, 1],
            ):
                return False
    return True


@njit(cache=True)
def _corners_visible(points, command, width, height):
    min_x = max_x = points[command, 0, 0]
    min_y = max_y = points[command, 0, 1]
    for index in range(1, 4):
        x = points[command, index, 0]
        y = points[command, index, 1]
        min_x = min(min_x, x)
        max_x = max(max_x, x)
        min_y = min(min_y, y)
        max_y = max(max_y, y)
    return not (max_x < 0.0 or min_x > width or max_y < 0.0 or min_y > height)


@njit(cache=True)
def _clip_visible(points, offset, count, width, height):
    min_x = max_x = points[offset, 0]
    min_y = max_y = points[offset, 1]
    for index in range(1, count):
        x = points[offset + index, 0]
        y = points[offset + index, 1]
        min_x = min(min_x, x)
        max_x = max(max_x, x)
        min_y = min(min_y, y)
        max_y = max(max_y, y)
    return not (max_x < 0.0 or min_x > width or max_y < 0.0 or min_y > height)


@njit(cache=True)
def compact_commands(
    corners,
    rows,
    source_rects,
    sources,
    atlas_rects,
    clip_spans,
    clip_points,
    count,
    atlas_count,
    width,
    height,
):
    """Cull invalid/offscreen commands and compact survivors in place."""
    command_out = 0
    clip_out = 0
    for command in range(count):
        if rows[command] < 0 or rows[command] >= atlas_count:
            continue
        row = rows[command]
        if not _write_source(
            source_rects,
            atlas_rects,
            row,
            command,
            sources,
            command_out,
        ):
            continue
        offset = int(clip_spans[command, 0])
        clip_count = int(clip_spans[command, 1])
        if not _valid_corners(corners, command):
            continue
        if clip_count == 0:
            if not _corners_visible(corners, command, width, height):
                continue
        elif not _valid_clip(clip_points, offset, clip_count) or not _clip_visible(
            clip_points, offset, clip_count, width, height
        ):
            continue

        if command_out != command:
            rows[command_out] = rows[command]
            for value in range(4):
                source_rects[command_out, value] = source_rects[command, value]
            for corner in range(4):
                corners[command_out, corner, 0] = corners[command, corner, 0]
                corners[command_out, corner, 1] = corners[command, corner, 1]
        if clip_count == 0:
            clip_spans[command_out, 0] = 0
            clip_spans[command_out, 1] = 0
        else:
            for point in range(clip_count):
                clip_points[clip_out + point, 0] = clip_points[offset + point, 0]
                clip_points[clip_out + point, 1] = clip_points[offset + point, 1]
            clip_spans[command_out, 0] = clip_out
            clip_spans[command_out, 1] = clip_count
            clip_out += clip_count
        command_out += 1
    return command_out, clip_out


@njit(cache=True)
def expand_geometry(corners, clip_spans, clip_points, positions, owners, indices):
    """Expand packed clips into positions, owners and ordered triangle fans.

    ``clip_spans`` contains an offset and point count for each command. A zero
    count selects that command's four mapping corners. Outputs are retained
    allocations sized by the caller; the returned pair is their live vertex
    and index counts.
    """
    vertex = 0
    index_at = 0
    for command in range(corners.shape[0]):
        offset = int(clip_spans[command, 0])
        count = int(clip_spans[command, 1])
        if count == 0:
            count = 4
            for local in range(count):
                positions[vertex + local, 0] = corners[command, local, 0]
                positions[vertex + local, 1] = corners[command, local, 1]
                positions[vertex + local, 2] = 0.0
                owners[vertex + local] = command
        else:
            for local in range(count):
                positions[vertex + local, 0] = clip_points[offset + local, 0]
                positions[vertex + local, 1] = clip_points[offset + local, 1]
                positions[vertex + local, 2] = 0.0
                owners[vertex + local] = command

        for triangle in range(count - 2):
            indices[index_at] = vertex
            indices[index_at + 1] = vertex + triangle + 1
            indices[index_at + 2] = vertex + triangle + 2
            index_at += 3
        vertex += count
    return vertex, index_at


def warm() -> None:
    """Load the compiled signature before a quad layer can reach presentation."""
    global _warmed
    if _warmed:
        return
    corners = np.zeros((0, 4, 2), dtype=np.float32)
    spans = np.zeros((0, 2), dtype=np.uint32)
    clips = np.zeros((0, 2), dtype=np.float32)
    compact_commands(
        corners,
        np.zeros(0, dtype=np.int32),
        np.zeros((0, 4), dtype=np.float64),
        np.zeros((0, 4, 2), dtype=np.float32),
        np.zeros((0, 4), dtype=np.int32),
        spans,
        clips,
        0,
        0,
        640.0,
        360.0,
    )
    expand_geometry(
        corners,
        spans,
        clips,
        np.zeros((0, 3), dtype=np.float32),
        np.zeros(0, dtype=np.int32),
        np.zeros(0, dtype=np.uint32),
    )
    _warmed = True
