"""TileBatch: N axis-aligned quads, one texture, one draw call.

The tilemap's GPU backend, and the reason the pillar's contract is one sheet
per map. `draw()` is one `glBindTexture` and one `glDrawElements` for the
whole layer at any quad count -- a second sheet would be two of each, and the
SoA/VBO design would stop paying for itself.

## Why not pyglet's Sprite + Batch

They are general and perform well for what they promise, and that generality
is exactly what a tile layer pays for: a Python `EventDispatcher` per quad, a
`SpriteGroup` per sprite (hashed for coalescing), a per-sprite vertex-list
allocation, six attribute streams per quad of which most never change, and
property -> `set_region` -> element-wise ctypes writes on every move.

A tilemap needs none of it. Quads are axis-aligned and never rotated or
scaled, tint is one whole-batch register, the pool is fixed, and positions
already live in a NumPy SoA. So the render state collapses to:

- **position VBO** -- `(cap, 4, 3)` float32, rewritten in ONE
  `glBufferSubData` when the bounded local map changes. Fine scrolling is a
  generic `translate` attribute and writes no buffer at all.
- **tex_coords VBO** -- written into a CPU mirror at placement time, flushed
  once at the next draw, static thereafter.
- **index buffer** -- the fixed `[0,1,2, 0,2,3] + 4i` pattern, rebuilt only
  when capacity grows.
- **constant attributes** -- `colors`, `translate`, `scale` and `rotation`
  fed as *generic* vertex attributes (`glVertexAttrib*f`, set per draw, no
  buffers). A disabled attribute array reads the current generic value, so
  the shared `default.vert` is used untouched.

Corner order matches pyglet's Sprite -- (x1,y1)(x2,y1)(x2,y2)(x1,y2) -- so a
`TextureRegion`'s twelve tex_coords floats pair up verbatim. The `WindowBlock`
projection UBO comes with the ShaderProgram itself, filled by the compositor
before it walks the layers, so drawing outside a pyglet Batch needs no extra
wiring: this sets no projection and no viewport.

## Orientation costs nothing per frame

Flipping and turning is an indexed reshape of four UV rows, applied ONCE in
`set_quad` at placement time. The per-frame path never sees a flag. That is
the mechanical reason `orientation.py` states its law the way it does -- an
orientation belongs to the placement, never to the source.

## What did not come across from the reference tree

- **`delete()`.** Its only caller was the rebinding teardown, and the sheet is
  immutable now, so nothing rebinds. It was never really a teardown either --
  it freed the three buffers and left the VAO, which is the shape of a
  rebinding helper rather than a release. `BufferObject.__del__` already
  queues the GL delete through the context, so dropping a batch frees its
  buffers anyway. Losing it takes the `Optional` buffers and their `assert ...
  is not None` guards with it.
- **The `TileFlags` alias.** `Orientation` is the engine's one vocabulary for
  this, shared with sprites and the console's pen, and it is an `IntFlag`, so
  chaining is unchanged: `Orientation.FLIP_H | Orientation.FLIP_V`.
"""

from typing import Callable, List, Tuple

import numpy as np
from pyglet.gl import (
    GL_FLOAT,
    GL_TEXTURE0,
    GL_TRIANGLES,
    GL_UNSIGNED_INT,
    glActiveTexture,
    glBindTexture,
    glDrawElements,
    glEnableVertexAttribArray,
    glVertexAttrib1f,
    glVertexAttrib2f,
    glVertexAttrib3f,
    glVertexAttrib4f,
    glVertexAttribPointer,
)
from pyglet.graphics.vertexarray import VertexArray
from pyglet.graphics.vertexbuffer import BufferObject

from ..orientation import Orientation


