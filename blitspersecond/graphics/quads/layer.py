"""`Quads`: one Atlas and an ordered list of screen-space draw commands."""

from __future__ import annotations

from contextlib import contextmanager
from operator import index
from typing import Iterator, Optional, Sequence, Tuple, Union

import numpy as np
from pyglet.gl import GL_TEXTURE0, GL_TEXTURE1, glActiveTexture, glBindTexture

from blitspersecond.common import Rect

from ..atlas import Atlas
from ..internal.layer import Layer
from ..palette import PaletteBank
from . import shader
from .batch import QuadBatch
from .kernel import compact_commands, expand_geometry, warm

TextureKey = Union[int, str]
Corner = Tuple[float, float]
Source = Union[Rect, Tuple[int, int, int, int]]
Clip = Union[Rect, Sequence[Corner]]

_FULL_SOURCE = ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))
_FULL_SOURCE_RECT = (0.0, 0.0, -1.0, -1.0)
_BAD_POINTS = np.full((4, 2), np.nan, dtype=np.float32)
_BAD_CLIP = np.full((3, 2), np.nan, dtype=np.float32)
_BAD_SOURCE = np.full(4, np.nan, dtype=np.float64)


class QuadCommands:
    """The contiguous command block made by one :meth:`Quads.extend` call.

    Its arrays are live views into the layer, following the same bulk-door
    shape as Sprite presentations. Texture names become integer ids once via
    :meth:`Quads.texture_id`; command and packed clip geometry then enter as
    ordinary NumPy writes with no scalar setter bridge.
    """

    __slots__ = (
        "_quads",
        "_start",
        "_count",
        "_clip_start",
        "_clip_count",
        "_generation",
    )

    def __init__(
        self,
        quads: "Quads",
        start: int,
        count: int,
        clip_start: int,
        clip_count: int,
    ) -> None:
        self._quads = quads
        self._start = start
        self._count = count
        self._clip_start = clip_start
        self._clip_count = clip_count
        self._generation = quads._generation

    def _live(self) -> None:
        if self._generation != self._quads._generation:
            raise ValueError(
                "this quad command block was invalidated by clear() or culling"
            )

    @property
    def texture_ids(self) -> np.ndarray:
        """Writable ``(n,)`` int32 view; ids come from ``texture_id()``."""
        self._live()
        return self._quads._rows[self._start : self._start + self._count]

    @property
    def corners(self) -> np.ndarray:
        """Writable ``(n, 4, 2)`` float32 mapping-corner view."""
        self._live()
        return self._quads._corners[self._start : self._start + self._count]

    @property
    def clip_points(self) -> np.ndarray:
        """Writable packed ``(m, 2)`` float32 clip-point view."""
        self._live()
        start = self._clip_start
        return self._quads._clip_points[start : start + self._clip_count]

    @property
    def source_rects(self) -> np.ndarray:
        """Writable ``(n, 4)`` pixel source rectangles; defaults to whole."""
        self._live()
        return self._quads._source_rects[
            self._start : self._start + self._count
        ]

    @contextmanager
    def edit(self) -> Iterator["QuadCommands"]:
        """Mark the layer dirty once around later writes through the views."""
        self._live()
        try:
            yield self
        finally:
            self._quads._dirty = True

    def __len__(self) -> int:
        return self._count


