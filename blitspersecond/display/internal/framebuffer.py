from typing import TYPE_CHECKING

import pyglet
import pyglet.image.buffer
from pyglet.graphics import shader

from blitspersecond.common import PACKAGE_ROOT

from .viewport import integer_viewport

if TYPE_CHECKING:
    from .surface import Surface


# `blitspersecond.gl.Shader` exists to be SHARED -- tile and sprite each ask
# for a program by name because many layers want the SAME compiled pair.
# `Framebuffer` is constructed exactly once, by `Display.__init__`, for the
# lifetime of the process -- there is no second caller ever asking for
# ("framebuffer", "framebuffer") to share with, so the cache it went through
# bought nothing. Compiled directly, same as the rest of this file's GL
# calls, rather than through a shared cache with exactly one entry.
_SHADERS = PACKAGE_ROOT / "assets" / "shaders"

# The CRT look applied by the post-process pass as it presents the composed
# framebuffer. A fixed house aesthetic, not per-game config -- named here so the
# draw path reads as intent rather than a wall of magic numbers.
_CRT_COLOR_MULTIPLIER = (1.1, 1.05, 0.9)
_CRT_BRIGHTNESS = 1.1
_CRT_CONTRAST = 1.2
_CRT_GAMMA = 0.9

# The one full-screen quad this pass ever draws: two triangles covering clip
# space, textured 0-1. Used to live on `Shader` itself, but this post-pass
# was its only reader anywhere in the tree -- tile/sprite/glyph each build
# their own geometry entirely separately -- so a "what does every shader
# draw" property was really "what does this ONE draw draw".
_QUAD_VERTICES = [-1.0, -1.0, 1.0, -1.0, 1.0, 1.0, -1.0, 1.0]
_QUAD_TEX_COORDS = [0.0, 0.0, 1.0, 0.0, 1.0, 1.0, 0.0, 1.0]
_QUAD_INDICES = [0, 1, 2, 0, 2, 3]


class Framebuffer(pyglet.image.buffer.Framebuffer):
    def __init__(
        self,
        width: int,
        height: int,
    ):
        super().__init__()
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

        vert_raw = (_SHADERS / "framebuffer.vert").read_text(encoding="utf-8")
        frag_raw = (_SHADERS / "framebuffer.frag").read_text(encoding="utf-8")
        self._program = shader.ShaderProgram(
            shader.Shader(vert_raw, "vertex"),
            shader.Shader(frag_raw, "fragment"),
        )

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

        self._program["texture1"] = 0
        self._program["scaleFactor"] = float(viewport.scale)
        self._program["viewportOriginY"] = float(viewport.y)
        self._program["colorMultiplier"] = _CRT_COLOR_MULTIPLIER
        self._program["brightness"] = _CRT_BRIGHTNESS
        self._program["contrast"] = _CRT_CONTRAST
        self._program["gamma"] = _CRT_GAMMA

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

        self._program.use()
        vertex_list = self._program.vertex_list_indexed(
            count=4,
            mode=pyglet.gl.GL_TRIANGLES,
            indices=_QUAD_INDICES,
            batch=self._batch,
            in_position=("f", _QUAD_VERTICES),
            in_tex_coords=("f", _QUAD_TEX_COORDS),
        )
        vertex_list.draw(pyglet.gl.GL_TRIANGLES)
        vertex_list.delete()
        self._program.stop()