def _uv_perms() -> Tuple[Tuple[int, ...], ...]:
    """The 8-entry LUT: orientation -> row permutation of a quad's four UVs.

    Computed from the coordinate transforms rather than hand-derived, because
    the compose order is exactly the Tiled trap. Corners live in the tile-local
    unit square, y down, in this engine's corner order
    (x1,y1)(x2,y1)(x2,y2)(x1,y2) = TL, TR, BR, BL.

    The flags build the image transform M = V . H . D; a screen corner `c` then
    samples the ORIGINAL image at M^-1(c) = D(H(V(c))) -- each operation is its
    own inverse -- so entry `i` is the base index of that corner.
    """
    corners = ((0, 0), (1, 0), (1, 1), (0, 1))
    perms = []
    for flags in range(8):
        perm = []
        for x, y in corners:
            if flags & Orientation.FLIP_V:
                y = 1 - y
            if flags & Orientation.FLIP_H:
                x = 1 - x
            if flags & Orientation.TRANSPOSE:
                x, y = y, x
            perm.append(corners.index((x, y)))
        perms.append(tuple(perm))
    return tuple(perms)


_UV_PERMS = _uv_perms()

#: The per-quad state pyglet's Sprite keeps as full vertex streams, fed here as
#: generic attributes instead: (attribute name, glVertexAttrib fn, values).
#: `translate` is generic too but is not constant -- it is the scroll register,
#: written per draw. See `draw()`.
_CONST_ATTRS = (
    ("scale", glVertexAttrib2f, (1.0, 1.0)),
    ("rotation", glVertexAttrib1f, (0.0,)),
)


