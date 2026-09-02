"""The sprite pillar's GPU buffers: N quads, MANY materials, one pass.

Where `TileBatch` is one texture and one `glDrawElements`, this is a sequence
of runs -- each a stretch of adjacent quads sharing `(buffer, palette bank,
palette row, colour registers)` -- issued in order with binds between them.
That difference is the pillar, not an implementation detail: a tilemap spends
its single bind on a uniform grid, and this spends binds on ordered
composition.

## Both streams are uploaded every rebuild

`TileBatch` keeps tex_coords as a CPU mirror written per placement, because a
tile's source rect changes only when something is stamped. A sprite's UVs
change whenever a pose or an orientation does, which for anything animated is
most frames -- so the frame plan produces both arrays whole and this uploads
both. `Sprite._sync` is dirty-gated, so a still scene uploads nothing.

## Order of binds inside a run

Touch the texture BEFORE binding either unit. `PixelBuffer.texture` and
`PaletteTexture.get` may blit into whatever unit is currently active, so
letting them run between the unit assignments would upload into the wrong one.

A palette upload may itself disturb unit 0's binding, so a run that changes
BANK invalidates the remembered texture and rebinds it. Changing palette
WITHIN a bank does not: the bank is one 256xN texture and the row is a
generic vertex attribute, so a costume swap between two runs of one tileset
costs a `glVertexAttrib1f` and nothing else. What is still expensive is a
change of BUFFER, which is why the frame plan works to keep instanced
presentations on one tileset.
"""

from typing import List, Optional, Tuple

