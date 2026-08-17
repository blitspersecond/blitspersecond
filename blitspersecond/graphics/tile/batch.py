"""TileBatch -- the domain-specific replacement for pyglet's Sprite+Batch on the
tile path: one texture, one shader, N axis-aligned quads fed straight from a SoA.

pyglet's Sprite/Batch are general and perform well for what they promise, but
that generality is exactly what the tile path pays for: a Python EventDispatcher
object per quad, a SpriteGroup per sprite (hashed for coalescing), a per-sprite
vertex-list allocation, SIX attribute streams per quad (52 floats, most never
changing), and property->set_region->element-wise-ctypes writes on every move.
Our domain needs none of it (see bps-one-texture-per-layer): quads are
axis-aligned, never rotated/scaled (tint is one whole-batch register), the pool is fixed, and positions
already live in a numpy SoA.

So the whole render state collapses to:

  * position VBO  -- (cap, 4, 3) float32, rewritten in ONE glBufferSubData
                     when the bounded local tilemap changes. Fine scrolling
                     remains a generic translate attribute and writes no VBO.
  * tex_coords VBO -- written at blit() time into a CPU mirror, uploaded
                     lazily in one shot; static thereafter.
  * index buffer  -- the fixed quad pattern [0,1,2, 0,2,3]+4i, rebuilt only on
                     capacity growth.
  * constant attrs -- colors/translate/scale/rotation from bps's shared
                     default.vert are fed as *generic* vertex attributes
                     (glVertexAttrib*f, set per draw, no buffers): disabled
                     attribute arrays read the current generic value, so the
                     shared shader stays untouched. (A leaner tile.vert that
                     drops the per-vertex matrix build is a possible later
                     step -- GPU-side, not our bottleneck.)

Vertex corner order matches Sprite's (x1,y1)(x2,y1)(x2,y2)(x1,y2) so a
TextureRegion's 12 tex_coords floats pair up verbatim. The WindowBlock
projection UBO plumbing comes with the ShaderProgram itself, so drawing outside
a pyglet Batch needs no extra wiring.
"""

from enum import IntFlag
from typing import Callable, Tuple

import numpy as np
from pyglet.gl import (
    GL_BLEND,
    GL_FLOAT,
    GL_ONE_MINUS_SRC_ALPHA,
    GL_SRC_ALPHA,
    GL_TEXTURE0,
    GL_TRIANGLES,
    GL_UNSIGNED_INT,
    glActiveTexture,
    glBindTexture,
    glBlendFunc,
    glDisable,
    glDrawElements,
    glEnable,
    glEnableVertexAttribArray,
    glVertexAttrib1f,
    glVertexAttrib2f,
    glVertexAttrib3f,
    glVertexAttrib4f,
    glVertexAttribPointer,
)
from pyglet.graphics.vertexarray import VertexArray
from pyglet.graphics.vertexbuffer import BufferObject

class TileFlags(IntFlag):
    """Orientation of one placement: the TMX flip bits, engine-side.

    A property of the *blit* (the placement), never of a pooled Tile -- the same
    source tile appears flipped and unflipped within one map, and a pooled
    source-ref must stay placement-free. Tiled semantics: TRANSPOSE (the
    'diagonal flip', an x/y axis swap) applies FIRST, then FLIP_H, then FLIP_V
    -- that ordering is baked into the UV permutation table, so callers just OR
    bits together. The eight combinations cover every 90-degree rotation and
    mirror (the ROT_* aliases spell out the rotations). Rendering cost: zero --
    a flag is just a different pairing of the four static tex_coords with the
    four quad corners (see _UV_PERMS). For rectangular tiles, TileEngine swaps
    the placement width/height when TRANSPOSE is present, so the permutation
    rotates the same pixels without stretching them.
    """

    NONE = 0
    TRANSPOSE = 1  # Tiled's diagonal bit: swap x/y axes (applied first)
    FLIP_H = 2  # mirror across the vertical axis
    FLIP_V = 4  # mirror across the horizontal axis
    ROT_90 = TRANSPOSE | FLIP_H  # 90 degrees clockwise
    ROT_180 = FLIP_H | FLIP_V
    ROT_270 = TRANSPOSE | FLIP_V