class TileBatch:
    """N quads, one texture, one program -- built for bulk SoA-driven updates.

    Needs a current GL context to construct, so build it on the render thread
    (a layer's lazy first-blit path, not its `__init__`). `program` is the
    pillar's compiled `ShaderProgram`; `capacity` is in quads and grows through
    `resize()`.
    """

    # Assigned by the `resize()` that `__init__` performs, and never unset --
    # there is no teardown and no rebinding, so these are never None.
    _positions: BufferObject
    _texcoords: BufferObject
    _indices: BufferObject

    def __init__(self, program, capacity: int) -> None:
        self._program = program
        attrs = program.attributes
        self._loc_position: int = attrs["position"]["location"]
        self._loc_tex_coords: int = attrs["tex_coords"]["location"]
        self._loc_translate: int = attrs["translate"]["location"]
        # Generic-attribute constants, resolved to locations once. A name the
        # program does not declare is skipped, so a leaner tile vertex stage
        # that drops the per-vertex matrix build needs no change here.
        self._const_attrs: List[Tuple[Callable[..., None], int, Tuple[float, ...]]] = [
            (fn, attrs[name]["location"], vals)
            for name, fn, vals in _CONST_ATTRS
            if name in attrs
        ]
        #: The whole-batch colour register: rgba multipliers fed as one constant
        #: attribute per draw. -1 means the program has no `colors` attribute
        #: and tinting is silently absent.
        self._loc_colors: int = attrs.get("colors", {}).get("location", -1)

        self._capacity = 0
        self._vao = VertexArray()
        # CPU mirror of the tex_coords stream, plus its permanent compaction
        # scratch. Rows are written by `set_quad` and flushed in one
        # `glBufferSubData` at the next draw.
        self._uv = np.zeros((0, 4, 3), dtype=np.float32)
        self._uv_scratch = np.zeros((0, 4, 3), dtype=np.float32)
        self._uv_dirty = False
        self.resize(max(8, capacity))

    @property
    def program(self):
        """The compiled program this batch draws with.

        Exposed so its layer asks the batch rather than asking
        `shader.program(...)` a second time: one place decides which of the
        two programs this map uses, and it is the construction that passed it
        in."""
        return self._program

    @property
    def capacity(self) -> int:
        """Current capacity in quads."""
        return self._capacity

    def resize(self, capacity: int) -> None:
        """Grow to `capacity` quads: fresh GPU buffers, static streams rebuilt,
        VAO pointers rebound.

        Indices are regenerated and tex_coords re-uploaded from the CPU mirror.
        Positions are recomputed by the caller on every upload, so they need no
        carry-over -- the caller marks itself dirty when it grows.

        Growth is the caller's policy; it doubles, so this runs O(log n) times.
        """
        if capacity <= self._capacity:
            return
        if self._capacity:  # nothing allocated yet on the first call
            self._positions.delete()
            self._texcoords.delete()
            self._indices.delete()

        self._positions = BufferObject(capacity * 4 * 3 * 4)
        self._texcoords = BufferObject(capacity * 4 * 3 * 4)
        self._indices = BufferObject(capacity * 6 * 4)

        # Static index pattern: quad i -> [0,1,2, 0,2,3] + 4i.
        idx = (
            np.array([0, 1, 2, 0, 2, 3], dtype=np.uint32)
            + (np.arange(capacity, dtype=np.uint32) * 4)[:, None]
        )
        # A ctypes address; the GL binding accepts it as a raw pointer even
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
        self._uv_dirty = True  # flushed on the next draw

        # VAO state: the two real attribute arrays plus the index binding. The
        # constant attributes stay DISABLED here -- that is what makes their
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

    def set_quad(
        self,
        index: int,
        tex_coords,
        orientation: Orientation = Orientation.NONE,
    ) -> None:
        """Set quad `index`'s texture rect: twelve floats in corner order
        (x1,y1)(x2,y1)(x2,y2)(x1,y2), as a `TextureRegion.tex_coords` gives
        them.

        `orientation` turns the placement by permuting which UV row pairs with
        which corner. The whole flip/rotation feature is this one indexed
        reshape, done once here at placement; the per-frame path never sees it.

        Written to the CPU mirror only. The GPU copy flushes at the next draw.
        """
        uv = np.asarray(tex_coords, dtype=np.float32).reshape(4, 3)
        if orientation:
            uv = uv[list(_UV_PERMS[int(orientation) & 7])]
        self._uv[index] = uv
        self._uv_dirty = True

    def compact(self, indices: np.ndarray) -> None:
        """Keep UV rows `indices` densely from row zero, preserving order.

        The layer performs the same `take` into its own placement scratch. Both
        mirrors are permanent and swap roles, so a coarse-scroll cull allocates
        nothing.
        """
        n = len(indices)
        if n:
            np.take(self._uv, indices, axis=0, out=self._uv_scratch[:n])
        self._uv, self._uv_scratch = self._uv_scratch, self._uv
        self._uv_dirty = True

    def upload_positions(self, verts: np.ndarray) -> None:
        """One `glBufferSubData` of the compact live range.

        `verts` is C-contiguous `(n, 4, 3)` float32, corner order as `set_quad`,
        already expanded from each placement's origin and extent.
        """
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
        """Draw quads [0, count): program, texture, the constant attributes,
        one `glDrawElements`.

        **Blend state is NOT set here.** It is compositing state and belongs to
        the compositor, which enables it once around its walk of the draw order
        -- every pillar wanted the same two calls, and repeating them per layer
        was redundant state changes and several places for one to drift. A
        caller driving this batch outside `Display` must establish blend
        itself.

        `translate` is the scroll register, Amiga-style: one generic vertex
        attribute shifts every quad on the GPU, so scrolling a whole layer
        writes no buffers. Pass the NEGATED scroll -- the view moving right
        moves content left.

        `colors` is the whole-batch colour register, rgba multipliers over
        every quad (both fragment stages multiply by `vertex_colors`). Like
        `translate` it is one constant attribute, so tinting or fading a whole
        layer writes no buffers either.
        """
        if count == 0:
            return
        self._program.use()
        glActiveTexture(GL_TEXTURE0)
        glBindTexture(texture.target, texture.id)
        for fn, loc, vals in self._const_attrs:
            fn(loc, *vals)
        if self._loc_colors >= 0:
            glVertexAttrib4f(self._loc_colors, *colors)
        glVertexAttrib3f(self._loc_translate, translate[0], translate[1], 0.0)
        self._vao.bind()
        if self._uv_dirty:
            live = self._uv[:count]
            self._texcoords.set_data_region(
                live.ctypes.data,  # pyright: ignore[reportArgumentType]
                0,
                live.nbytes,
            )
            self._uv_dirty = False
        glDrawElements(GL_TRIANGLES, count * 6, GL_UNSIGNED_INT, 0)
        self._vao.unbind()
        self._program.stop()