class Quads(Layer):
    """An ordered, collisionless list of clipped projective textures.

        quads = Quads(atlas)
        quads.add(texture="STARTAN3", corners=(p1, p2, p3, p4))
        quads.clear()                 # begin the next command list

    Four screen-space corners define where one copy of the texture lies. By
    default they are also its clip, preserving an ordinary finite quad. An
    optional convex screen-space polygon can instead clip the projective
    texture anywhere -- including the viewport, which is an infinite tiled
    plane without infinite coordinates.

    Later commands draw over earlier ones. The list is never sorted, and its
    one Atlas is assigned at construction and cannot be rebound. Collision is
    structurally absent: the layer keeps no mask and cannot be switched on.
    """

    def __init__(self, atlas: Atlas, *, tag: Optional[str] = None) -> None:
        super().__init__(tag=tag)
        if not isinstance(atlas, Atlas):
            raise TypeError(
                f"a quad layer draws from an Atlas; got {type(atlas).__name__}. "
                f"Cut one from a picture with Atlas(PixelBuffer(path), ...)."
            )
        # The clip compiler needs no GL context, so settle its one-time Numba
        # load here rather than making the first presented frame pay for it.
        warm()
        self._atlas = atlas
        self._palette = PaletteBank()
        embedded = atlas.buffer.embedded_palette
        if embedded is not None:
            self._palette.texels[0, : len(embedded), :3] = embedded
            self._palette.texels[0, 0, 3] = 0
            self._palette.dirty = True

        self._capacity = 8
        self._count = 0
        self._generation = 0
        self._corners = np.zeros((self._capacity, 4, 2), dtype=np.float32)
        self._rows = np.zeros(self._capacity, dtype=np.int32)
        self._source_rects = np.empty((self._capacity, 4), dtype=np.float64)
        self._sources = np.zeros((self._capacity, 4, 2), dtype=np.float32)
        self._mappings = np.zeros((self._capacity, 3, 3), dtype=np.float64)
        # A command's clip is a span into one packed point array. Count zero
        # means its four mapping corners are also its coverage polygon. The
        # two cursors grow and rewind independently, so neither allocation is
        # cleared or moved merely because a new command list begins.
        self._clip_spans = np.zeros((self._capacity, 2), dtype=np.uint32)
        self._clip_capacity = self._capacity * 4
        self._clip_count = 0
        self._clip_points = np.zeros(
            (self._clip_capacity, 2), dtype=np.float32
        )
        self._uvs = np.zeros((self._capacity, 4, 3), dtype=np.float32)
        self._vertex_capacity = self._capacity * 4
        self._positions = np.zeros((self._vertex_capacity, 3), dtype=np.float32)
        self._projective = np.zeros((self._vertex_capacity, 3), dtype=np.float32)
        self._regions = np.zeros((self._vertex_capacity, 4), dtype=np.float32)
        self._indices = np.zeros(self._vertex_capacity * 3, dtype=np.uint32)
        self._owners = np.zeros(self._vertex_capacity, dtype=np.int32)
        self._vertex_count = 0
        self._index_count = 0
        self._has_crops = False
        self._dirty = False
        self._batch: Optional[QuadBatch] = None

    @property
    def atlas(self) -> Atlas:
        """The fixed sheet every command selects a texture from."""
        return self._atlas

    @property
    def palette(self) -> PaletteBank:
        """This layer's colour tables; used only by an indexed Atlas."""
        return self._palette

    @palette.setter
    def palette(self, value: PaletteBank) -> None:
        if not isinstance(value, PaletteBank):
            raise TypeError(f"expected PaletteBank, got {type(value)}")
        self._palette = value

    def __len__(self) -> int:
        """The number of commands currently in painter order."""
        return self._count

    def texture_id(self, key: TextureKey) -> int:
        """Resolve one Atlas key for broadcasting through a bulk block."""
        return self._atlas._row(key)

    def _collision_refusal(self) -> str:
        return "quad layers are presentation only and keep no collision masks"

    def _grow(self) -> None:
        capacity = self._capacity * 2
        corners = np.zeros((capacity, 4, 2), dtype=np.float32)
        corners[: self._count] = self._corners[: self._count]
        rows = np.zeros(capacity, dtype=np.int32)
        rows[: self._count] = self._rows[: self._count]
        source_rects = np.empty((capacity, 4), dtype=np.float64)
        source_rects[: self._count] = self._source_rects[: self._count]
        mappings = np.zeros((capacity, 3, 3), dtype=np.float64)
        mappings[: self._count] = self._mappings[: self._count]
        clip_spans = np.zeros((capacity, 2), dtype=np.uint32)
        clip_spans[: self._count] = self._clip_spans[: self._count]
        self._corners = corners
        self._rows = rows
        self._source_rects = source_rects
        self._sources = np.zeros((capacity, 4, 2), dtype=np.float32)
        self._mappings = mappings
        self._clip_spans = clip_spans
        self._uvs = np.zeros((capacity, 4, 3), dtype=np.float32)
        self._capacity = capacity

    def _grow_clips(self, required: int) -> None:
        capacity = self._clip_capacity
        while capacity < required:
            capacity *= 2
        points = np.zeros((capacity, 2), dtype=np.float32)
        points[: self._clip_count] = self._clip_points[: self._clip_count]
        self._clip_points = points
        self._clip_capacity = capacity

    def _grow_vertices(self, required: int) -> None:
        capacity = self._vertex_capacity
        while capacity < required:
            capacity *= 2
        self._positions = np.zeros((capacity, 3), dtype=np.float32)
        self._projective = np.zeros((capacity, 3), dtype=np.float32)
        self._regions = np.zeros((capacity, 4), dtype=np.float32)
        self._indices = np.zeros(capacity * 3, dtype=np.uint32)
        self._owners = np.zeros(capacity, dtype=np.int32)
        self._vertex_capacity = capacity

    def add(
        self,
        *,
        texture: TextureKey,
        corners: Sequence[Corner],
        source: Optional[Source] = None,
        clip: Optional[Clip] = None,
    ) -> None:
        """Append one projective texture to the painter-ordered command list.

        `texture` is a key in this layer's Atlas. `source`, when given, is an
        `(x, y, width, height)` pixel rectangle inside that texture, with its
        origin at the texture's top-left; omitting it selects the whole
        texture. `corners` is exactly four finite `(x, y)` pairs, p1 through
        p4 around a convex perimeter and define one repetition of the source.
        `clip`, when given, is a `Rect` or at least three finite `(x, y)`
        points around a convex screen-space perimeter. The texture is
        projectively extrapolated and tiled through that polygon. Omitting it
        uses `corners` as the clip, so the ordinary finite case stays simple.
        Either winding is accepted. Geometric validation and viewport culling
        happen in one batch immediately before GPU submission; malformed or
        bad geometry is recorded with the command and discarded there while
        survivors retain painter order. `add()` returns no handle: rebuild a
        changing view with `clear()` then ordered adds.
        """
        row = self._atlas._row(texture)
        source_rect = _recorded_source(source)
        points = _recorded_corners(corners)
        clip_points = None if clip is None else _recorded_clip(clip)
        if self._count == self._capacity:
            self._grow()
        if clip_points is not None:
            required = self._clip_count + len(clip_points)
            if required > self._clip_capacity:
                self._grow_clips(required)
        self._corners[self._count] = points
        self._rows[self._count] = row
        self._source_rects[self._count] = source_rect
        if clip_points is None:
            self._clip_spans[self._count] = (0, 0)
        else:
            start = self._clip_count
            stop = start + len(clip_points)
            self._clip_points[start:stop] = clip_points
            self._clip_spans[self._count] = (start, len(clip_points))
            self._clip_count = stop
        self._has_crops |= source is not None
        self._count += 1
        self._dirty = True

    def extend(
        self,
        count: int,
        *,
        clip_counts: Optional[Sequence[int] | np.ndarray] = None,
    ) -> QuadCommands:
        """Append a contiguous bulk block and return writable NumPy views.

        ``clip_counts`` gives the packed clip size for each command; zero uses
        that command's mapping corners as coverage. New texture ids, corners,
        and custom clip points begin invalid, so anything the caller leaves
        unwritten is simply removed by the batch validation at commit. Whole
        textures are the default; write ``source_rects`` for pixel crops.
        """
        try:
            count = index(count)
        except TypeError as error:
            raise TypeError("quad extend count must be an integer") from error
        if count < 0:
            raise ValueError(f"cannot extend by {count} quad commands")

        if clip_counts is None:
            counts = np.zeros(count, dtype=np.uint32)
        else:
            raw = np.asarray(clip_counts)
            if raw.shape != (count,) or not np.issubdtype(raw.dtype, np.integer):
                raise ValueError(
                    f"clip_counts must be {count} non-negative integers"
                )
            if np.any(raw < 0):
                raise ValueError(
                    f"clip_counts must be {count} non-negative integers"
                )
            counts = raw.astype(np.uint32)
        clip_total = int(counts.sum(dtype=np.uint64))

        required = self._count + count
        while required > self._capacity:
            self._grow()
        clip_required = self._clip_count + clip_total
        if clip_required > self._clip_capacity:
            self._grow_clips(clip_required)

        start = self._count
        stop = start + count
        clip_start = self._clip_count
        self._rows[start:stop] = -1
        self._corners[start:stop] = np.nan
        self._source_rects[start:stop] = _FULL_SOURCE_RECT
        if count:
            offsets = np.empty(count, dtype=np.uint64)
            offsets[0] = clip_start
            if count > 1:
                np.cumsum(counts[:-1], dtype=np.uint64, out=offsets[1:])
                offsets[1:] += clip_start
            self._clip_spans[start:stop, 0] = np.where(counts, offsets, 0)
            self._clip_spans[start:stop, 1] = counts
        if clip_total:
            self._clip_points[clip_start:clip_required] = np.nan

        self._count = stop
        self._clip_count = clip_required
        self._dirty = True
        return QuadCommands(self, start, count, clip_start, clip_total)

    def clear(self) -> None:
        """Rewind both live cursors, retaining the Atlas and allocations."""
        self._count = 0
        self._clip_count = 0
        self._vertex_count = 0
        self._index_count = 0
        self._has_crops = False
        self._dirty = False
        self._generation += 1

    def _sync(self) -> None:
        # Touching the texture uploads edited pixels even when commands did not
        # change; its GL name stays stable and no command needs rebuilding.
        _ = self._atlas.buffer.texture
        if self._count == 0:
            return
        if not self._dirty:
            return
        self._compact()
        if self._count == 0:
            self._vertex_count = 0
            self._index_count = 0
            self._dirty = False
            return
        np.take(
            self._atlas.uvs,
            self._rows[: self._count],
            axis=0,
            out=self._uvs[: self._count],
        )
        if self._has_crops:
            live = self._uvs[: self._count]
            sources = self._sources[: self._count]
            left = live[:, 0, 0].copy()
            right = live[:, 1, 0].copy()
            top = live[:, 0, 1].copy()
            bottom = live[:, 3, 1].copy()
            live[:, :, 0] = left[:, None] + sources[:, :, 0] * (
                right - left
            )[:, None]
            live[:, :, 1] = top[:, None] + sources[:, :, 1] * (
                bottom - top
            )[:, None]

        self._mappings[: self._count] = _inverse_homographies(
            self._corners[: self._count]
        )

        clip_counts = self._clip_spans[: self._count, 1]
        vertex_count = int(
            np.where(clip_counts == 0, 4, clip_counts).sum(dtype=np.uint64)
        )
        if vertex_count > self._vertex_capacity:
            self._grow_vertices(vertex_count)
        if self._batch is None:
            self._batch = QuadBatch(
                shader.program(self._atlas.buffer.mode == "P"),
                self._vertex_capacity,
            )
        elif self._batch.capacity < self._vertex_capacity:
            self._batch.resize(self._vertex_capacity)

        if not np.any(clip_counts):
            index_at = self._sync_unclipped_geometry()
        else:
            index_at = self._sync_clipped_geometry()

        self._vertex_count = vertex_count
        self._index_count = index_at
        self._batch.upload(
            self._positions[:vertex_count],
            self._projective[:vertex_count],
            self._regions[:vertex_count],
            self._indices[:index_at],
        )
        self._dirty = False

    def _compact(self) -> int:
        """Batch-cull bad geometry and compact survivors in painter order."""
        before = self._count
        self._count, self._clip_count = compact_commands(
            self._corners,
            self._rows,
            self._source_rects,
            self._sources,
            self._atlas._rects,
            self._clip_spans,
            self._clip_points,
            self._count,
            len(self._atlas),
            640.0,
            360.0,
        )
        self._has_crops = bool(
            np.any(self._sources[: self._count] != _FULL_SOURCE)
        )
        culled = before - self._count
        if culled:
            self._generation += 1
        return culled

    def _sync_unclipped_geometry(self) -> int:
        """Fill the four-vertex common case without a command-wise loop."""
        count = self._count
        vertex_count = count * 4
        positions = self._positions[:vertex_count].reshape(count, 4, 3)
        positions[:, :, :2] = self._corners[:count]

        x = positions[:, :, 0]
        y = positions[:, :, 1]
        projective = self._projective[:vertex_count].reshape(count, 4, 3)
        mapping = self._mappings[:count]
        projective[:, :, 0] = (
            mapping[:, None, 0, 0] * x
            + mapping[:, None, 0, 1] * y
            + mapping[:, None, 0, 2]
        )
        projective[:, :, 1] = (
            mapping[:, None, 1, 0] * x
            + mapping[:, None, 1, 1] * y
            + mapping[:, None, 1, 2]
        )
        projective[:, :, 2] = (
            mapping[:, None, 2, 0] * x
            + mapping[:, None, 2, 1] * y
            + mapping[:, None, 2, 2]
        )

        uv = self._uvs[:count]
        regions = self._regions[:vertex_count].reshape(count, 4, 4)
        regions[:, :, 0] = uv[:, None, 0, 0]
        regions[:, :, 1] = uv[:, None, 0, 1]
        regions[:, :, 2] = uv[:, None, 1, 0] - uv[:, None, 0, 0]
        regions[:, :, 3] = uv[:, None, 3, 1] - uv[:, None, 0, 1]

        indices = self._indices[: count * 6].reshape(count, 6)
        bases = np.arange(count, dtype=np.uint32) * 4
        indices[:] = bases[:, None] + (0, 1, 2, 0, 2, 3)
        return count * 6

    def _sync_clipped_geometry(self) -> int:
        """Compile arbitrary convex clips, then fill attributes in bulk."""
        vertex, index_at = expand_geometry(
            self._corners[: self._count],
            self._clip_spans[: self._count],
            self._clip_points[: self._clip_count],
            self._positions,
            self._owners,
            self._indices,
        )

        owners = self._owners[:vertex]
        positions = self._positions[:vertex]
        x = positions[:, 0]
        y = positions[:, 1]
        mapping = self._mappings[owners]
        projective = self._projective[:vertex]
        projective[:, 0] = (
            mapping[:, 0, 0] * x
            + mapping[:, 0, 1] * y
            + mapping[:, 0, 2]
        )
        projective[:, 1] = (
            mapping[:, 1, 0] * x
            + mapping[:, 1, 1] * y
            + mapping[:, 1, 2]
        )
        projective[:, 2] = (
            mapping[:, 2, 0] * x
            + mapping[:, 2, 1] * y
            + mapping[:, 2, 2]
        )

        uv = self._uvs[owners]
        regions = self._regions[:vertex]
        regions[:, 0] = uv[:, 0, 0]
        regions[:, 1] = uv[:, 0, 1]
        regions[:, 2] = uv[:, 1, 0] - uv[:, 0, 0]
        regions[:, 3] = uv[:, 3, 1] - uv[:, 0, 1]
        return index_at

    def _composite(self) -> None:
        """Upload changed commands and issue one painter-ordered draw."""
        self._sync()
        if self._batch is None or self._count == 0:
            return
        program = self._batch.program
        program["sprite_texture"] = 0
        if self._atlas.buffer.mode == "P":
            program["palette_texture"] = 1
            table = self._palette.texture
            glActiveTexture(GL_TEXTURE1)
            glBindTexture(table.target, table.id)
            glActiveTexture(GL_TEXTURE0)
        self._batch.draw(
            self._atlas.buffer.texture, self._index_count, self._palette.active
        )

    def __repr__(self) -> str:
        return f"<Quads {self._count} commands [{self._tag}]>"