def _uv_perms() -> Tuple[Tuple[int, ...], ...]:
    """The 8-entry LUT: flags -> row permutation of a quad's four tex_coords.

    Computed from the coordinate transforms rather than hand-derived (the
    compose order is exactly the Tiled trap). Corners live in the tile-local
    unit square, y down, in the engine's corner order (x1,y1)(x2,y1)(x2,y2)
    (x1,y2) = TL,TR,BR,BL. The flags build the image transform M = V∘H∘D;
    screen corner c then samples the ORIGINAL image at M⁻¹(c) = D(H(V(c)))
    (each op is its own inverse), so entry i is the base index of that corner."""
    corners = ((0, 0), (1, 0), (1, 1), (0, 1))
    perms = []
    for flags in range(8):
        perm = []
        for x, y in corners:
            if flags & TileFlags.FLIP_V:
                y = 1 - y
            if flags & TileFlags.FLIP_H:
                x = 1 - x
            if flags & TileFlags.TRANSPOSE:
                x, y = y, x
            perm.append(corners.index((x, y)))
        perms.append(tuple(perm))
    return tuple(perms)


_UV_PERMS = _uv_perms()

# The constant per-quad state Sprite kept as full vertex streams. Fed as generic
# attributes instead: (attribute name, glVertexAttrib fn, values). `translate`
# is generic too but not constant -- it's the scroll register (see draw()).
_CONST_ATTRS = (
    ("scale", glVertexAttrib2f, (1.0, 1.0)),
    ("rotation", glVertexAttrib1f, (0.0,)),
)