import numpy as np
from pyglet.gl import (
    GL_ARRAY_BUFFER,
    GL_FLOAT,
    GL_TEXTURE0,
    GL_TEXTURE1,
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

#: Per-quad state pyglet's Sprite keeps as full vertex streams, fed here as
#: generic attributes instead. `translate` stays zero because the frame plan
#: bakes positions; `colors` is NOT here because it is written per run.
_CONST_ATTRS = (
    ("scale", glVertexAttrib2f, (1.0, 1.0)),
    ("rotation", glVertexAttrib1f, (0.0,)),
    ("translate", glVertexAttrib3f, (0.0, 0.0, 0.0)),
)


class SpriteBatch:
    """Quad buffers for one sprite layer, drawn as an ordered list of runs.

    Needs a current GL context to construct, so build it on the render thread
    -- the layer's lazy first-draw path, never its `__init__`.
    """

    _positions: BufferObject
    _texcoords: BufferObject
    _indices: BufferObject

    def __init__(self, program, capacity: int) -> None:
        self._program = program
        attrs = program.attributes
        self._loc_position: int = attrs["position"]["location"]
        self._loc_tex_coords: int = attrs["tex_coords"]["location"]
        self._const_attrs: List[Tuple[object, int, Tuple[float, ...]]] = [
            (fn, attrs[name]["location"], vals)
            for name, fn, vals in _CONST_ATTRS
            if name in attrs
        ]
        #: The per-run colour register. -1 means the program has no `colors`
        #: attribute and tinting is silently absent.
        self._loc_colors: int = attrs.get("colors", {}).get("location", -1)
        #: The per-run palette register: which row of the bound bank the
        #: fragment stage reads. -1 means the program has no `palette_row`
        #: -- an RGBA stage, which has no bank to pick a row out of.
        self._loc_palette_row: int = attrs.get("palette_row", {}).get(
            "location", -1
        )
        self._capacity = 0
        self._vao = VertexArray()
        self.resize(max(8, capacity))

    @property
    def program(self):
        """The compiled program this batch draws with -- asked of the batch so
        one place decides, the construction that passed it in."""
        return self._program

    @property
    def capacity(self) -> int:
        return self._capacity

    def resize(self, capacity: int) -> None:
        """Grow to `capacity` quads: fresh buffers, index pattern rebuilt, VAO
        pointers rebound.

        Both streams are re-uploaded by the caller on every rebuild, so
        nothing carries over. Growth doubles, so this runs O(log n) times.
        """
        if capacity <= self._capacity:
            return
        if self._capacity:
            self._positions.delete()
            self._texcoords.delete()
            self._indices.delete()

        self._positions = BufferObject(capacity * 4 * 3 * 4)
        self._texcoords = BufferObject(capacity * 4 * 3 * 4)
        self._indices = BufferObject(capacity * 6 * 4)

        # Static index pattern: quad i -> [0,1,2, 0,2,3] + 4i. A run then
        # draws by OFFSET into it -- 6 indices and 24 bytes per quad -- which
        # is what lets many runs share one buffer.
        idx = (
            np.array([0, 1, 2, 0, 2, 3], dtype=np.uint32)
            + (np.arange(capacity, dtype=np.uint32) * 4)[:, None]
        )
        self._indices.set_data_region(
            idx.ctypes.data,  # pyright: ignore[reportArgumentType]
            0,
            idx.nbytes,
        )

        # The two real attribute arrays plus the index binding. The constant
        # attributes stay DISABLED -- that is what makes their generic values
        # apply.
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

    def upload(self, verts: np.ndarray, uvs: np.ndarray, quads: int) -> None:
        """Push the frame plan's two staging arrays for quads [0, quads)."""
        if quads == 0:
            return
        self.resize(max(self._capacity, quads))
        live = np.ascontiguousarray(verts[:quads])
        self._positions.set_data_region(
            live.ctypes.data,  # pyright: ignore[reportArgumentType]
            0,
            live.nbytes,
        )
        coords = np.ascontiguousarray(uvs[:quads])
        self._texcoords.set_data_region(
            coords.ctypes.data,  # pyright: ignore[reportArgumentType]
            0,
            coords.nbytes,
        )

    def begin(self) -> None:
        """Bind the VAO and set the attributes that hold for every run."""
        self._vao.bind()
        for fn, location, values in self._const_attrs:
            fn(location, *values)  # type: ignore[operator]

    def end(self) -> None:
        self._vao.unbind()

    def draw_run(
        self,
        texture,
        palette_texture,
        palette_row: int,
        colors: Tuple[float, float, float, float],
        first: int,
        count: int,
        bound_texture,
        bound_palette,
        bound_row,
    ):
        """Issue one run, binding and setting only what actually changed.

        Returns the new `(bound_texture, bound_palette, bound_row)` so the
        caller carries the state across the loop -- explicit rather than held
        on the batch, because it is the DRAW's state and lives exactly as
        long as the pass.

        **The row is tracked, not written every run, and that is measured
        rather than assumed.** A run boundary means SOMETHING in the material
        changed, not everything -- and the row is the field least likely to
        be it. It answers whether Chun-Li is wearing her 1P blue costume or
        her 2P red one. That is one answer for a whole presentation and
        usually one for a whole frame, so a run cut by anything else was
        writing a row that had not moved. `glVertexAttrib1f` is a ctypes call
        rather than an assignment, about 2us, so that cost 2.7us per run.
        `util/run_cost.py` at 100,000 presentations cut into 4,096 runs BY
        COLOUR: 24.4ms shipped, 35.4ms with the row written unconditionally,
        27.1ms with this comparison.
        """
        if palette_texture is not bound_palette:
            # Unit 1 carries palette RAM as a 256xN texture rather than a
            # 256-vec4 uniform: see PaletteTexture. Uploading may disturb
            # unit 0, so the atlas is forced to rebind after.
            self._program["palette_texture"] = 1
            glActiveTexture(GL_TEXTURE1)
            glBindTexture(palette_texture.target, palette_texture.id)
            glActiveTexture(GL_TEXTURE0)
            bound_palette = palette_texture
            bound_texture = None
        if texture is not bound_texture:
            glActiveTexture(GL_TEXTURE0)
            glBindTexture(texture.target, texture.id)
            bound_texture = texture
        if palette_row != bound_row and self._loc_palette_row >= 0:
            glVertexAttrib1f(self._loc_palette_row, float(palette_row))
            bound_row = palette_row
        if self._loc_colors >= 0:
            glVertexAttrib4f(self._loc_colors, *colors)
        glDrawElements(
            GL_TRIANGLES, count * 6, GL_UNSIGNED_INT, first * 24
        )
        return bound_texture, bound_palette, bound_row
