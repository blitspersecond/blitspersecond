import os
from pyglet.gl import (
    glActiveTexture,
    glBindTexture,
    glBlendFunc,
    glDisable,
    glEnable,
    glTexParameteri,
    GL_BLEND,
    GL_SRC_ALPHA,
    GL_ONE_MINUS_SRC_ALPHA,
    GL_TEXTURE_2D,
    GL_TEXTURE_MIN_FILTER,
    GL_NEAREST,
    GL_TEXTURE_MAG_FILTER,
    GL_TEXTURE0,
    GL_TRIANGLES,
)
from .config import Config
from pyglet.graphics import Batch, Group
from pyglet.graphics.shader import Shader, ShaderProgram
from pyglet.image import Texture


class Renderer:
    def __init__(self):
        self._c = Config()
        self._indices = (0, 1, 2, 0, 2, 3)
        self._coords = (0, 0, 1, 0, 1, 1, 0, 1)

        # Load shaders from external files
        vertex_shader = self._load_shader("vertex.glsl", "vertex")
        fragment_shader = self._load_shader("fragment.glsl", "fragment")
        self._program = ShaderProgram(vertex_shader, fragment_shader)

    def _load_shader(self, filename: str, shader_type: str) -> Shader:
        module_dir = os.path.dirname(__file__)
        file_path = os.path.join(module_dir, "resources", filename)
        with open(file_path, "r") as shader_file:
            return Shader(shader_file.read(), shader_type)

    def render(self, texture: Texture):
        batch = Batch()

        width = self._c.window.width
        height = self._c.window.height
        scale = self._c.window.scale * self._c.window.dpi_scale

        # Calculate scaled vertices based on current width, height, and scale
        # fmt: off
        vertices = (
            0, 0,
            width * scale, 0,
            width * scale, height * scale,
            0, height * scale,
        )
        # fmt: on

        # Bind texture and set parameters
        glActiveTexture(GL_TEXTURE0)
        glBindTexture(GL_TEXTURE_2D, texture.id)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST)

        # Set up blending and shader program
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        self._program.use()

        # Add the vertex list to the batch, with Group set to None

        self._program.vertex_list_indexed(
            4,
            GL_TRIANGLES,
            self._indices,
            batch,
            None,
            position=("f", vertices),
            tex_coords=("f", self._coords),
        )

        # Draw the batch
        batch.draw()

        # Clean up state
        glDisable(GL_BLEND)
        self._program.stop()
