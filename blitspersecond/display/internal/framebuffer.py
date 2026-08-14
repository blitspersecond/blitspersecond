from typing import TYPE_CHECKING

import pyglet
import pyglet.image.buffer

from blitspersecond.graphics.internal import Shader
from blitspersecond.system import Config

from .presentation import integer_viewport

if TYPE_CHECKING:
    from .surface import Surface


# The CRT look applied by the post-process pass as it presents the composed
# framebuffer. A fixed house aesthetic, not per-game config -- named here so the
# draw path reads as intent rather than a wall of magic numbers.
_CRT_COLOR_MULTIPLIER = (1.1, 1.05, 0.9)
_CRT_BRIGHTNESS = 1.1
_CRT_CONTRAST = 1.2
_CRT_GAMMA = 0.9


class Framebuffer(pyglet.image.buffer.Framebuffer):
    def __init__(
        self,
        width: int | None = None,
        height: int | None = None,
    ):
        super().__init__()
        display = Config().display
        width = display.width if width is None else width
        height = display.height if height is None else height
        self._color_buffer = pyglet.image.Texture.create(
            width,
            height,
            internalformat=pyglet.gl.GL_RGBA,
            min_filter=pyglet.gl.GL_NEAREST,
            mag_filter=pyglet.gl.GL_NEAREST,
        )
        self.attach_texture(
            self._color_buffer,
            attachment=pyglet.gl.GL_COLOR_ATTACHMENT0,
        )
        self._batch = pyglet.graphics.Batch()
        self._shader = Shader.get("framebuffer", "framebuffer")

    def clear(self) -> None:
        self.bind()
        pyglet.gl.glClearColor(0.0, 0.0, 0.0, 0.0)
        pyglet.gl.glClear(pyglet.gl.GL_COLOR_BUFFER_BIT)
        self.unbind()

    def draw(self, surface: "Surface") -> None:
        self.unbind()
        w, h = surface.get_size()
        viewport = integer_viewport(self.width, self.height, w, h)

        pyglet.gl.glViewport(0, 0, w, h)
        pyglet.gl.glDisable(pyglet.gl.GL_SCISSOR_TEST)
        pyglet.gl.glClearColor(0.0, 0.0, 0.0, 1.0)
        pyglet.gl.glClear(pyglet.gl.GL_COLOR_BUFFER_BIT)
        pyglet.gl.glViewport(
            viewport.x,
            viewport.y,
            viewport.width,
            viewport.height,
        )

        self._shader.program["texture1"] = 0
        self._shader.program["scaleFactor"] = float(viewport.scale)
        self._shader.program["viewportOriginY"] = float(viewport.y)
        self._shader.program["colorMultiplier"] = _CRT_COLOR_MULTIPLIER
        self._shader.program["brightness"] = _CRT_BRIGHTNESS
        self._shader.program["contrast"] = _CRT_CONTRAST
        self._shader.program["gamma"] = _CRT_GAMMA

        pyglet.gl.glBindTexture(pyglet.gl.GL_TEXTURE_2D, self._color_buffer.id)
        pyglet.gl.glTexParameteri(
            pyglet.gl.GL_TEXTURE_2D,
            pyglet.gl.GL_TEXTURE_MIN_FILTER,
            pyglet.gl.GL_NEAREST,
        )
        pyglet.gl.glTexParameteri(
            pyglet.gl.GL_TEXTURE_2D,
            pyglet.gl.GL_TEXTURE_MAG_FILTER,
            pyglet.gl.GL_NEAREST,
        )
        pyglet.gl.glDisable(pyglet.gl.GL_DEPTH_TEST)
        pyglet.gl.glDisable(pyglet.gl.GL_BLEND)

        self._shader.program.use()
        vertex_list = self._shader._program.vertex_list_indexed(
            count=4,
            mode=pyglet.gl.GL_TRIANGLES,
            indices=self._shader.indices,
            batch=self._batch,
            in_position=("f", self._shader.vertices),
            in_tex_coords=("f", self._shader.tex_coords),
        )
        vertex_list.draw(pyglet.gl.GL_TRIANGLES)
        vertex_list.delete()
        self._shader.program.stop()
