from contextlib import contextmanager
from typing import Iterable, Iterator, Optional, Tuple

import numpy as np


class SpritePool:
    """A dense, ordered set of compatible one-part sprite instances.

    The pool is the data-oriented path inside one sprite layer: instance
    transforms live in contiguous arrays and every member shares one material.
    Assembly choice is fixed per slot, but position, visibility, facing and
    quarter-turn rotation remain live. NumPy callers update thousands of
    instances inside one ``with pool.edit():`` block, which marks the owning
    engine dirty once on exit.
    """

    def __init__(self, engine, z: int, assemblies: Iterable[str]) -> None:
        self._engine = engine
        self.z = int(z)
        names = tuple(assemblies)
        if not names:
            raise ValueError("a sprite pool needs at least one instance")

        unique_names = tuple(dict.fromkeys(names))
        self._unique_assemblies = unique_names
        name_to_id = {name: index for index, name in enumerate(unique_names)}
        parts = []
        material = None
        for name in unique_names:
            assembly = engine.sheet.assemblies.get(name)
            if assembly is None:
                raise KeyError(f"unknown assembly {name!r}")
            if len(assembly.parts) != 1:
                raise ValueError(
                    f"pooled assembly {name!r} has {len(assembly.parts)} parts; "
                    "a pool instance must have exactly one"
                )
            part = assembly.parts[0]
            tileset = engine.sheet.tilesets[part.tileset]
            palette_index = (
                part.palette
                if part.palette is not None
                else tileset.default_palette
            )
            this_material = (tileset.buffer, tileset.palettes[palette_index])
            if material is None:
                material = this_material
            elif (
                this_material[0] is not material[0]
                or this_material[1] is not material[1]
            ):
                raise ValueError(
                    "all assemblies in a sprite pool must share one texture "
                    "and palette"
                )
            parts.append((part, tileset.tiles[part.tile]))

        self._assemblies = names
        self._assembly_ids = np.fromiter(
            (name_to_id[name] for name in names), dtype=np.intp, count=len(names)
        )
        self._parts = tuple(parts)
        assert material is not None  # unique_names is non-empty, so the loop ran
        self._buffer, self._palette = material
        count = len(names)
        self._positions = np.zeros((count, 2), dtype=np.float32)
        self._visible = np.ones(count, dtype=bool)
        self._collide = np.ones(count, dtype=bool)
        self._flip_x = np.zeros(count, dtype=bool)
        self._rotation = np.zeros(count, dtype=np.uint8)
        self._color = (1.0, 1.0, 1.0, 1.0)

        # Per-assembly/per-orientation local rects and UVs are immutable. The
        # frame path only gathers them and broadcasts the current positions.
        self._rects = np.empty((len(unique_names), 8, 4), dtype=np.int32)
        self._uvs = None
        for index, (part, rect) in enumerate(self._parts):
            for flip in (0, 1):
                for rotation in range(4):
                    variant = flip * 4 + rotation
                    self._rects[index, variant] = engine._local_rect_origin(
                        bool(flip), rotation, part, rect
                    )

    def _ensure_templates(self) -> None:
        if self._uvs is not None:
            return
        count = len(self._parts)
        self._uvs = np.empty((count, 2, 4, 4, 3), dtype=np.float32)
        for index, (part, _rect) in enumerate(self._parts):
            for flip in (0, 1):
                for rotation in range(4):
                    self._uvs[index, flip, rotation] = self._engine._uv(
                        part.tileset, part.tile, bool(flip), rotation
                    )

    def __len__(self) -> int:
        return len(self._assemblies)

    @contextmanager
    def edit(self) -> Iterator["SpritePool"]:
        """Mutate array state and mark the renderer dirty once on exit."""
        try:
            yield self
        finally:
            self._engine._touch()

    @property
    def positions(self) -> np.ndarray:
        """Writable float32 ``(n, 2)`` screen-space origins."""
        return self._positions

    @property
    def visible(self) -> np.ndarray:
        """Writable bool ``(n,)`` display flags."""
        return self._visible

    @property
    def collide(self) -> np.ndarray:
        """Writable bool ``(n,)`` collision participation flags."""
        return self._collide

    @property
    def flip_x(self) -> np.ndarray:
        """Writable bool ``(n,)`` horizontal-facing flags."""
        return self._flip_x

    @property
    def rotation(self) -> np.ndarray:
        """Writable uint8 ``(n,)`` clockwise quarter turns."""
        return self._rotation

    @property
    def color(self) -> Tuple[float, float, float, float]:
        return self._color

    @color.setter
    def color(self, value) -> None:
        if len(value) != 4:
            raise ValueError("color must contain red, green, blue and alpha")
        r, g, b, a = (min(max(float(channel), 0.0), 1.0) for channel in value)
        self._color = (r, g, b, a)
        self._engine._touch()

    @property
    def count_visible(self) -> int:
        return int(np.count_nonzero(self._visible))


class SpritePlane:
    """One integer painter plane inside a SpriteEngine."""

    def __init__(self, engine, order: int) -> None:
        self._engine = engine
        self.order = int(order)

    def sprite(self, assembly: Optional[str] = None):
        from .sprite import Sprite

        return Sprite(self._engine, assembly, _z=self.order)

    def pool(self, assemblies: Iterable[str]) -> SpritePool:
        pool = SpritePool(self._engine, self.order, assemblies)
        self._engine._adopt_pool(pool)
        return pool