class TileBatch:
    """N quads, one texture, one program -- built for bulk SoA-driven updates.

    Needs a current GL context to construct (create it on the render thread,
    e.g. from a layer's lazy _ensure_built). `program` is the bps shader's
    pyglet ShaderProgram; capacity is in quads and grows via resize().
    """

    def __init__(self, program, capacity: int) -> None:
        self._program = program
        attrs = program.attributes
        self._loc_position: int = attrs["position"]["location"]
        self._loc_tex_coords: int = attrs["tex_coords"]["location"]
        # Generic-attribute constants, resolved to locations once. Missing names
        # are skipped so a future leaner tile shader Just Works.
        self._const_attrs: list[Tuple[Callable[..., None], int, Tuple[float, ...]]] = [
            (fn, attrs[name]["location"], vals)
            for name, fn, vals in _CONST_ATTRS
            if name in attrs
        ]

        self._loc_translate: int = attrs["translate"]["location"]
        # The whole-batch colour register: rgba multipliers fed as one
        # constant attribute per draw (see draw(colors=...)). -1 = the
        # shader has no colors attribute and tinting is silently absent.
        self._loc_colors: int = attrs.get("colors", {}).get("location", -1)
        self._capacity = 0
        self._vao = VertexArray()
        self._positions: BufferObject | None = None
        self._texcoords: BufferObject | None = None
        self._indices: BufferObject | None = None
        # CPU mirror of the tex_coords stream plus permanent compaction scratch.
        # Rows are written by set_quad() and flushed in one glBufferSubData.
        self._uv = np.zeros((0, 4, 3), dtype=np.float32)
        self._uv_scratch = np.zeros((0, 4, 3), dtype=np.float32)
        self._uv_dirty = False
        self.resize(max(8, capacity))

    @property
    def capacity(self) -> int:
        """Current capacity in quads."""
        return self._capacity

    def resize(self, capacity: int) -> None:
        """Grow to `capacity` quads: fresh GPU buffers, static streams rebuilt
        (indices regenerated, tex_coords re-uploaded from the CPU mirror), VAO
        pointers rebound. Position data is recomputed by the caller every
        upload, so it needs no carry-over."""
        if capacity <= self._capacity:
            return
        for buf in (self._positions, self._texcoords, self._indices):
            if buf is not None:
                buf.delete()

        self._positions = BufferObject(capacity * 4 * 3 * 4)
        self._texcoords = BufferObject(capacity * 4 * 3 * 4)
        self._indices = BufferObject(capacity * 6 * 4)

        # Static index pattern: quad i -> [0,1,2, 0,2,3] + 4i.
        idx = (
            np.array([0, 1, 2, 0, 2, 3], dtype=np.uint32)
            + (np.arange(capacity, dtype=np.uint32) * 4)[:, None]
        )
        # ctypes address; the GL binding accepts it as a raw pointer even
        # though the stub only names Sequence[int] | CTypesPointer.
        self._indices.set_data_region(
            idx.ctypes.data,  # pyright: ignore[reportArgumentType]
            0,
            idx.nbytes,
        )

        grown = np.zeros((capacity, 4, 3), dtype=np.float32)
        grown[: len(self._uv)] = self._uv
        self._uv = grown
        self._uv_scratch = np.zeros((capacity, 4, 3), dtype=np.float32)
        self._uv_dirty = True  # flushed on next draw

        # VAO state: the two real attribute arrays + the index binding. The
        # constant attributes stay *disabled* here -- that's what makes their
        # generic values apply.
        self._vao.bind()
        self._positions.bind()
        glEnableVertexAttribArray(self._loc_position)
        glVertexAttribPointer(self._loc_position, 3, GL_FLOAT, False, 0, 0)
        self._texcoords.bind()
        glEnableVertexAttribArray(self._loc_tex_coords)
        glVertexAttribPointer(self._loc_tex_coords, 3, GL_FLOAT, False, 0, 0)
        self._indices.bind_to_index_buffer()
        self._vao.unbind()

        self._capacity = capacity

    def set_quad(self, index: int, tex_coords, flags: TileFlags = TileFlags.NONE) -> None:
        """Set quad `index`'s texture rect: 12 floats, a TextureRegion's
        .tex_coords, corner order (x1,y1)(x2,y1)(x2,y2)(x1,y2). `flags` orients
        the placement by permuting which UV row pairs with which corner -- the
        whole flip/rotation feature is this one indexed reshape, done once at
        placement time; the per-frame path never sees it. Written to the CPU
        mirror; the GPU copy flushes lazily at the next draw."""
        uv = np.asarray(tex_coords, dtype=np.float32).reshape(4, 3)
        if flags:
            uv = uv[list(_UV_PERMS[int(flags) & 7])]
        self._uv[index] = uv
        self._uv_dirty = True

    def compact(self, indices: np.ndarray) -> None:
        """Keep UV rows `indices` densely from row zero, preserving order.

        TileEngine performs the same take into its placement scratch array.
        Both mirrors are permanent and swap roles, so a coarse-scroll cull
        allocates nothing."""
        n = len(indices)
        if n:
            np.take(self._uv, indices, axis=0, out=self._uv_scratch[:n])
        self._uv, self._uv_scratch = self._uv_scratch, self._uv
        self._uv_dirty = True

    def upload_positions(self, verts: np.ndarray) -> None:
        """One glBufferSubData of the compact live range.
        `verts` is C-contiguous (n, 4, 3) float32 -- corner order as set_quad --
        and already expanded from each placement's origin + extent."""
        assert self._positions is not None
        self._positions.set_data_region(
            verts.ctypes.data,  # pyright: ignore[reportArgumentType]
            0,
            verts.nbytes,
        )

    def draw(
        self,
        texture,
        count: int,
        translate: Tuple[float, float] = (0.0, 0.0),
        colors: Tuple[float, float, float, float] = (1.0, 1.0, 1.0, 1.0),
    ) -> None:
        """Draw quads [0, count): program + texture + blend state (same recipe
        as SpriteGroup.set_state), constant attrs, one glDrawElements.

        `translate` is the scroll register, Amiga-style: one generic vertex
        attribute shifts every quad on the GPU, so scrolling a whole layer
        writes no buffers at all. Pass the NEGATED scroll (view moves right ->
        content moves left).

        `colors` is the whole-batch colour register -- rgba multipliers over
        every quad (both frag shaders multiply by vertex_colors). Like
        translate it is one constant attribute: tinting or fading a whole
        layer writes no buffers."""
        if count == 0:
            return
        self._program.use()
        glActiveTexture(GL_TEXTURE0)
        glBindTexture(texture.target, texture.id)
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        for fn, loc, vals in self._const_attrs:
            fn(loc, *vals)
        if self._loc_colors >= 0:
            glVertexAttrib4f(self._loc_colors, *colors)
        glVertexAttrib3f(self._loc_translate, translate[0], translate[1], 0.0)
        self._vao.bind()
        if self._uv_dirty:
            assert self._texcoords is not None
            live = self._uv[:count]
            self._texcoords.set_data_region(
                live.ctypes.data,  # pyright: ignore[reportArgumentType]
                0,
                live.nbytes,
            )
            self._uv_dirty = False
        glDrawElements(GL_TRIANGLES, count * 6, GL_UNSIGNED_INT, 0)
        self._vao.unbind()
        glDisable(GL_BLEND)
        self._program.stop()

    def delete(self) -> None:
        """Release the GL objects now rather than at GC time."""
        for buf in (self._positions, self._texcoords, self._indices):
            if buf is not None and buf.id is not None:
                buf.delete()
        self._positions = self._texcoords = self._indices = None
