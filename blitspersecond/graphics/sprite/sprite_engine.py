from typing import Dict, List, Optional, Tuple

import numpy as np
from pyglet.gl import (
    GL_BLEND,
    GL_FLOAT,
    GL_ONE_MINUS_SRC_ALPHA,
    GL_SRC_ALPHA,
    GL_TEXTURE0,
    GL_TEXTURE1,
    GL_TRIANGLES,
    GL_UNSIGNED_INT,
    glActiveTexture,
    glBindTexture,
    glBlendFunc,
    glDisable,
    glDrawElements,
    glEnable,
    glEnableVertexAttribArray,
    glVertexAttrib3f,
    glVertexAttrib4f,
    glVertexAttribPointer,
)
from pyglet.graphics.vertexarray import VertexArray
from pyglet.graphics.vertexbuffer import BufferObject
from pyglet.image import Texture

from typing import Union

from blitspersecond.common import Size
from blitspersecond.display.layer import Layer
from blitspersecond.graphics.common import (
    Collidable,
    CollisionQuery,
    PixelBuffer,
    PlaneCollidable,
)
from blitspersecond.graphics.internal import PaletteTexture, Shader
from blitspersecond.resources import Palette

from .collision import SpriteCollision
from .sprite import Sprite
from .sprite_pool import SpritePlane, SpritePool
from .sprite_sheet import SpriteSheet


def _local_rect(
    px: int, py: int, flip: bool, rot: int, x: int, y: int, w: int, h: int
) -> Tuple[int, int, int, int]:
    """A local rect placed on screen: the ONE orientation formula, shared by
    draw staging and collision stamping so the two can never drift. Facing
    mirrors the local frame about the anchor's vertical axis (local [x, x+w)
    lands at [-x-w, -x)); rotation then turns the mirrored frame k quarter-
    turns clockwise about the anchor -- the same H-then-D composition the
    tile flip bits speak. Returns (screen_x, screen_y, w, h); odd turns
    swap the extents."""
    if flip:
        x = -x - w
    for _ in range(rot & 3):
        x, y, w, h = -y - h, x, h, w
    return px + x, py + y, w, h


# Dest corners are (TL, TR, BR, BL); each clockwise quarter-turn of the
# pixels means the source corner feeding dest TL walks backwards around the
# ring. Applied after the flip swizzle, matching _local_rect's H-then-D order.
_ROT_UV = (
    np.array([0, 1, 2, 3]),
    np.array([3, 0, 1, 2]),
    np.array([2, 3, 0, 1]),
    np.array([1, 2, 3, 0]),
)

# One draw call: `count` quads starting at quad `first`, all sharing one
# material -- the texture to bind, the palette table to upload, the colour
# registers to ride the generic attribute. The run list IS the frame plan.
_Run = Tuple[PixelBuffer, Palette, Tuple[float, float, float, float], int, int]


