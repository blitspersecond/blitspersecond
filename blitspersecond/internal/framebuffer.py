"""The compose target, and the seam the engine hands it across.

How the chain works, why one buffer is enough, what the counters really
count and what the present pass does: `bps/reference/engine/display.md`.
"""

from contextlib import contextmanager
from typing import TYPE_CHECKING, Iterator

import pyglet
import pyglet.image.buffer
from pyglet.graphics import shader
from pyglet.math import Mat4

from blitspersecond.common import PACKAGE_ROOT

from .window.viewport import PresentationViewport, integer_viewport

if TYPE_CHECKING:
    from .window.surface import Surface


# Compiled here rather than through a module accessor: one reader, one
# instance for the life of the process.
_SHADERS = PACKAGE_ROOT / "assets" / "shaders"

# The house CRT look, applied by the present pass. Fixed, not per-game.
_CRT_COLOR_MULTIPLIER = (1.1, 1.05, 0.9)
_CRT_BRIGHTNESS = 1.1
_CRT_CONTRAST = 1.2
_CRT_GAMMA = 0.9

# The one full-screen quad this pass ever draws: two triangles covering
# clip space, textured 0-1.
_QUAD_VERTICES = [-1.0, -1.0, 1.0, -1.0, 1.0, 1.0, -1.0, 1.0]
_QUAD_TEX_COORDS = [0.0, 0.0, 1.0, 0.0, 1.0, 1.0, 0.0, 1.0]
_QUAD_INDICES = [0, 1, 2, 0, 2, 3]


class Framebuffer(pyglet.image.buffer.Framebuffer):
    def __init__(
        self,
        width: int,
        height: int,
        surface: "Surface",
    ):
        super().__init__()
        # Held to declare this buffer's render space -- see `as_target`. The
        # projection is a window-level uniform in pyglet, so establishing it
        # needs the window even though the space itself is the buffer's.
        self._surface = surface
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
        # The batch owns the vertex domain; it is never drawn. The quad is
        # issued directly below, so drawing the batch would draw it twice.
        self._batch = pyglet.graphics.Batch()

        vert_raw = (_SHADERS / "framebuffer.vert").read_text(encoding="utf-8")
        frag_raw = (_SHADERS / "framebuffer.frag").read_text(encoding="utf-8")
        self._program = shader.ShaderProgram(
            shader.Shader(vert_raw, "vertex"),
            shader.Shader(frag_raw, "fragment"),
        )
        # The look never varies, so it is uploaded once. Written inside
        # `use()` because pyglet only has a bindless path where the driver
        # offers DSA; without it an unbound write lands on whatever program
        # is current.
        self._program.use()
        self._program["texture1"] = 0
        self._program["colorMultiplier"] = _CRT_COLOR_MULTIPLIER
        self._program["brightness"] = _CRT_BRIGHTNESS
        self._program["contrast"] = _CRT_CONTRAST
        self._program["gamma"] = _CRT_GAMMA
        self._program.stop()
        #: The viewport the size-dependent uniforms currently describe. They
        #: change only when the window does.
        self._uniform_viewport: "PresentationViewport | None" = None

        # Built once: the geometry is a compile-time constant, so there is
        # nothing per-frame to say about it. Rebuilding it every present
        # allocates in the vertex domain, re-uploads 22 unchanged numbers and
        # discards the list again -- all to draw the same two triangles.
        self._quad = self._program.vertex_list_indexed(
            count=4,
            mode=pyglet.gl.GL_TRIANGLES,
            indices=_QUAD_INDICES,
            batch=self._batch,
            in_position=("f", _QUAD_VERTICES),
            in_tex_coords=("f", _QUAD_TEX_COORDS),
        )

    def clear(self, color: tuple[float, float, float] = (0.0, 0.0, 0.0)) -> None:
        red, green, blue = color
        self.bind()
        pyglet.gl.glClearColor(red, green, blue, 1.0)
        pyglet.gl.glClear(pyglet.gl.GL_COLOR_BUFFER_BIT)
        self.unbind()

    @contextmanager
    def as_target(self) -> Iterator["Framebuffer"]:
        """Bind this buffer and declare its render space, for the duration.

        The space is DECLARED from this buffer's own size, never inherited:
        the present pass leaves framebuffer 0 bound and a window-sized
        viewport behind it, so a compose cannot trust what is current. The
        projection is a window-level uniform in pyglet, which is the only
        reason this holds a surface.

        The ortho is deliberately uninverted -- see `display.md`, *One space,
        and therefore one flip*, before changing it.
        """
        self.bind()
        self._surface.projection = Mat4.orthogonal_projection(
            0, self.width, 0, self.height, -1, 1
        )
        pyglet.gl.glViewport(0, 0, self.width, self.height)
        try:
            yield self
        finally:
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
        if viewport != self._uniform_viewport:
            self._uniform_viewport = viewport
            self._program["scaleFactor"] = float(viewport.scale)
            self._program["viewportOriginY"] = float(viewport.y)
        self._quad.draw(pyglet.gl.GL_TRIANGLES)
        self._program.stop()


class FrameHandoff:
    """The seam between a free-running engine and a display on its own grid.

    The engine composes into `pending` and calls `complete()`; display calls
    `promote()` and draws. That is the whole contract, and neither side asks
    the other for permission.

    `ready`, not the buffer, is what a present asks about -- so drop and
    repeat are properties of the flag and need no second buffer.

    **One buffer holds only while present strictly precedes compose, on one
    thread, in one GL context.** That ordering is what keeps the compose's
    write behind the present's read. If compose moves off this thread, or the
    loop ever composes before presenting within an iteration, the buffer has
    to double; nothing else here changes if it does.

    The measurements behind that, and what the counters below actually count:
    `bps/reference/engine/display.md`.
    """

    def __init__(self, width: int, height: int, surface: "Surface") -> None:
        self._buffer = Framebuffer(width, height, surface)
        # Presentation starts before the first tick completes, so something
        # IS shown before anything is composed: clear, or show driver litter.
        self._buffer.clear()
        self._ready = False
        self._primed = False
        self._dropped = 0
        self._repeated = 0

    @property
    def pending(self) -> Framebuffer:
        """The buffer the engine composes into."""
        return self._buffer

    @property
    def dropped(self) -> int:
        """Frames composed over before being shown.

        Reachable only while backgrounded, where the loop keeps composing and
        stops presenting. Zero on every ordinary run -- see `display.md`,
        *What the counters count*, before reading anything into it.
        """
        return self._dropped

    @property
    def repeated(self) -> int:
        """Presents with no new frame ready.

        Reachable only after a tick that returned early on a closed display.
        Zero on every ordinary run -- see `display.md`, *What the counters
        count*.
        """
        return self._repeated

    def complete(self) -> bool:
        """Engine: the frame in `pending` is finished. True if one was dropped."""
        dropped = self._ready
        if dropped:
            self._dropped += 1
        self._ready = True
        self._primed = True
        return dropped

    def promote(self) -> bool:
        """Display: take the standing frame. True if the picture moved."""
        if not self._ready:
            if self._primed:
                self._repeated += 1
            return False
        self._ready = False
        return True

    def draw(self, surface: "Surface") -> None:
        """Queue the post-process draw of the frame currently being shown."""
        self._buffer.draw(surface)
