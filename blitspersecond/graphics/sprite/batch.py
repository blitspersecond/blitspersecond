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
    GL_SHADER_STORAGE_BUFFER,
    GL_TEXTURE0,
    GL_TEXTURE1,
    GL_TRIANGLES,
    GL_UNSIGNED_INT,
    glActiveTexture,
    glBindBufferBase,
    glBindTexture,
    glDrawElements,
    glUniform1f,
    glUniform4f,
)
from pyglet.graphics.vertexarray import VertexArray
from pyglet.graphics.vertexbuffer import BufferObject

#: Where `quads.vert` expects the quad records. One binding point, fixed,
#: because there is exactly one storage buffer in this program and a name
#: would only be a second place for it to disagree with the shader.
QUAD_BINDING = 0

#: Floats per quad. Mirrors `FramePlan.RECORD` and `quads.vert`'s stride;
#: imported rather than repeated so a change to the layout cannot leave the
#: buffer sized for the old one.
from .frame import FramePlan  # noqa: E402

RECORD = FramePlan.RECORD


def _uniform(program, name: str) -> int:
    """One uniform's location, or -1 if the program has not got it.

    Resolved once at construction rather than looked up per run: pyglet's
    `program[name] = value` is a dict lookup and a type dispatch on a path
    that runs once per draw call, and a run is 6.4us to begin with.
    """
    entry = getattr(program, "uniforms", {}).get(name)
    # A dict, exactly like `program.attributes` -- not an object with a
    # `.location`. Getting that wrong resolves to -1, every write is skipped,
    # `run_colors` stays at its default of zero and the whole pillar draws
    # black. Which is how it presented: "nothing was drawn".
    return int(entry["location"]) if entry else -1


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
        self._loc_colors = _uniform(program, "run_colors")
        self._loc_row = _uniform(program, "run_palette_row")
        self._capacity = 0
        # Still needed, and still empty: a core profile refuses to draw with
        # no vertex array bound, even by a program that reads no attributes.
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
        """Grow to `capacity` quads: a fresh record buffer and index pattern.

        The record buffer is re-uploaded by the caller on every rebuild, so
        nothing carries over. Growth doubles, so this runs O(log n) times.
        """
        if capacity <= self._capacity:
            return
        if self._capacity:
            self._quads.delete()
            self._indices.delete()

        # Nine floats a quad, read by `quads.vert` at `gl_VertexID / 4`.
        # There are no vertex attribute arrays at all any more: positions and
        # texture coordinates stopped being streams when the corner expansion
        # moved to the vertex stage.
        self._quads = BufferObject(capacity * RECORD * 4)
        self._indices = BufferObject(capacity * 6 * 4)

        # Static index pattern: quad i -> [0,1,2, 0,2,3] + 4i. A run draws by
        # OFFSET into it, which is what lets many runs share one buffer -- and
        # it is what makes `gl_VertexID` the QUAD-corner number rather than a
        # draw position, so four vertices run per quad instead of six.
        idx = (
            np.array([0, 1, 2, 0, 2, 3], dtype=np.uint32)
            + (np.arange(capacity, dtype=np.uint32) * 4)[:, None]
        )
        self._indices.set_data_region(
            idx.ctypes.data,  # pyright: ignore[reportArgumentType]
            0,
            idx.nbytes,
        )
        self._vao.bind()
        self._indices.bind_to_index_buffer()
        self._vao.unbind()
        self._capacity = capacity

    def upload(self, data: np.ndarray, quads: int) -> None:
        """Push the frame plan's record array for quads [0, quads)."""
        if quads == 0:
            return
        self.resize(max(self._capacity, quads))
        live = np.ascontiguousarray(data[:quads])
        self._quads.set_data_region(
            live.ctypes.data,  # pyright: ignore[reportArgumentType]
            0,
            live.nbytes,
        )

    def begin(self) -> None:
        """Bind the VAO and the record buffer for the pass."""
        self._vao.bind()
        glBindBufferBase(GL_SHADER_STORAGE_BUFFER, QUAD_BINDING, self._quads.id)

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
        writing a row that had not moved. `glUniform1f` is a ctypes call
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
        # Uniforms rather than generic vertex attributes, because there are
        # no vertex attributes left to be generic about. Same cost shape: one
        # ctypes call each, and the row is still compared before it is written
        # for the reason above.
        if palette_row != bound_row and self._loc_row >= 0:
            glUniform1f(self._loc_row, float(palette_row))
            bound_row = palette_row
        if self._loc_colors >= 0:
            glUniform4f(self._loc_colors, *colors)
        glDrawElements(
            GL_TRIANGLES, count * 6, GL_UNSIGNED_INT, first * 24
        )
        return bound_texture, bound_palette, bound_row