class SpriteLayer(Layer):
    """The sprite layer: independently movable, named instances over one
    SpriteSheet -- distinct from graphics.tile's finite environmental map,
    drawing through the same one-object-IS-the-layer path.

    Produced by the SpriteEngine factory (the mainline); constructing
    SpriteLayer directly builds the same object *unattached*. One sheet per
    layer; the layer is the collision identity and the depth position (a
    mirror match is two SpriteLayers binding the SAME sheet -- shared
    pixels, one texture upload, separate identities; depth between sprites
    is layer order). Instances are cheap; identities cost a layer.

    The interior job: walk integer painter planes low-to-high, walk each
    rich assembly's ordered parts or vector-project each dense one-part
    pool, and coalesce adjacent quads sharing a
    (buffer, palette, registers) material into runs -- strictly
    order-preserving, no reordering across a material boundary. Each run is
    one texture bind + palette upload + glDrawElements. Mid-draw state
    switches are the point here (TileEngine spends its single bind on a
    uniform-grid map; this engine spends binds on ordered composition),
    and the cost driver is material-transition count, not instance count.
    One-part poses under one palette coalesce into a single run.

    Dense compatible swarms use SpritePool's NumPy SoA storage; rich
    multipart composition retains the scalar Sprite path. The staging arrays
    and GL buffers are retained and capacity-doubled, so a steady scene
    allocates nothing per frame; a dirty frame rewrites [:nquads] in place
    and re-uploads once.

    The engine never reads the sheet's animation data: a sprite presents
    whatever assembly game code (or a future Animation instance) selected.
    """

    def __init__(self, sheet: SpriteSheet) -> None:
        if not isinstance(sheet, SpriteSheet):
            raise TypeError(f"expected SpriteSheet, got {type(sheet)}")
        self._sheet = sheet
        # The first tileset is the engine's face for layer-surface metadata (the
        # compositor's neighbours want *a* buffer to ask about; multi-buffer
        # composition is this engine's interior business).
        self._primary = next(iter(sheet.tilesets.values()))
        self._sprites: List[Sprite] = []
        self._planes: Dict[int, List[object]] = {}
        self._sprite_z: Dict[Sprite, int] = {}
        self._plane_faces: Dict[int, SpritePlane] = {}
        # GL side, built lazily on first prepare()/_composite().
        self._shader: Optional[Shader] = None
        self._vao: Optional[VertexArray] = None
        self._positions: Optional[BufferObject] = None
        self._texcoords: Optional[BufferObject] = None
        self._indices: Optional[BufferObject] = None
        self._loc_colors: int = -1
        self._loc_translate: int = -1
        # Retained staging: (cap, 4, 3) float32 corner arrays, rewritten in
        # place on dirty frames; capacity doubles, never shrinks.
        self._capacity = 0
        self._verts = np.zeros((0, 4, 3), dtype=np.float32)
        self._uvs = np.zeros((0, 4, 3), dtype=np.float32)
        self._nquads = 0
        self._runs: List[_Run] = []
        # (tileset, tile, flip, rot) -> (4, 3) UV corners. Source rects are
        # static per sheet, so each variant is computed once, ever.
        self._uv_cache: Dict[Tuple[str, str, bool, int], np.ndarray] = {}
        # Set by every Sprite setter (they hold the engine); cleared by the
        # rebuild. Fighters move every frame, so most frames are dirty --
        # the flag exists so a paused scene costs nothing.
        self._dirty = True
        self._collision_dirty = True
        self._collision = SpriteCollision(self)
        # IS-A Layer: content and place are one object. See TileLayer.
        super().__init__()

    @property
    def sheet(self) -> SpriteSheet:
        return self._sheet

    # -- instances ---------------------------------------------------------

    def sprite(self, assembly: Optional[str] = None) -> Sprite:
        """Construct an instance against this engine (the mainline):
        `chun = engine.sprite("stance-01")`. Draw order within the layer is
        insertion order; a tail's segments or a trail's ghosts are simply
        more instances."""
        return Sprite(self, assembly)

    def _touch(self) -> None:
        """Presentation changed: both GPU staging and collision composition
        need rebuilding before their next consumer."""
        self._dirty = True
        self._collision_dirty = True

    def z(self, order: int) -> SpritePlane:
        """Address one integer painter plane inside this sprite layer.

        Planes draw low-to-high.  Each plane may contain rich Sprite objects
        and/or dense compatible pools, preserving insertion order within the
        plane.
        """
        order = int(order)
        face = self._plane_faces.get(order)
        if face is None:
            face = self._plane_faces[order] = SpritePlane(self, order)
        return face

    def _adopt(self, sprite: Sprite, z: int = 0) -> None:
        """Register a freshly constructed instance (Sprite.__init__ calls
        this, so direct `Sprite(engine)` construction registers too)."""
        self._sprites.append(sprite)
        z = int(z)
        self._planes.setdefault(z, []).append(sprite)
        self._sprite_z[sprite] = z
        self._touch()

    def _adopt_pool(self, pool: SpritePool) -> None:
        self._planes.setdefault(pool.z, []).append(pool)
        self._touch()

    def remove(self, sprite: Sprite) -> None:
        """Withdraw an instance. Not a frame-path operation -- effect
        lifetimes that toggle per tick should use .visible instead."""
        self._sprites.remove(sprite)
        z = self._sprite_z.pop(sprite)
        self._planes[z].remove(sprite)
        if not self._planes[z]:
            del self._planes[z]
        self._touch()

    @property
    def sprites(self) -> Tuple[Sprite, ...]:
        return tuple(
            entry
            for z in sorted(self._planes)
            for entry in self._planes[z]
            if isinstance(entry, Sprite)
        )

    def _entries(self):
        for z in sorted(self._planes):
            yield from self._planes[z]

    # -- GL build ----------------------------------------------------------

    def _ensure_built(self) -> None:
        """Fetch the shared program on first use (needs a current GL
        context). The picture pair is exactly this engine's shape: real
        position/UV streams, colors/translate as generics, the palette as
        the per-draw uniform."""
        if self._shader is not None:
            return
        self._shader = Shader.get("picture", "indexed")
        attrs = self._shader.program.attributes
        self._loc_colors = attrs["colors"]["location"]
        self._loc_translate = attrs["translate"]["location"]

    def _ensure_capacity(self, n: int) -> None:
        """Grow staging + GL buffers to hold >= n quads (doubling), and
        rebuild the VAO over the new buffers. Never in a steady frame."""
        if n <= self._capacity:
            return
        cap = max(8, self._capacity * 2, n)
        verts = np.zeros((cap, 4, 3), dtype=np.float32)
        verts[: self._capacity] = self._verts
        self._verts = verts
        uvs = np.zeros((cap, 4, 3), dtype=np.float32)
        uvs[: self._capacity] = self._uvs
        self._uvs = uvs
        self._capacity = cap
        # Static index pattern: two triangles per quad, for the whole
        # capacity -- runs draw contiguous slices of it by byte offset.
        # (Only ever reached from _rebuild, so the shader exists and a GL
        # context is current.)
        idx = (
            np.tile(np.array([0, 1, 2, 0, 2, 3], dtype=np.uint32), cap)
            + np.repeat(np.arange(cap, dtype=np.uint32) * 4, 6)
        )
        self._positions = BufferObject(self._verts.nbytes)
        self._texcoords = BufferObject(self._uvs.nbytes)
        self._indices = BufferObject(idx.nbytes)
        self._indices.set_data_region(idx.ctypes.data, 0, idx.nbytes)

        attrs = self._shader.program.attributes
        self._vao = VertexArray()
        self._vao.bind()
        self._positions.bind()
        glEnableVertexAttribArray(attrs["position"]["location"])
        glVertexAttribPointer(attrs["position"]["location"], 3, GL_FLOAT, False, 0, 0)
        self._texcoords.bind()
        glEnableVertexAttribArray(attrs["tex_coords"]["location"])
        glVertexAttribPointer(attrs["tex_coords"]["location"], 3, GL_FLOAT, False, 0, 0)
        self._indices.bind_to_index_buffer()
        self._vao.unbind()

    def _uv(
        self, tileset_name: str, tile_name: str, flip: bool, rot: int
    ) -> np.ndarray:
        """UV corners for one tile, computed once per (tile, flip, rot)
        ever. Corner order matches the dest quad below -- (x1,y1)(x2,y1)
        (x2,y2)(x1,y2); the flip variant swaps left/right corners so the
        pixels mirror while the dest rect stays put, and the rotation
        variant cycles the ring so the pixels turn inside the (extent-
        swapped) dest rect. Flip first, then rotate -- _local_rect's order."""
        key = (tileset_name, tile_name, flip, rot)
        cached = self._uv_cache.get(key)
        if cached is not None:
            return cached
        ts = self._sheet.tilesets[tileset_name]
        r = ts.tiles[tile_name]
        region = ts.buffer.texture.get_region(r.x, r.y, r.width, r.height)
        uv = np.asarray(region.tex_coords, dtype=np.float32).reshape(4, 3)
        if flip:
            uv = uv[[1, 0, 3, 2]]
        uv = np.ascontiguousarray(uv[_ROT_UV[rot]])
        self._uv_cache[key] = uv
        return uv

    @staticmethod
    def _local_rect_origin(flip, rotation, part, rect):
        return _local_rect(
            0, 0, flip, rotation,
            part.offset.x, part.offset.y, rect.width, rect.height,
        )

    # -- the frame plan ----------------------------------------------------

    def _rebuild(self) -> None:
        """Project instance state into staging + the run list: the ordered
        part walk with strictly order-preserving material coalescing."""
        total = 0
        for entry in self._entries():
            if isinstance(entry, SpritePool):
                total += entry.count_visible
            elif entry._visible and entry._assembly is not None:
                total += len(entry._assembly.parts)
        self._ensure_capacity(total)
        runs: List[_Run] = []
        key = None
        n = 0
        for s in self._entries():
            if isinstance(s, SpritePool):
                indices = np.flatnonzero(s._visible)
                count = len(indices)
                if not count:
                    continue
                s._ensure_templates()
                flip = s._flip_x[indices].astype(np.intp)
                rotation = (s._rotation[indices] & 3).astype(np.intp)
                variant = flip * 4 + rotation
                assembly_ids = s._assembly_ids[indices]
                rects = s._rects[assembly_ids, variant]
                xy = np.floor(s._positions[indices]).astype(np.int32)
                x0 = xy[:, 0] + rects[:, 0]
                y0 = xy[:, 1] + rects[:, 1]
                x1 = x0 + rects[:, 2]
                y1 = y0 + rects[:, 3]
                v = self._verts[n : n + count]
                v[:, 0, 0], v[:, 0, 1] = x0, y0
                v[:, 1, 0], v[:, 1, 1] = x1, y0
                v[:, 2, 0], v[:, 2, 1] = x1, y1
                v[:, 3, 0], v[:, 3, 1] = x0, y1
                self._uvs[n : n + count] = s._uvs[
                    assembly_ids, flip, rotation
                ]
                material = (s._buffer, s._palette, s._color)
                if material == key:
                    buffer, palette, col, first, old_count = runs[-1]
                    runs[-1] = (
                        buffer, palette, col, first, old_count + count
                    )
                else:
                    runs.append((*material, n, count))
                    key = material
                n += count
                continue
            if not s._visible or s._assembly is None:
                continue
            # Floored once per sprite, exactly as every engine floors --
            # whole-pixel crispness (collision stamping floors identically,
            # so draw and planes stay in lockstep).
            px, py = int(s._xy[0]), int(s._xy[1])
            flip = s._flip_x
            rot = s._rotation
            colors = (s._color[0], s._color[1], s._color[2], s._color[3])
            for part in s._assembly.parts:
                ts = self._sheet.tilesets[part.tileset]
                r = ts.tiles[part.tile]
                # The offset moves via the shared orientation formula; the
                # pixels follow via the flipped/rotated UV variant.
                dx, dy, dw, dh = _local_rect(
                    px, py, flip, rot,
                    part.offset.x, part.offset.y, r.width, r.height,
                )
                v = self._verts[n]
                v[0, 0] = dx
                v[0, 1] = dy
                v[1, 0] = dx + dw
                v[1, 1] = dy
                v[2, 0] = dx + dw
                v[2, 1] = dy + dh
                v[3, 0] = dx
                v[3, 1] = dy + dh
                self._uvs[n] = self._uv(part.tileset, part.tile, flip, rot)
                # The material: which texture, which palette table, which
                # colour registers. A part override outranks the instance
                # register (effects pinned while the character recolours).
                index = (
                    part.palette
                    if part.palette is not None
                    else s._palettes[part.tileset]
                )
                material = (ts.buffer, ts.palettes[index], colors)
                if material == key:
                    buffer, palette, col, first, count = runs[-1]
                    runs[-1] = (buffer, palette, col, first, count + 1)
                else:
                    runs.append((*material, n, 1))
                    key = material
                n += 1
        self._nquads = n
        self._runs = runs
        self._dirty = False
        if n and self._positions is not None:
            nbytes = n * 12 * 4  # quads x 4 corners x 3 floats x float32
            self._positions.set_data_region(self._verts.ctypes.data, 0, nbytes)
            self._texcoords.set_data_region(self._uvs.ctypes.data, 0, nbytes)

    def prepare(self) -> None:
        """Prepare both retained projections after presentation changes:
        restamp packed collision planes, then re-plan and upload draw quads.
        Queries may trigger collision composition earlier, but never repeat
        it while state remains unchanged."""
        self._ensure_built()
        for ts in self._sheet.tilesets.values():
            _ = ts.buffer.texture
        if self._collision_dirty:
            self._collision.rebuild()
        if self._dirty:
            self._rebuild()

    def _composite(self) -> None:
        """The frame plan, executed: program + blend once, then per run --
        texture bind and palette upload only when they actually change --
        the colour registers and one glDrawElements over the run's slice."""
        self.prepare()
        if not self._nquads:
            return
        assert self._shader is not None and self._vao is not None
        program = self._shader.program
        program.use()
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        # Positions are baked per frame, so the translate generic stays
        # zero; set per draw because generic attributes are context state
        # other engines' draws overwrite (as are the colour registers).
        glVertexAttrib3f(self._loc_translate, 0.0, 0.0, 0.0)
        self._vao.bind()
        bound_texture: Optional[Texture] = None
        bound_palette: Optional[Palette] = None
        for buffer, palette, colors, first, count in self._runs:
            # Touch (possibly upload) BEFORE binding: .texture and
            # PaletteTexture.get blit_into on whatever unit is active, so
            # they must not run between the unit assignments below.
            texture = buffer.texture
            if palette is not bound_palette:
                # The palette rides texture unit 1 (palette RAM as a 256x1
                # texture, see PaletteTexture): a run's palette switch is a
                # texture bind, not a 256-vec4 uniform send -- material
                # transitions got cheaper. A palette upload may have
                # disturbed unit 0's binding, so force the atlas re-bind.
                ptex = PaletteTexture.get(palette)
                program["palette_texture"] = 1
                glActiveTexture(GL_TEXTURE1)
                glBindTexture(ptex.target, ptex.id)
                glActiveTexture(GL_TEXTURE0)
                bound_palette = palette
                bound_texture = None
            if texture is not bound_texture:
                glActiveTexture(GL_TEXTURE0)
                glBindTexture(texture.target, texture.id)
                bound_texture = texture
            glVertexAttrib4f(self._loc_colors, *colors)
            glDrawElements(GL_TRIANGLES, count * 6, GL_UNSIGNED_INT, first * 24)
        self._vao.unbind()
        glDisable(GL_BLEND)
        program.stop()

    # -- collision (retained packed screen-space planes) --------------------

    @property
    def collidable(self) -> bool:
        """Always True; participation intent lives per presentation."""
        return True

    @property
    def collision_mask(self) -> np.ndarray:
        """The retained packed default silhouette plane."""
        return self._collision.mask()

    def collision_mask_for(
        self, planes: Optional[Tuple[str, ...]] = None
    ) -> np.ndarray:
        """One retained named plane, or the packed union of several."""
        return self._collision.mask(planes)

    def collision_mask_unpacked(
        self, planes: Optional[Tuple[str, ...]] = None
    ) -> np.ndarray:
        """Debug convenience: unpack a selected plane to screen pixels."""
        return self._collision.unpacked(planes)

    def collides_with(
        self,
        other: Union[Collidable, PlaneCollidable],
        mine: Union[str, Tuple[str, ...], None] = None,
        theirs: Union[str, Tuple[str, ...], None] = None,
    ) -> CollisionQuery:
        """Prepare a collision question against any other collidable engine
        -- another sprite layer, a tile layer, the text layer:

            p1.collides_with(p2).at()                       # silhouettes
            p1.collides_with(p2, mine="attack",
                             theirs=("body_hurt", "limb_hurt")).at()

        No selector means the default plane: do our pixels touch. Selectors
        name planes from the sheets' precalculated vocabulary and validate
        LOUDLY -- a typo'd plane name must be an error, not a hitbox that
        never hits. A mask layer (tiles, text) is the single-plane
        degenerate case: `theirs` cannot select on one. Returns a
        CollisionQuery; nothing is computed until .at() runs, and the
        answer stays boolean -- richness lives in the question."""
        if not other.collidable:
            raise ValueError(f"collides_with() target {other!r} is not collidable")
        mine_t = self._selector(mine, self._sheet.planes, "mine")
        if isinstance(other, PlaneCollidable):
            theirs_t = self._selector(theirs, other.sheet.planes, "theirs")
        elif theirs is not None:
            raise ValueError(
                f"theirs={theirs!r}: {other!r} is a mask layer -- the "
                "single-plane degenerate case has no named planes to select"
            )
        else:
            theirs_t = None
        return CollisionQuery(self, other, mine=mine_t, theirs=theirs_t)

    @staticmethod
    def _selector(
        value: Union[str, Tuple[str, ...], None],
        vocabulary: frozenset,
        argument: str,
    ) -> Optional[Tuple[str, ...]]:
        """Normalise a plane selector (name, or tuple of names, or None)
        and validate every name against the sheet's precalculated plane
        vocabulary."""
        if value is None:
            return None
        names = (value,) if isinstance(value, str) else tuple(value)
        for name in names:
            if name not in vocabulary:
                raise KeyError(
                    f"{argument}={name!r}: unknown collision plane "
                    f"(sheet has {sorted(vocabulary)})"
                )
        return names

    # -- layer-surface metadata (the primary tileset is the engine's face) --

    @property
    def texture(self) -> Texture:
        return self._primary.buffer.texture

    @property
    def size(self) -> Size:
        return self._primary.buffer.size

    @property
    def indexed(self) -> bool:
        return True  # sprite tilesets are indexed by construction

    @property
    def transparency_index(self) -> Optional[int]:
        return self._primary.transparent_index

    @property
    def palette(self) -> Optional[Palette]:
        return self._primary.palettes[self._primary.default_palette]


class SpriteEngine:
    """The static factory: make a SpriteLayer and put it on screen.

        chun_layer = SpriteEngine(sheet)
        chun = chun_layer.sprite("stance-01")

    Calling it returns a SpriteLayer (__new__ hands back the layer; no
    SpriteEngine instance ever exists), appended at the front of the running
    engine's draw order. Placement and layer.enabled are the caller's
    business from there. If no BlitsPerSecond engine exists yet (GL-free
    tests, offline tools), the layer is returned unattached, exactly as
    SpriteLayer(sheet) would build it.
    """

    def __new__(cls, sheet: SpriteSheet) -> "SpriteLayer":
        layer = SpriteLayer(sheet)
        # Peek, don't boot -- see TileEngine.__new__.
        from blitspersecond.blitspersecond import BlitsPerSecond
        from blitspersecond.common.singleton_meta import SingletonMeta

        bps = SingletonMeta._instances.get(BlitsPerSecond)
        if bps is not None:
            bps.layers.add(layer)
        return layer
