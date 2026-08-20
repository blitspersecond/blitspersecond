from contextlib import contextmanager
from typing import Iterable, Iterator, Optional, Tuple, Union

import numpy as np

from blitspersecond.common import Location
from blitspersecond.graphics.common.orientation import FLIP_OF, ROTATION_OF


class SpritePool:
    """A dense, ordered set of compatible one-part sprite instances.

    The pool is the data-oriented path inside one sprite layer: instance
    transforms live in contiguous arrays and every member shares one material.
    Assembly choice is fixed per slot, but position, visibility, facing and
    quarter-turn rotation remain live. NumPy callers update thousands of
    instances inside one ``with pool.edit():`` block, which marks the owning
    engine dirty once on exit.
    """

    def __init__(
        self,
        engine,
        z: int,
        assemblies: Iterable[Union[str, int, Tuple[int, int]]],
    ) -> None:
        self._engine = engine
        self.z = int(z)
        # Grid addresses resolve here, so a pool over a grid sheet reads
        # `pool(range(4))` the way a single instance reads `sprite(0)`.
        names = tuple(engine.sheet.assembly_name(key) for key in assemblies)
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
            this_material = (
                tileset.buffer, tileset.palettes[tileset.default_palette]
            )
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
        # One orientation per slot, not a bool beside a turn count: there
        # are exactly eight, the variant tables below already have eight
        # slots, and storing the flags themselves means `pool.orientation`
        # can be a LIVE array like every other pool field rather than a
        # computed copy that silently swallows writes.
        self._orientation = np.zeros(count, dtype=np.uint8)
        self._color = (1.0, 1.0, 1.0, 1.0)

        # Per-assembly/per-orientation local rects and UVs are immutable. The
        # frame path only gathers them and broadcasts the current positions.
        self._rects = np.empty((len(unique_names), 8, 4), dtype=np.int32)
        self._uvs = None
        self._origin = Location(0, 0)
        self._build_rects()

    def _build_rects(self) -> None:
        """The eight oriented local rects per distinct assembly. Rebuilt when
        the origin moves, which is per unique assembly rather than per slot --
        a 25,000-bullet pool of two kinds rebuilds sixteen rows."""
        for index, (part, rect) in enumerate(self._parts):
            for orientation in range(8):
                self._rects[index, orientation] = (
                    self._engine._local_rect_origin(
                        bool(FLIP_OF[orientation]),
                        int(ROTATION_OF[orientation]),
                        part,
                        rect,
                        self._origin,
                    )
                )

    def _ensure_templates(self) -> None:
        if self._uvs is not None:
            return
        count = len(self._parts)
        self._uvs = np.empty((count, 8, 4, 3), dtype=np.float32)
        for index, (part, _rect) in enumerate(self._parts):
            for orientation in range(8):
                self._uvs[index, orientation] = self._engine._uv(
                    part.tileset,
                    part.tile,
                    bool(FLIP_OF[orientation]),
                    int(ROTATION_OF[orientation]),
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
    def origin(self) -> Location:
        """The draw point and the pivot, for every slot -- a Sprite's
        `origin`, pool-wide.

        Pool-wide rather than per slot because nothing varies it per
        particle, and because the cached rects it feeds are per distinct
        assembly, not per instance: moving it rebuilds a handful of rows
        rather than an array as long as the swarm.

        Bullets want their centre. A danmaku bullet's position IS its centre
        -- the hitbox is a small disc there, an oriented bullet turns about
        it, and a ring or spiral spawn is polar maths measured from it. An
        asset can bake that in (the bullets fixture carries `[-4, -4]` on 8x8
        tiles), but this lets the game say it about art that did not.
        """
        return self._origin

    @origin.setter
    def origin(self, value) -> None:
        origin = Location(int(value[0]), int(value[1]))
        if origin != self._origin:
            self._origin = origin
            self._build_rects()
            self._engine._touch()

    @property
    def centre(self) -> Location:
        """The middle of the pooled art -- the value to hand `origin`.

        Every slot shares one origin, so this only means something when the
        pooled assemblies share an extent; mixed sizes raise rather than pick
        a winner."""
        extents = {(rect.width, rect.height) for _part, rect in self._parts}
        if len(extents) != 1:
            raise ValueError(
                f"pooled assemblies have {len(extents)} different sizes "
                f"({sorted(extents)}) -- there is no single centre; set "
                "origin explicitly"
            )
        (width, height), = extents
        part = self._parts[0][0]
        return Location(
            part.offset.x + width // 2, part.offset.y + height // 2
        )

    @property
    def orientation(self) -> np.ndarray:
        """Writable uint8 ``(n,)`` orientations -- `Orientation` values, the
        same eight a tile placement or a lone Sprite uses.

            bullets.orientation[spinning] = Orientation.ROT_90
            bullets.orientation[homing] = Orientation.FLIP_H
        """
        return self._orientation

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

    def sprite(self, assembly: Optional[Union[str, int, Tuple[int, int]]] = None):
        from .sprite import Sprite

        return Sprite(self._engine, assembly, _z=self.order)

    def pool(
        self, assemblies: Iterable[Union[str, int, Tuple[int, int]]]
    ) -> SpritePool:
        pool = SpritePool(self._engine, self.order, assemblies)
        self._engine._adopt_pool(pool)
        return pool
