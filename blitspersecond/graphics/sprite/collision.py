"""Retained packed screen-space collision masks for SpriteEngine."""

from typing import Dict, Optional, Tuple

import numpy as np

from blitspersecond.graphics.common import (
    CLOW,
    CWORD,
    CWORD_BITS,
    CWORD_SHIFT,
)


def _packed_pattern(bits: np.ndarray) -> np.ndarray:
    """A bool image as uint64 row patterns, padded to collision words."""
    height, width = bits.shape
    words = -(-width // CWORD_BITS)
    block = np.zeros((height, words * CWORD_BITS), dtype=bool)
    block[:, :width] = bits
    packed = np.packbits(block, axis=1, bitorder="little")
    return packed.view(CWORD).astype(np.uint64)


class SpriteCollision:
    """All retained packed planes belonging to one SpriteEngine.

    The exported query surface is exactly 640x360. Internally each plane has
    an apron large enough for every assembly-local source: stamps straddling
    the screen therefore need no row-by-row clipping. Cached uint64 row
    patterns scatter through permanent work arrays, the same proven regime
    used by TileEngine's moving-particle restamp.
    """

    def __init__(self, engine) -> None:
        self._engine = engine
        self._masks: Optional[Dict[Optional[str], np.ndarray]] = None
        self._capron: Tuple[int, int] = (0, 0)  # rows, words
        self._cdims: Tuple[int, int, int] = (0, 0, 0)  # h, w, words
        self._patterns: Dict[Tuple[str, str, bool, int], np.ndarray] = {}
        self._pattern_sources: Dict[str, np.ndarray] = {}
        self._solid: Dict[Tuple[int, int], np.ndarray] = {}
        self._selected: Dict[Tuple[str, ...], np.ndarray] = {}
        self._work: Optional[Tuple[np.ndarray, ...]] = None

    def _apron(self) -> Tuple[int, int]:
        """Maximum oriented source extent across pixels and named boxes.

        Offsets have already been folded into each stamp's final top-left, so
        the apron only has to absorb a source straddling a screen edge.
        """
        extent = 1
        for assembly in self._engine.sheet.assemblies.values():
            for part in assembly.parts:
                tileset = self._engine.sheet.tilesets[part.tileset]
                source = tileset.tiles[part.tile]
                extent = max(extent, source.width, source.height)
            for boxes in assembly.collision.values():
                for rect in boxes:
                    extent = max(extent, rect.width, rect.height)
        # One extra word absorbs an unaligned pattern's high carry.
        return extent + 1, -(-extent // CWORD_BITS) + 1

    def _ensure(self) -> Dict[Optional[str], np.ndarray]:
        if self._masks is None:
            from blitspersecond.system.config import Config

            display = Config().display
            height, width = display.height, display.width
            words = -(-width // CWORD_BITS)
            ah, aw = self._apron()
            shape = (height + 2 * ah, words + 2 * aw)
            self._masks = {
                None: np.zeros(shape, dtype=CWORD),
                **{
                    name: np.zeros(shape, dtype=CWORD)
                    for name in self._engine.sheet.planes
                },
            }
            self._capron = (ah, aw)
            self._cdims = (height, width, words)
        return self._masks

    def _view(self, mask: np.ndarray) -> np.ndarray:
        height, _width, words = self._cdims
        ah, aw = self._capron
        return mask[ah : ah + height, aw : aw + words]

    def _pattern(self, tileset_name, tile_name, flip, rotation):
        key = (tileset_name, tile_name, bool(flip), int(rotation) & 3)
        pattern = self._patterns.get(key)
        tileset = self._engine.sheet.tilesets[tileset_name]
        source = tileset.buffer.mask
        if (
            pattern is not None
            and self._pattern_sources.get(tileset_name) is source
        ):
            return pattern
        if self._pattern_sources.get(tileset_name) is not source:
            self._patterns = {
                cached_key: cached
                for cached_key, cached in self._patterns.items()
                if cached_key[0] != tileset_name
            }
            self._pattern_sources[tileset_name] = source
        rect = tileset.tiles[tile_name]
        bits = source[
            rect.y : rect.y + rect.height,
            rect.x : rect.x + rect.width,
        ]
        if flip:
            bits = bits[:, ::-1]
        if rotation:
            bits = np.rot90(bits, -rotation)
        pattern = _packed_pattern(bits)
        self._patterns[key] = pattern
        return pattern

    def _solid_pattern(self, width: int, height: int) -> np.ndarray:
        key = (int(width), int(height))
        pattern = self._solid.get(key)
        if pattern is None:
            pattern = _packed_pattern(np.ones((height, width), dtype=bool))
            self._solid[key] = pattern
        return pattern

    def _ensure_work(self, count: int) -> Tuple[np.ndarray, ...]:
        if self._work is None or len(self._work[0]) < count:
            capacity = max(8, count)
            if self._work is not None:
                capacity = max(capacity, 2 * len(self._work[0]))
            self._work = (
                np.empty(capacity, dtype=np.uint64),  # shifted pattern
                np.empty(capacity, dtype=np.uint64),  # lo/hi extraction
                np.empty(capacity, dtype=CWORD),      # scatter values
                np.empty(capacity, dtype=np.intp),    # flat indices
            )
        return self._work

    def _stamp(
        self,
        target: np.ndarray,
        xs: np.ndarray,
        ys: np.ndarray,
        pattern: np.ndarray,
    ) -> None:
        """OR one cached row pattern at many positions without allocation."""
        count = len(xs)
        if not count:
            return
        height, width, _words = self._cdims
        ah, aw = self._capron
        rows, stride = target.shape
        xs = np.asarray(xs, dtype=np.int32)
        ys = np.asarray(ys, dtype=np.int32)
        pattern_height, pattern_words = pattern.shape
        xwords = (xs >> CWORD_SHIFT) + aw
        valid = (
            (ys + ah >= 0)
            & (ys + ah + pattern_height <= rows)
            & (xwords >= 0)
            & (xwords + pattern_words + 1 <= stride)
            # Ignore remote instances that cannot intersect the retained
            # screen/apron even if their local source would technically fit.
            & (xs < width + aw * CWORD_BITS)
            & (ys < height + ah)
            & (xs + pattern_words * CWORD_BITS > -(aw * CWORD_BITS))
            & (ys + pattern_height > -ah)
        )
        selected = np.flatnonzero(valid)
        count = len(selected)
        if not count:
            return
        xs = xs[selected]
        ys = ys[selected]
        xwords = xwords[selected]
        offsets = (xs & (CWORD_BITS - 1)).astype(np.uint64)
        base = (ys + ah).astype(np.intp) * stride + xwords
        shifted, extracted, values, indices = (
            work[:count] for work in self._ensure_work(count)
        )
        flat = target.reshape(-1)

        for dy in range(pattern_height):
            for word_column in range(pattern_words):
                source = pattern[dy, word_column]
                if source == 0:
                    continue
                np.left_shift(source, offsets, out=shifted)
                np.bitwise_and(shifted, CLOW, out=extracted)
                values[:] = extracted
                np.add(base, dy * stride + word_column, out=indices)
                np.bitwise_or.at(flat, indices, values)
                np.right_shift(
                    shifted, np.uint64(CWORD_BITS), out=extracted
                )
                values[:] = extracted
                np.add(indices, 1, out=indices)
                np.bitwise_or.at(flat, indices, values)

    def _stamp_one(
        self, target: np.ndarray, x: int, y: int, pattern: np.ndarray
    ) -> None:
        """OR one pattern as two rectangular word-slice strokes.

        Rich sprites have only a handful of parts/boxes. Routing those through
        the many-placement scatter path costs more setup than collision work.
        """
        height, width, _words = self._cdims
        ah, aw = self._capron
        pattern_height, pattern_words = pattern.shape
        row = int(y) + ah
        word = (int(x) >> CWORD_SHIFT) + aw
        if (
            row < 0
            or row + pattern_height > target.shape[0]
            or word < 0
            or word + pattern_words + 1 > target.shape[1]
            or x >= width + aw * CWORD_BITS
            or y >= height + ah
            or x + pattern_words * CWORD_BITS <= -(aw * CWORD_BITS)
            or y + pattern_height <= -ah
        ):
            return
        shifted = pattern << np.uint64(int(x) & (CWORD_BITS - 1))
        target[
            row : row + pattern_height,
            word : word + pattern_words,
        ] |= (shifted & CLOW).astype(CWORD)
        target[
            row : row + pattern_height,
            word + 1 : word + pattern_words + 1,
        ] |= (shifted >> np.uint64(CWORD_BITS)).astype(CWORD)

    def _stamp_pose(
        self, assembly, px, py, flip, rotation, masks
    ) -> None:
        from .sprite_engine import _local_rect

        for part in assembly.parts:
            tileset = self._engine.sheet.tilesets[part.tileset]
            rect = tileset.tiles[part.tile]
            x, y, _width, _height = _local_rect(
                px, py, flip, rotation,
                part.offset.x, part.offset.y, rect.width, rect.height,
            )
            self._stamp_one(
                masks[None],
                x,
                y,
                self._pattern(part.tileset, part.tile, flip, rotation),
            )
        for name, rects in assembly.collision.items():
            target = masks[name]
            for rect in rects:
                x, y, width, height = _local_rect(
                    px, py, flip, rotation,
                    rect.x, rect.y, rect.width, rect.height,
                )
                self._stamp_one(
                    target,
                    x,
                    y,
                    self._solid_pattern(width, height),
                )

    def _stamp_pool(self, pool, masks) -> None:
        from .sprite_engine import _local_rect

        active = np.flatnonzero(pool._visible & pool._collide)
        if not len(active):
            return
        assembly_ids = pool._assembly_ids[active]
        flips = pool._flip_x[active].astype(np.intp)
        rotations = (pool._rotation[active] & 3).astype(np.intp)
        variants = flips * 4 + rotations
        positions = np.floor(pool._positions[active]).astype(np.int32)
        keys = assembly_ids * 8 + variants

        for key in np.unique(keys):
            selected = np.flatnonzero(keys == key)
            assembly_id = int(key) // 8
            variant = int(key) & 7
            flip, rotation = bool(variant >> 2), variant & 3
            part, _source_rect = pool._parts[assembly_id]
            local = pool._rects[assembly_id, variant]
            xs = positions[selected, 0] + local[0]
            ys = positions[selected, 1] + local[1]
            self._stamp(
                masks[None],
                xs,
                ys,
                self._pattern(part.tileset, part.tile, flip, rotation),
            )

            assembly_name = pool._unique_assemblies[assembly_id]
            assembly = self._engine.sheet.assemblies[assembly_name]
            for name, rects in assembly.collision.items():
                target = masks[name]
                for rect in rects:
                    x, y, width, height = _local_rect(
                        0, 0, flip, rotation,
                        rect.x, rect.y, rect.width, rect.height,
                    )
                    self._stamp(
                        target,
                        positions[selected, 0] + x,
                        positions[selected, 1] + y,
                        self._solid_pattern(width, height),
                    )

    def rebuild(self) -> None:
        from .sprite_pool import SpritePool

        masks = self._ensure()
        for mask in masks.values():
            mask.fill(0)
        self._selected.clear()
        for entry in self._engine._entries():
            if isinstance(entry, SpritePool):
                self._stamp_pool(entry, masks)
            elif (
                entry._visible
                and entry._collide
                and entry._assembly is not None
            ):
                self._stamp_pose(
                    entry._assembly,
                    int(entry._xy[0]),
                    int(entry._xy[1]),
                    entry._flip_x,
                    entry._rotation,
                    masks,
                )
        self._engine._collision_dirty = False

    def mask(self, planes: Optional[Tuple[str, ...]] = None) -> np.ndarray:
        if not self._engine._collision_dirty:
            for name, source in tuple(self._pattern_sources.items()):
                if self._engine.sheet.tilesets[name].buffer.mask is not source:
                    self._engine._collision_dirty = True
                    break
        if self._engine._collision_dirty:
            self.rebuild()
        masks = self._ensure()
        if planes is None:
            return self._view(masks[None])
        if len(planes) == 1:
            return self._view(masks[planes[0]])
        key = tuple(planes)
        selected = self._selected.get(key)
        if selected is None:
            selected = np.zeros_like(masks[None])
            for name in key:
                selected |= masks[name]
            self._selected[key] = selected
        return self._view(selected)

    def unpacked(
        self, planes: Optional[Tuple[str, ...]] = None
    ) -> np.ndarray:
        packed = np.ascontiguousarray(self.mask(planes))
        _height, width, _words = self._cdims
        return np.unpackbits(
            packed.view(np.uint8),
            axis=1,
            bitorder="little",
            count=width,
        ).view(np.bool_)
