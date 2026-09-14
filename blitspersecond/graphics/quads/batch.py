"""GPU storage for an ordered list of clipped projective quads."""

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
    glVertexAttribPointer,
)
from pyglet.graphics.vertexarray import VertexArray
from pyglet.graphics.vertexbuffer import BufferObject


class QuadBatch:
    """Projective vertices and their ordered triangle fans, drawn once."""

    def __init__(self, program, capacity: int) -> None:
        self._program = program
        attrs = program.attributes
        self._loc_position = attrs["position"]["location"]
        self._loc_tex_coords = attrs["tex_coords"]["location"]
        self._loc_texture_region = attrs["texture_region"]["location"]
        self._loc_palette_row = attrs.get("palette_row", {}).get("location", -1)
        self._capacity = 0
        self._vao = VertexArray()
        self.resize(max(32, capacity))

    @property
    def program(self):
        return self._program

    @property
    def capacity(self) -> int:
        return self._capacity

    def resize(self, capacity: int) -> None:
        """Grow the four buffers, preserving nothing: the list reuploads."""
        if capacity <= self._capacity:
            return
        if self._capacity:
            self._positions.delete()
            self._texcoords.delete()
            self._regions.delete()
            self._indices.delete()

        self._positions = BufferObject(capacity * 3 * 4)
        self._texcoords = BufferObject(capacity * 3 * 4)
        self._regions = BufferObject(capacity * 4 * 4)
        # A convex n-gon needs 3(n-2) indices, always fewer than 3n.
        self._indices = BufferObject(capacity * 3 * 4)

        self._vao.bind()
        self._positions.bind()
        glEnableVertexAttribArray(self._loc_position)
        glVertexAttribPointer(self._loc_position, 3, GL_FLOAT, False, 0, 0)
        self._texcoords.bind()
        glEnableVertexAttribArray(self._loc_tex_coords)
        glVertexAttribPointer(self._loc_tex_coords, 3, GL_FLOAT, False, 0, 0)
        self._regions.bind()
        glEnableVertexAttribArray(self._loc_texture_region)
        glVertexAttribPointer(self._loc_texture_region, 4, GL_FLOAT, False, 0, 0)
        self._indices.bind_to_index_buffer()
        self._vao.unbind()
        self._capacity = capacity

    def upload(
        self,
        positions: np.ndarray,
        texcoords: np.ndarray,
        regions: np.ndarray,
        indices: np.ndarray,
    ) -> None:
        """Upload the compact live geometry, once per stream."""
        self._positions.set_data_region(
            positions.ctypes.data,  # pyright: ignore[reportArgumentType]
            0,
            positions.nbytes,
        )
        self._texcoords.set_data_region(
            texcoords.ctypes.data,  # pyright: ignore[reportArgumentType]
            0,
            texcoords.nbytes,
        )
        self._regions.set_data_region(
            regions.ctypes.data,  # pyright: ignore[reportArgumentType]
            0,
            regions.nbytes,
        )
        self._indices.set_data_region(
            indices.ctypes.data,  # pyright: ignore[reportArgumentType]
            0,
            indices.nbytes,
        )

    def draw(self, texture, index_count: int, palette_row: int) -> None:
        """Draw the indices in their submitted order, without depth state."""
        if index_count == 0:
            return
        self._program.use()
        glActiveTexture(GL_TEXTURE0)
        glBindTexture(texture.target, texture.id)
        if self._loc_palette_row >= 0:
            glVertexAttrib1f(self._loc_palette_row, float(palette_row))
        self._vao.bind()
        glDrawElements(GL_TRIANGLES, index_count, GL_UNSIGNED_INT, 0)
        self._vao.unbind()
        self._program.stop()