def _recorded_source(raw: Optional[Source]) -> tuple[float, ...] | np.ndarray:
    if raw is None:
        return _FULL_SOURCE_RECT
    try:
        source = np.asarray(raw, dtype=np.float64)
    except (TypeError, ValueError, OverflowError):
        return _BAD_SOURCE
    if source.shape != (4,):
        return _BAD_SOURCE
    return source


def _recorded_clip(raw: Clip) -> np.ndarray:
    if isinstance(raw, Rect):
        raw = (
            (raw.x, raw.y),
            (raw.x + raw.width, raw.y),
            (raw.x + raw.width, raw.y + raw.height),
            (raw.x, raw.y + raw.height),
        )
    try:
        points = np.asarray(tuple(raw), dtype=np.float32)
    except (TypeError, ValueError, OverflowError):
        return _BAD_CLIP
    if points.ndim != 2 or points.shape[0] < 3 or points.shape[1] != 2:
        return _BAD_CLIP
    return points


def _inverse_homography(
    points: tuple[Corner, Corner, Corner, Corner],
) -> np.ndarray:
    """Map screen `(x, y, 1)` to the defining quad's `(u*q, v*q, q)`."""
    return _inverse_homographies(np.asarray((points,), dtype=np.float64))[0]


def _inverse_homographies(points: np.ndarray) -> np.ndarray:
    """Solve screen-to-unit mappings for an `(n, 4, 2)` corner array."""
    x0, y0 = points[:, 0, 0], points[:, 0, 1]
    x1, y1 = points[:, 1, 0], points[:, 1, 1]
    x2, y2 = points[:, 2, 0], points[:, 2, 1]
    x3, y3 = points[:, 3, 0], points[:, 3, 1]
    dx1, dx2 = x1 - x2, x3 - x2
    dy1, dy2 = y1 - y2, y3 - y2
    dx3 = x0 - x1 + x2 - x3
    dy3 = y0 - y1 + y2 - y3
    divisor = dx1 * dy2 - dx2 * dy1
    projective = (dx3 != 0.0) | (dy3 != 0.0)
    g = np.zeros_like(dx3)
    h = np.zeros_like(dy3)
    g[projective] = (
        dx3[projective] * dy2[projective]
        - dx2[projective] * dy3[projective]
    ) / divisor[projective]
    h[projective] = (
        dx1[projective] * dy3[projective]
        - dx3[projective] * dy1[projective]
    ) / divisor[projective]

    a = x1 - x0 + g * x1
    b = x3 - x0 + h * x3
    c = x0
    d = y1 - y0 + g * y1
    e = y3 - y0 + h * y3
    f = y0

    aa = e - f * h
    bb = c * h - b
    cc = b * f - c * e
    dd = f * g - d
    ee = a - c * g
    ff = c * d - a * f
    gg = d * h - e * g
    hh = b * g - a * h
    ii = a * e - b * d
    determinant = a * aa + b * dd + c * gg
    matrices = np.stack(
        (
            np.stack((aa, bb, cc), axis=1),
            np.stack((dd, ee, ff), axis=1),
            np.stack((gg, hh, ii), axis=1),
        ),
        axis=1,
    )
    return matrices / determinant[:, None, None]


def _recorded_corners(raw: Sequence[Corner]) -> np.ndarray:
    try:
        points = np.asarray(raw, dtype=np.float32)
    except (TypeError, ValueError, OverflowError):
        return _BAD_POINTS
    if points.shape != (4, 2):
        return _BAD_POINTS
    return points
